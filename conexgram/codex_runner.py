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
from typing import Optional

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
    ) -> CodexTurnResult:
        working_dir = Path(session.working_dir).expanduser().resolve()
        ensure_dir(self.logs_dir / session.id)
        stamp = now_iso().replace(":", "").replace("+", "Z")
        raw_log_path = self.logs_dir / session.id / f"turn-{stamp}.jsonl"
        final_message_path = self.logs_dir / session.id / f"turn-{stamp}.final.txt"

        prompt = self._build_prompt(session, user_text)
        command = self._build_command(session, final_message_path)
        LOG.info("Running Codex: %s", " ".join(command))

        env = os.environ.copy()
        profile_home = profile_home or Path.home()
        command_env = self._build_environment(profile_home, env)

        process = subprocess.Popen(
            command,
            cwd=str(working_dir),
            env=command_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with self._lock:
            self._processes[session.id] = process

        thread_id: Optional[str] = session.codex_thread_id
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
            with self._lock:
                if self._processes.get(session.id) is process:
                    self._processes.pop(session.id, None)

        raw_log_path.write_text("".join(raw_lines), encoding="utf-8")

        final_text = ""
        if final_message_path.exists():
            final_text = final_message_path.read_text(encoding="utf-8").strip()
        if not final_text and agent_messages:
            final_text = agent_messages[-1]
        if not final_text:
            final_text = self._fallback_text(raw_lines, return_code)
        if timed_out:
            final_text = (
                f"Codex exceeded max_turn_seconds={self.config.max_turn_seconds} "
                "and was stopped.\n\n"
                + final_text
            )

        return CodexTurnResult(
            text=final_text,
            thread_id=thread_id,
            return_code=return_code,
            raw_log_path=raw_log_path,
            final_message_path=final_message_path,
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

    def _build_command(self, session: Session, final_message_path: Path) -> list[str]:
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
        if session.model:
            command.extend(["--model", session.model])
        if session.reasoning_effort:
            command.extend(["-c", f'model_reasoning_effort="{session.reasoning_effort}"'])
        command.extend(["--output-last-message", str(final_message_path)])
        if session.codex_thread_id:
            command.append(session.codex_thread_id)
        command.append("-")
        return command

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

    def _build_prompt(self, session: Session, user_text: str) -> str:
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
