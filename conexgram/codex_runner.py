"""Run Codex CLI turns and parse JSONL events."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import CodexConfig
from .paths import ensure_dir
from .session_store import Session, now_iso

LOG = logging.getLogger(__name__)


@dataclass
class CodexTurnResult:
    text: str
    thread_id: Optional[str]
    return_code: int
    raw_log_path: Path
    final_message_path: Path


@dataclass
class _CodexAttempt:
    thread_id: Optional[str]
    return_code: int
    raw_lines: list[str]
    agent_messages: list[str]
    timed_out: bool = False
    model: Optional[str] = None


class CodexRunner:
    def __init__(
        self,
        config: CodexConfig,
        logs_dir: Path,
        max_log_days: int = 14,
        max_log_mb: int = 100,
    ) -> None:
        self.config = config
        self.logs_dir = ensure_dir(logs_dir)
        self.max_log_days = max_log_days
        self.max_log_bytes = max_log_mb * 1024 * 1024
        self._lock = threading.Lock()
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._cleanup_logs()

    def run_turn(
        self,
        session: Session,
        user_text: str,
        profile_home: Optional[Path] = None,
        event_callback: Optional[Callable[[dict], None]] = None,
        prompt_mode: str = "telegram",
    ) -> CodexTurnResult:
        working_dir = Path(session.working_dir).expanduser().resolve()
        ensure_dir(self.logs_dir / session.id)
        stamp = now_iso().replace(":", "").replace("+", "Z")
        raw_log_path = self.logs_dir / session.id / f"turn-{stamp}.jsonl"
        final_message_path = self.logs_dir / session.id / f"turn-{stamp}.final.txt"

        prompt = self._build_prompt(session, user_text, prompt_mode=prompt_mode)
        command = self._build_command(session, final_message_path)
        LOG.info("Running Codex: %s", " ".join(command))

        env = os.environ.copy()
        profile_home = profile_home or Path.home()
        command_env = self._build_environment(profile_home, env)

        attempted_model = self._resolve_model_alias(session.model)
        attempt = self._run_command(
            session_id=session.id,
            command=command,
            prompt=prompt,
            working_dir=working_dir,
            env=command_env,
            initial_thread_id=session.codex_thread_id,
            event_callback=event_callback,
            model=attempted_model,
        )
        raw_lines = list(attempt.raw_lines)
        final_return_code = attempt.return_code
        thread_id = attempt.thread_id

        fallback_model = self._fallback_model()
        fallback_attempt: Optional[_CodexAttempt] = None
        if self._should_try_fallback(attempt, fallback_model):
            LOG.info(
                "Codex quota/rate-limit detected for model %s; retrying with %s",
                attempted_model or "default",
                fallback_model,
            )
            fallback_event = {
                "type": "conexgram.fallback",
                "reason": "quota_or_rate_limit",
                "from_model": attempted_model or "default",
                "to_model": fallback_model,
            }
            raw_lines.append(json.dumps(fallback_event) + "\n")
            if final_message_path.exists():
                final_message_path.unlink()
            fallback_command = self._build_command(
                session,
                final_message_path,
                override_model=fallback_model,
            )
            fallback_attempt = self._run_command(
                session_id=session.id,
                command=fallback_command,
                prompt=prompt,
                working_dir=working_dir,
                env=command_env,
                initial_thread_id=thread_id or session.codex_thread_id,
                event_callback=event_callback,
                model=fallback_model,
            )
            raw_lines.extend(fallback_attempt.raw_lines)
            final_return_code = fallback_attempt.return_code
            thread_id = fallback_attempt.thread_id
            if fallback_attempt.return_code == 0:
                session.model = fallback_model

        raw_log_path.write_text("".join(raw_lines), encoding="utf-8")

        final_text = self._final_text(
            final_message_path=final_message_path,
            agent_messages=(
                fallback_attempt.agent_messages
                if fallback_attempt is not None
                else attempt.agent_messages
            ),
            raw_lines=raw_lines,
            return_code=final_return_code,
        )

        graceful_limit_message = self._limit_message_if_needed(
            attempt=attempt,
            fallback_attempt=fallback_attempt,
            fallback_model=fallback_model,
        )
        if graceful_limit_message:
            final_text = graceful_limit_message
            final_return_code = 0

        timed_out = fallback_attempt.timed_out if fallback_attempt is not None else attempt.timed_out
        if timed_out:
            final_text = (
                f"Codex exceeded max_turn_seconds={self.config.max_turn_seconds} "
                "and was stopped.\n\n"
                + final_text
            )

        return CodexTurnResult(
            text=final_text,
            thread_id=thread_id,
            return_code=final_return_code,
            raw_log_path=raw_log_path,
            final_message_path=final_message_path,
        )

    def _run_command(
        self,
        session_id: str,
        command: list[str],
        prompt: str,
        working_dir: Path,
        env: dict[str, str],
        initial_thread_id: Optional[str],
        event_callback: Optional[Callable[[dict], None]],
        model: Optional[str],
    ) -> _CodexAttempt:
        process = subprocess.Popen(
            command,
            cwd=str(working_dir),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self._lock:
            self._processes[session_id] = process

        thread_id: Optional[str] = initial_thread_id
        agent_messages: list[str] = []
        raw_lines: list[str] = []

        timed_out = False

        def terminate_on_timeout() -> None:
            nonlocal timed_out
            timed_out = True
            process.terminate()

        timer = threading.Timer(self.config.max_turn_seconds, terminate_on_timeout)
        timer.start()
        try:
            assert process.stdin is not None
            process.stdin.write(prompt)
            process.stdin.close()
            assert process.stdout is not None
            for line in process.stdout:
                raw_lines.append(line)
                event = self._parse_event(line)
                if event is None:
                    continue
                if event_callback is not None:
                    try:
                        event_callback(event)
                    except Exception:
                        LOG.exception("Codex event callback failed")
                if event.get("type") == "thread.started":
                    thread_id = str(event.get("thread_id") or thread_id or "")
                item = event.get("item")
                if event.get("type") == "item.completed" and isinstance(item, dict):
                    if item.get("type") == "agent_message":
                        text = item.get("text")
                        if isinstance(text, str) and text.strip():
                            agent_messages.append(text.strip())
            return_code = process.wait()
        finally:
            timer.cancel()
            if process.stdout is not None:
                process.stdout.close()
            with self._lock:
                if self._processes.get(session_id) is process:
                    self._processes.pop(session_id, None)

        return _CodexAttempt(
            thread_id=thread_id,
            return_code=return_code,
            raw_lines=raw_lines,
            agent_messages=agent_messages,
            timed_out=timed_out,
            model=model,
        )

    def stop_session(self, session_id: str) -> bool:
        with self._lock:
            process = self._processes.get(session_id)
        if process is None or process.poll() is not None:
            return False
        process.terminate()
        return True

    def stop_current(self) -> bool:
        with self._lock:
            items = list(self._processes.values())
        for process in items:
            if process.poll() is None:
                process.terminate()
                return True
        return False

    def _build_command(
        self,
        session: Session,
        final_message_path: Path,
        override_model: Optional[str] = None,
    ) -> list[str]:
        command = [self.config.binary]
        if session.codex_thread_id:
            command.extend(["exec", "resume"])
        else:
            command.append("exec")

        command.append("--json")
        if self.config.skip_git_repo_check:
            command.append("--skip-git-repo-check")
        if self._should_use_full_access(session):
            command.append("--dangerously-bypass-approvals-and-sandbox")
        if not session.codex_thread_id:
            command.extend(["--cd", session.working_dir])
            for item in self.config.additional_writable_dirs:
                command.extend(["--add-dir", str(item)])
        resolved_model = self._resolve_model_alias(override_model or session.model)
        if resolved_model:
            command.extend(["--model", resolved_model])
        if session.reasoning_effort:
            command.extend(["-c", f'model_reasoning_effort="{session.reasoning_effort}"'])
        command.extend(["--output-last-message", str(final_message_path)])
        if session.codex_thread_id:
            command.append(session.codex_thread_id)
        command.append("-")
        return command

    def _resolve_model_alias(self, model: Optional[str]) -> Optional[str]:
        if not model:
            return None
        model = model.strip()
        if not model:
            return None
        preset = self.config.model_presets.get(model)
        if preset is not None:
            return preset
        preset = self.config.model_presets.get(model.lower())
        return preset if preset is not None else model

    def _fallback_model(self) -> str:
        return (
            self._resolve_model_alias(self.config.model_presets.get("fast"))
            or self._resolve_model_alias("spark")
            or "gpt-5.3-codex-spark"
        )

    def _should_try_fallback(self, attempt: _CodexAttempt, fallback_model: str) -> bool:
        if attempt.return_code == 0 or attempt.timed_out:
            return False
        if not self._is_quota_or_rate_limit(attempt.raw_lines):
            return False
        return (attempt.model or "").strip().lower() != fallback_model.strip().lower()

    def _limit_message_if_needed(
        self,
        attempt: _CodexAttempt,
        fallback_attempt: Optional[_CodexAttempt],
        fallback_model: str,
    ) -> str:
        if fallback_attempt is None:
            if attempt.return_code != 0 and self._is_model_unavailable(attempt.raw_lines):
                model = attempt.model or "the selected model"
                return (
                    f"{model} is not available for this Codex account right now. "
                    "Please switch to an available model or another authenticated profile."
                )
            return ""
        if fallback_attempt.return_code == 0:
            return ""
        if self._is_model_unavailable(fallback_attempt.raw_lines):
            return (
                "Your Codex quota for the active model appears to be exhausted or temporarily "
                f"rate-limited. I tried {fallback_model} as a fallback, but this account cannot "
                "use that model right now. Please wait for the quota reset or switch to another "
                "authenticated profile."
            )
        if self._is_quota_or_rate_limit(fallback_attempt.raw_lines):
            return (
                "Your Codex quota for the active model appears to be exhausted or temporarily "
                f"rate-limited. I also tried {fallback_model}, but that quota is unavailable too. "
                "Please wait until the quota resets, switch to another authenticated profile, "
                "or add credits before retrying."
            )
        return (
            "Your Codex quota for the active model appears to be exhausted or temporarily "
            f"rate-limited. I tried {fallback_model} as a fallback, but the fallback turn did not "
            "complete. Please retry after the quota reset or switch to another authenticated profile."
        )

    @staticmethod
    def _is_quota_or_rate_limit(raw_lines: list[str]) -> bool:
        text = "\n".join(raw_lines).lower()
        needles = (
            "rate limit",
            "rate_limit",
            "limit reached",
            "quota",
            "usage limit",
            "usage cap",
            "too many requests",
            "429",
            "insufficient_quota",
            "no credits",
            "credit balance",
        )
        return any(needle in text for needle in needles)

    @staticmethod
    def _is_model_unavailable(raw_lines: list[str]) -> bool:
        text = "\n".join(raw_lines).lower()
        needles = (
            "model is not supported",
            "model_not_found",
            "unsupported model",
            "not supported when using codex",
            "cannot use that model",
            "not available for this account",
        )
        return any(needle in text for needle in needles)

    @staticmethod
    def _final_text(
        final_message_path: Path,
        agent_messages: list[str],
        raw_lines: list[str],
        return_code: int,
    ) -> str:
        final_text = ""
        if final_message_path.exists():
            final_text = final_message_path.read_text(encoding="utf-8").strip()
        if not final_text and agent_messages:
            final_text = agent_messages[-1]
        if not final_text:
            final_text = CodexRunner._fallback_text(raw_lines, return_code)
        return final_text

    def _build_environment(
        self,
        profile_home: Path,
        base_env: dict[str, str],
    ) -> dict[str, str]:
        profile = profile_home
        ensure_dir(profile)
        env = dict(base_env)
        env["HOME"] = str(profile)
        env["XDG_CONFIG_HOME"] = str(Path(profile) / ".config")
        env["XDG_STATE_HOME"] = str(Path(profile) / ".local" / "state")
        env["XDG_CACHE_HOME"] = str(Path(profile) / ".cache")
        ensure_dir(Path(profile) / ".config")
        ensure_dir(Path(profile) / ".local" / "state")
        ensure_dir(Path(profile) / ".cache")
        return env

    def _build_prompt(self, session: Session, user_text: str, prompt_mode: str = "telegram") -> str:
        if prompt_mode == "terminal":
            return self._terminal_prompt(user_text)

        tool_prompt = self._gateway_tool_prompt()
        if session.codex_thread_id:
            return f"{tool_prompt}\n\nUser message:\n{user_text.strip()}\n"
        parts = []
        if self.config.base_prompt:
            parts.append(self.config.base_prompt)
        parts.append(tool_prompt)
        parts.append(
            "Runtime preferences:\n"
            f"- Mode: {session.mode}\n"
            f"- Fast mode: {'on' if session.fast_mode else 'off'}\n"
            f"- Reasoning effort: {session.reasoning_effort or 'Codex default'}\n"
            "- If fast mode is on, keep responses concise and avoid extra exploration unless needed.\n"
        )
        parts.append(
            "Session rules:\n"
            "- This is a private Telegram-controlled Codex CLI session.\n"
            "- Keep context for this session until the user starts a new session.\n"
            "- When you run commands, report verified results clearly.\n"
            "- If a task is blocked, state the exact blocker and next action.\n"
        )
        parts.append("User message:\n" + user_text.strip())
        return "\n\n".join(parts) + "\n"

    @staticmethod
    def _terminal_prompt(user_text: str) -> str:
        return "User message:\n" + user_text.strip() + "\n"

    @staticmethod
    def _gateway_tool_prompt() -> str:
        return (
            "Conexgram gateway tool protocol:\n"
            "- If the user asks you to send, attach, upload, or deliver a local file to Telegram, "
            "create or locate the file, then include these directive lines in your final answer:\n"
            "  CONEXGRAM_SEND_FILE: /absolute/path/to/file\n"
            "  CONEXGRAM_SEND_FILE_CAPTION: optional caption\n"
            "- The gateway will validate the path and send the file as a Telegram attachment.\n"
            "- Do not say you cannot attach files just because Codex CLI lacks a native upload tool."
        )

    def _should_use_full_access(self, session: Session) -> bool:
        if session.mode == "full":
            return self.config.full_access or bool(session.full_access)
        if session.full_access is not None:
            return bool(session.full_access)
        return self.config.full_access

    @staticmethod
    def _parse_event(line: str) -> Optional[dict]:
        line = line.strip()
        if not line.startswith("{"):
            return None
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        return event if isinstance(event, dict) else None

    @staticmethod
    def _fallback_text(raw_lines: list[str], return_code: int) -> str:
        tail = "".join(raw_lines[-20:]).strip()
        if tail:
            return f"Codex exited with code {return_code}.\n\n{tail}"
        return f"Codex exited with code {return_code} and produced no text output."

    def _cleanup_logs(self) -> None:
        cutoff = time.time() - self.max_log_days * 86400
        files = [path for path in self.logs_dir.rglob("*") if path.is_file()]
        for path in files:
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
        files = sorted(
            [path for path in self.logs_dir.rglob("*") if path.is_file()],
            key=lambda item: item.stat().st_mtime,
        )
        total = sum(path.stat().st_size for path in files)
        for path in files:
            if total <= self.max_log_bytes:
                break
            try:
                size = path.stat().st_size
                path.unlink()
                total -= size
            except OSError:
                continue
