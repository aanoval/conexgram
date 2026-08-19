"""Run Codex CLI turns and parse JSONL events."""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .config import CodexConfig
from .chatgpt_ipc import (
    ChatGPTIPCClient,
    ChatGPTIPCError,
    ChatGPTIPCRequestError,
    ChatGPTIPCUnavailable,
    ChatGPTIPCTurn,
)
from .codex_transcript import CodexTranscriptReader
from .paths import ensure_dir, workspace_access_error
from .session_store import Session, now_iso

LOG = logging.getLogger(__name__)


@dataclass
class CodexTurnResult:
    text: str
    thread_id: Optional[str]
    return_code: int
    raw_log_path: Path
    final_message_path: Path
    source: str = "codex-exec"


@dataclass
class _CodexAttempt:
    thread_id: Optional[str]
    return_code: int
    raw_lines: list[str]
    agent_messages: list[str]
    timed_out: bool = False
    startup_timed_out: bool = False
    idle_timed_out: bool = False
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
        self._ipc_turns: dict[str, tuple[ChatGPTIPCClient, ChatGPTIPCTurn]] = {}
        self._cleanup_logs()

    def run_turn(
        self,
        session: Session,
        user_text: str,
        profile_home: Optional[Path] = None,
        event_callback: Optional[Callable[[dict], None]] = None,
        prompt_mode: str = "telegram",
    ) -> CodexTurnResult:
        working_dir = Path(session.working_dir).expanduser()
        ensure_dir(self.logs_dir / session.id)
        stamp = now_iso().replace(":", "").replace("+", "Z")
        raw_log_path = self.logs_dir / session.id / f"turn-{stamp}.jsonl"
        final_message_path = self.logs_dir / session.id / f"turn-{stamp}.final.txt"
        access_error = workspace_access_error(
            working_dir,
            timeout_seconds=self.config.workspace_preflight_timeout_seconds,
        )
        if access_error:
            raw_log_path.write_text(
                json.dumps({"type": "conexgram.workspace_error", "message": access_error}) + "\n",
                encoding="utf-8",
            )
            return CodexTurnResult(
                text=(
                    f"Codex could not open this workspace. {access_error}\n\n"
                    "Choose another workspace or grant the background runtime access to this folder."
                ),
                thread_id=session.codex_thread_id,
                return_code=1,
                raw_log_path=raw_log_path,
                final_message_path=final_message_path,
            )
        working_dir = working_dir.resolve()
        raw_log_path.unlink(missing_ok=True)

        prompt = self._build_prompt(session, user_text, prompt_mode=prompt_mode)
        profile_home = profile_home or Path.home()
        if prompt_mode == "telegram" and self._should_use_chatgpt_ipc(session, profile_home):
            try:
                return self._run_chatgpt_ipc_turn(
                    session=session,
                    prompt=self._build_prompt(session, user_text, prompt_mode="chatgpt-ipc"),
                    profile_home=profile_home,
                    event_callback=event_callback,
                    raw_log_path=raw_log_path,
                    final_message_path=final_message_path,
                )
            except ChatGPTIPCUnavailable as exc:
                # A thread is only owned by the Desktop app while that app has
                # the local thread open.  Preserve the existing CLI path when
                # the app is closed or the selected thread is not active there.
                LOG.info("ChatGPT IPC unavailable; falling back to Codex CLI: %s", exc)
            except ChatGPTIPCError:
                # A rejected turn is not safe to duplicate through a second
                # runner: the Desktop app may already have accepted it.
                raise

        command = self._build_command(session, final_message_path)
        LOG.info("Running Codex: %s", " ".join(command))

        env = os.environ.copy()
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
            timeout_enabled=self._timeout_enabled(session),
            raw_log_path=raw_log_path,
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
            with raw_log_path.open("a", encoding="utf-8") as raw_log:
                raw_log.write(json.dumps(fallback_event) + "\n")
                raw_log.flush()
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
                timeout_enabled=self._timeout_enabled(session),
                raw_log_path=raw_log_path,
            )
            raw_lines.extend(fallback_attempt.raw_lines)
            final_return_code = fallback_attempt.return_code
            thread_id = fallback_attempt.thread_id
            if fallback_attempt.return_code == 0:
                session.model = fallback_model

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
        watchdog_attempt = fallback_attempt if fallback_attempt is not None else attempt
        if watchdog_attempt.startup_timed_out:
            final_text = (
                f"Codex produced no startup event within "
                f"{self.config.startup_timeout_seconds} seconds and was stopped. "
                "The workspace may be blocked or inaccessible to the background runtime."
            )
        elif watchdog_attempt.idle_timed_out:
            final_text = (
                f"Codex produced no progress event for "
                f"{self.config.idle_timeout_seconds} seconds and was stopped."
            )

        return CodexTurnResult(
            text=final_text,
            thread_id=thread_id,
            return_code=final_return_code,
            raw_log_path=raw_log_path,
            final_message_path=final_message_path,
        )

    def _should_use_chatgpt_ipc(self, session: Session, profile_home: Path) -> bool:
        """Use ChatGPT Desktop ownership only for existing local-profile threads."""
        if not self.config.chatgpt_ipc_enabled or not session.codex_thread_id:
            return False
        try:
            return profile_home.expanduser().resolve() == Path.home().resolve()
        except OSError:
            return False

    def _run_chatgpt_ipc_turn(
        self,
        session: Session,
        prompt: str,
        profile_home: Path,
        event_callback: Optional[Callable[[dict], None]],
        raw_log_path: Path,
        final_message_path: Path,
    ) -> CodexTurnResult:
        socket_path = self.config.chatgpt_ipc_socket
        seen_request_ids: set[str] = set()

        def on_broadcast(event: dict) -> None:
            self._record_ipc_event(raw_log_path, event, event_callback)
            self._emit_ipc_interaction_events(
                event,
                session.codex_thread_id or "",
                seen_request_ids,
                raw_log_path,
                event_callback,
            )

        client = ChatGPTIPCClient(
            socket_path=socket_path,
            host_id=self.config.chatgpt_host_id,
            client_type=self.config.chatgpt_ipc_client_type,
            timeout_seconds=self.config.chatgpt_ipc_timeout_seconds,
            broadcast_callback=on_broadcast,
        )
        reader = CodexTranscriptReader(
            profile_home,
            stale_after_seconds=max(30.0, float(self.config.idle_timeout_seconds)),
        )
        before = reader.snapshot(session.codex_thread_id or "")
        before_mtime = before.source_mtime
        turn_params = self._build_chatgpt_turn_params(session, prompt)
        turn = client.start_turn(session.codex_thread_id or "", turn_params)
        with self._lock:
            self._ipc_turns[session.id] = (client, turn)

        started_event = {
            "type": "thread.started",
            "thread_id": session.codex_thread_id,
            "source": "chatgpt-ipc",
            "owner_client_id": turn.owner_client_id,
        }
        self._record_ipc_event(raw_log_path, started_event, event_callback)

        started_at = time.monotonic()
        last_activity = started_at
        last_signature = (before.source_mtime, before.content, before.processing)
        last_content = before.content
        saw_new_output = False
        try:
            while True:
                client.poll()
                snapshot = reader.snapshot(session.codex_thread_id or "")
                signature = (snapshot.source_mtime, snapshot.content, snapshot.processing)
                if signature != last_signature:
                    last_signature = signature
                    last_activity = time.monotonic()
                    if snapshot.source_mtime > before_mtime or snapshot.content != before.content:
                        saw_new_output = True

                if snapshot.processing:
                    saw_new_output = saw_new_output or snapshot.source_mtime > before_mtime
                if snapshot.content and snapshot.content != last_content:
                    last_content = snapshot.content
                    self._record_ipc_event(
                        raw_log_path,
                        {
                            "type": "item.updated",
                            "item": {
                                "type": "agent_message",
                                "text": snapshot.content,
                            },
                            "source": "chatgpt-ipc-rollout",
                        },
                        event_callback,
                    )

                if snapshot.complete and saw_new_output and not snapshot.processing:
                    final_text = snapshot.content.strip() or "Codex finished without a final response."
                    final_message_path.write_text(final_text + "\n", encoding="utf-8")
                    self._record_ipc_event(
                        raw_log_path,
                        {
                            "type": "turn.completed",
                            "thread_id": session.codex_thread_id,
                            "status": "completed",
                            "source": "chatgpt-ipc-rollout",
                        },
                        event_callback,
                    )
                    return CodexTurnResult(
                        text=final_text,
                        thread_id=session.codex_thread_id,
                        return_code=0,
                        raw_log_path=raw_log_path,
                        final_message_path=final_message_path,
                        source="chatgpt-ipc",
                    )

                elapsed = time.monotonic() - started_at
                if self._timeout_enabled(session) and elapsed >= self.config.max_turn_seconds:
                    self._interrupt_ipc_turn(session.id, client, turn)
                    message = (
                        f"Codex exceeded max_turn_seconds={self.config.max_turn_seconds} "
                        "and was stopped."
                    )
                    final_message_path.write_text(message + "\n", encoding="utf-8")
                    return CodexTurnResult(
                        text=message,
                        thread_id=session.codex_thread_id,
                        return_code=1,
                        raw_log_path=raw_log_path,
                        final_message_path=final_message_path,
                        source="chatgpt-ipc",
                    )
                if not saw_new_output and elapsed >= self.config.startup_timeout_seconds:
                    self._interrupt_ipc_turn(session.id, client, turn)
                    message = (
                        "Codex produced no startup event within "
                        f"{self.config.startup_timeout_seconds} seconds and was stopped."
                    )
                    final_message_path.write_text(message + "\n", encoding="utf-8")
                    return CodexTurnResult(
                        text=message,
                        thread_id=session.codex_thread_id,
                        return_code=1,
                        raw_log_path=raw_log_path,
                        final_message_path=final_message_path,
                        source="chatgpt-ipc",
                    )
                if saw_new_output and self._timeout_enabled(session):
                    idle_for = time.monotonic() - last_activity
                    if idle_for >= self.config.idle_timeout_seconds:
                        self._interrupt_ipc_turn(session.id, client, turn)
                        message = (
                            f"Codex produced no progress event for "
                            f"{self.config.idle_timeout_seconds} seconds and was stopped."
                        )
                        final_message_path.write_text(message + "\n", encoding="utf-8")
                        return CodexTurnResult(
                            text=message,
                            thread_id=session.codex_thread_id,
                            return_code=1,
                            raw_log_path=raw_log_path,
                            final_message_path=final_message_path,
                            source="chatgpt-ipc",
                        )
                time.sleep(0.35)
        finally:
            with self._lock:
                if self._ipc_turns.get(session.id, (None, None))[0] is client:
                    self._ipc_turns.pop(session.id, None)
            client.close()

    @staticmethod
    def _record_ipc_event(
        raw_log_path: Path,
        event: dict,
        event_callback: Optional[Callable[[dict], None]],
    ) -> None:
        with raw_log_path.open("a", encoding="utf-8") as raw_log:
            raw_log.write(json.dumps(event, ensure_ascii=False) + "\n")
        if event_callback is not None:
            try:
                event_callback(event)
            except Exception:
                LOG.exception("Codex IPC event callback failed")

    def _emit_ipc_interaction_events(
        self,
        event: dict,
        conversation_id: str,
        seen_request_ids: set[str],
        raw_log_path: Path,
        event_callback: Optional[Callable[[dict], None]],
    ) -> None:
        """Expose owner-side approval/input requests to the Telegram layer."""
        if event.get("method") != "thread-stream-state-changed":
            return
        params = event.get("params")
        if not isinstance(params, dict):
            return
        requests = _find_request_collection(params)
        if requests is None:
            return
        for request in requests:
            if not isinstance(request, dict):
                continue
            request_id = _request_id(request)
            if not request_id or request_id in seen_request_ids:
                continue
            seen_request_ids.add(request_id)
            interaction = {
                "type": "conexgram.interaction.requested",
                "thread_id": conversation_id,
                "request": request,
                "source": "chatgpt-ipc",
            }
            self._record_ipc_event(raw_log_path, interaction, event_callback)

    def submit_ipc_interaction(
        self,
        session_id: str,
        request: dict,
        user_text: str,
    ) -> None:
        """Submit a Telegram answer to a pending Desktop approval/input request."""
        with self._lock:
            ipc_turn = self._ipc_turns.get(session_id)
        if ipc_turn is None:
            raise ChatGPTIPCUnavailable("No active ChatGPT IPC turn for this session")
        client, turn = ipc_turn
        method, params = self._build_ipc_interaction(request, turn.conversation_id, user_text)
        client.send_follower_request(method, params, turn.owner_client_id)

    def steer_ipc_turn(
        self,
        session_id: str,
        conversation_id: str,
        user_text: str,
        working_dir: Optional[str] = None,
    ) -> bool:
        """Steer an active ChatGPT turn, including one started in Desktop."""
        cwd = str(
            Path(working_dir or self.config.default_working_dir)
            .expanduser()
            .resolve()
        )
        with self._lock:
            active_turn = self._ipc_turns.get(session_id)
        if active_turn is not None:
            client, turn = active_turn
            client.steer_turn(
                turn.conversation_id,
                turn.owner_client_id,
                user_text,
                cwd,
            )
            return True

        # A selected thread may already be running in ChatGPT Desktop before
        # Conexgram receives the Telegram message.  Use a short-lived follower
        # connection for that case; the existing transcript watcher mirrors the
        # result back to Telegram.
        client = ChatGPTIPCClient(
            socket_path=self.config.chatgpt_ipc_socket,
            host_id=self.config.chatgpt_host_id,
            client_type=self.config.chatgpt_ipc_client_type,
            timeout_seconds=self.config.chatgpt_ipc_timeout_seconds,
        )
        try:
            try:
                owner = client.discover_thread_owner(conversation_id)
            except ChatGPTIPCUnavailable:
                return False
            try:
                client.steer_turn(conversation_id, owner, user_text, cwd)
            except ChatGPTIPCRequestError as exc:
                if _is_idle_thread_error(exc):
                    return False
                raise
            return True
        finally:
            client.close()

    @staticmethod
    def _build_ipc_interaction(
        request: dict,
        conversation_id: str,
        user_text: str,
    ) -> tuple[str, dict]:
        request_params = request.get("params")
        request_params = request_params if isinstance(request_params, dict) else {}
        request_id = _request_id(request)
        if not request_id:
            raise ChatGPTIPCError("ChatGPT interaction request has no request id")

        request_method = str(request.get("method") or "").lower()
        normalized = user_text.strip().lower()
        accept = {"yes", "y", "ok", "okay", "allow", "approve", "approved", "accept"}
        decline = {"no", "n", "deny", "denied", "reject", "rejected", "decline", "cancel"}
        decision: Optional[str] = None
        if normalized in accept:
            decision = "accept"
        elif normalized in decline:
            decision = "decline"

        if request_method == "item/commandexecution/requestapproval":
            if decision is None:
                raise ChatGPTIPCError("Reply yes/allow or no/deny for this command approval")
            return "thread-follower-command-approval-decision", {
                "conversationId": conversation_id,
                "requestId": request_id,
                "decision": decision,
            }
        if request_method == "item/filechange/requestapproval":
            if decision is None:
                raise ChatGPTIPCError("Reply yes/allow or no/deny for this file approval")
            return "thread-follower-file-approval-decision", {
                "conversationId": conversation_id,
                "requestId": request_id,
                "decision": decision,
            }
        if request_method == "item/permissions/requestapproval":
            if decision is None:
                raise ChatGPTIPCError("Reply yes/allow or no/deny for this permission request")
            requested_permissions = request_params.get("permissions")
            permissions = requested_permissions if decision == "accept" else {}
            return "thread-follower-permissions-request-approval-response", {
                "conversationId": conversation_id,
                "requestId": request_id,
                "response": {"permissions": permissions, "scope": "turn"},
            }
        if request_method == "mcpserver/elicitation/request":
            if normalized in decline:
                response = {"action": "decline"}
            else:
                response = {"action": "accept", "content": user_text}
            return "thread-follower-submit-mcp-server-elicitation-response", {
                "conversationId": conversation_id,
                "requestId": request_id,
                "response": response,
            }

        if request_method in {"item/tool/requestuserinput", "item/tool/call"}:
            questions = request_params.get("questions")
            answers: dict[str, dict[str, list[str]]] = {}
            if isinstance(questions, list):
                for question in questions:
                    if not isinstance(question, dict):
                        continue
                    question_id = question.get("id")
                    if isinstance(question_id, str) and question_id:
                        answers[question_id] = {"answers": [user_text]}
            if not answers:
                answers["response"] = {"answers": [user_text]}
            return "thread-follower-submit-user-input", {
                "conversationId": conversation_id,
                "requestId": request_id,
                "response": {"answers": answers},
            }

        raise ChatGPTIPCError(f"Unsupported ChatGPT interaction request: {request.get('method')}")

    def _build_chatgpt_turn_params(self, session: Session, prompt: str) -> dict:
        params: dict[str, object] = {
            "input": [{"type": "text", "text": prompt, "text_elements": []}],
            "cwd": str(Path(session.working_dir).expanduser().resolve()),
            "clientUserMessageId": str(uuid.uuid4()),
        }
        model = self._resolve_model_alias(session.model)
        if model:
            params["model"] = model
        if session.reasoning_effort:
            params["effort"] = session.reasoning_effort
        if session.approval_policy and not self._should_use_full_access(session):
            params["approvalPolicy"] = session.approval_policy
            params["approvalsReviewer"] = "user"
        if session.sandbox_mode == "read-only":
            params["sandboxPolicy"] = {"type": "readOnly"}
        elif session.sandbox_mode == "workspace-write":
            writable_roots = [str(Path(session.working_dir).expanduser().resolve())]
            writable_roots.extend(str(path.expanduser().resolve()) for path in self.config.additional_writable_dirs)
            params["sandboxPolicy"] = {
                "type": "workspaceWrite",
                "writableRoots": list(dict.fromkeys(writable_roots)),
            }
        elif self._should_use_full_access(session):
            params["sandboxPolicy"] = {"type": "dangerFullAccess"}
            params["approvalPolicy"] = "never"
            params["approvalsReviewer"] = "user"
        return params

    def _interrupt_ipc_turn(
        self,
        session_id: str,
        client: ChatGPTIPCClient,
        turn: ChatGPTIPCTurn,
    ) -> None:
        try:
            client.interrupt_turn(turn)
        except ChatGPTIPCError as exc:
            LOG.warning("Failed to interrupt ChatGPT IPC turn %s: %s", session_id, exc)

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
        timeout_enabled: bool = True,
        raw_log_path: Optional[Path] = None,
    ) -> _CodexAttempt:
        popen_kwargs: dict[str, object] = {}
        if os.name == "posix":
            popen_kwargs["start_new_session"] = True
        elif os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            command,
            cwd=str(working_dir),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            **popen_kwargs,
        )
        with self._lock:
            self._processes[session_id] = process

        thread_id: Optional[str] = initial_thread_id
        agent_messages: list[str] = []
        raw_lines: list[str] = []

        timed_out = False
        startup_timed_out = False
        idle_timed_out = False
        activity_lock = threading.Lock()
        started = False
        last_activity = time.monotonic()
        monitor_stop = threading.Event()

        def terminate_on_timeout() -> None:
            nonlocal timed_out
            timed_out = True
            self._terminate_process_group(process)

        def monitor_activity() -> None:
            nonlocal startup_timed_out, idle_timed_out
            while not monitor_stop.wait(1):
                if process.poll() is not None:
                    return
                with activity_lock:
                    has_started = started
                    idle_for = time.monotonic() - last_activity
                if not has_started and idle_for >= self.config.startup_timeout_seconds:
                    startup_timed_out = True
                    self._terminate_process_group(process)
                    return
                if timeout_enabled and has_started and idle_for >= self.config.idle_timeout_seconds:
                    idle_timed_out = True
                    self._terminate_process_group(process)
                    return

        timer: Optional[threading.Timer] = None
        if timeout_enabled:
            timer = threading.Timer(self.config.max_turn_seconds, terminate_on_timeout)
            timer.start()
        activity_monitor = threading.Thread(
            target=monitor_activity,
            name=f"codex-watchdog-{session_id[:8]}",
            daemon=True,
        )
        activity_monitor.start()
        try:
            assert process.stdin is not None
            process.stdin.write(prompt)
            process.stdin.close()
            assert process.stdout is not None
            log_context = (
                raw_log_path.open("a", encoding="utf-8")
                if raw_log_path is not None
                else open(os.devnull, "w", encoding="utf-8")
            )
            with log_context as raw_log:
                for line in process.stdout:
                    raw_lines.append(line)
                    raw_log.write(line)
                    raw_log.flush()
                    event = self._parse_event(line)
                    if event is None:
                        continue
                    with activity_lock:
                        started = True
                        last_activity = time.monotonic()
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
            monitor_stop.set()
            if timer is not None:
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
            startup_timed_out=startup_timed_out,
            idle_timed_out=idle_timed_out,
            model=model,
        )

    def stop_session(self, session_id: str) -> bool:
        with self._lock:
            process = self._processes.get(session_id)
            ipc_turn = self._ipc_turns.get(session_id)
        if ipc_turn is not None:
            client, turn = ipc_turn
            try:
                client.interrupt_turn(turn)
            except ChatGPTIPCError as exc:
                LOG.warning("Failed to stop ChatGPT IPC turn %s: %s", session_id, exc)
            return True
        if process is None or process.poll() is not None:
            return False
        self._terminate_process_group(process)
        return True

    def stop_current(self) -> bool:
        with self._lock:
            items = list(self._processes.values())
            ipc_items = list(self._ipc_turns.values())
        for client, turn in ipc_items:
            try:
                client.interrupt_turn(turn)
            except ChatGPTIPCError as exc:
                LOG.warning("Failed to stop ChatGPT IPC turn: %s", exc)
            return True
        for process in items:
            if process.poll() is None:
                self._terminate_process_group(process)
                return True
        return False

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str], grace_seconds: float = 5.0) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "posix":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=grace_seconds)
        except ProcessLookupError:
            return
        except subprocess.TimeoutExpired:
            try:
                if os.name == "posix":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass

    @staticmethod
    def _timeout_enabled(session: Session) -> bool:
        return (session.reasoning_effort or "").strip().lower() != "ultra"

    def _build_command(
        self,
        session: Session,
        final_message_path: Path,
        override_model: Optional[str] = None,
    ) -> list[str]:
        command = [self.config.binary]
        if session.approval_policy and not self._should_use_full_access(session):
            command.extend(["-a", session.approval_policy])
        if session.codex_thread_id:
            command.extend(["exec", "resume"])
        else:
            command.append("exec")

        command.append("--json")
        if self.config.skip_git_repo_check:
            command.append("--skip-git-repo-check")
        if self._should_use_full_access(session):
            command.append("--dangerously-bypass-approvals-and-sandbox")
        elif session.sandbox_mode:
            command.extend(["--sandbox", session.sandbox_mode])
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
        if prompt_mode == "chatgpt-ipc":
            return user_text.strip() + "\n"
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


def _request_id(request: dict) -> Optional[str]:
    for key in ("requestId", "request_id", "id"):
        value = request.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _find_request_collection(value: object) -> Optional[list[dict]]:
    """Find the current app-server request list in a state snapshot."""
    if isinstance(value, dict):
        requests = value.get("requests")
        if isinstance(requests, list):
            return [item for item in requests if isinstance(item, dict)]
        for child in value.values():
            found = _find_request_collection(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_request_collection(child)
            if found is not None:
                return found
    return None


def _is_idle_thread_error(error: ChatGPTIPCRequestError) -> bool:
    message = str(error).lower()
    return any(
        phrase in message
        for phrase in (
            "not being streamed",
            "not streaming",
            "no active turn",
            "not in progress",
            "conversation is not being streamed",
        )
    )
