"""Live Telegram mirrors for Codex output."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .config import ProgressConfig
from .message_format import split_message
from .session_store import Session
from .telegram_api import TelegramApiError, TelegramClient

LOG = logging.getLogger(__name__)


class ProgressNotifier:
    """Keep one Telegram message synchronized with the latest Codex content."""

    PROCESSING_MARKER = "⏳ Processing…"

    def __init__(self, telegram: TelegramClient, config: ProgressConfig, max_chars: int = 3900) -> None:
        self.telegram = telegram
        self.config = config
        self.max_chars = max(100, max_chars)

    def start(
        self,
        session: Session,
        chat_id: int,
        reply_to_message_id: int,
        initial_message_ids: Optional[list[int]] = None,
        initial_content: str = "",
    ) -> "ProgressHandle":
        stop_event = threading.Event()
        handle = ProgressHandle(
            stop_event,
            message_ids=initial_message_ids,
            content=initial_content,
            live_updates=self._effective_bool(session.progress_messages, self.config.progress_messages),
        )
        thread = threading.Thread(
            target=self._run,
            args=(session, chat_id, reply_to_message_id, handle),
            name=f"progress-{chat_id}",
            daemon=True,
        )
        handle.thread = thread
        thread.start()
        return handle

    def render_once(
        self,
        handle: "ProgressHandle",
        chat_id: int,
        text: str,
        processing: bool = False,
    ) -> list[int]:
        handle.set_content(text, processing=processing)
        return self._render(handle, chat_id)

    def complete(
        self,
        handle: "ProgressHandle",
        chat_id: int,
        final_text: str = "",
        success: bool = True,
    ) -> list[int]:
        del success  # The final Codex text already contains the real result/error.
        text = final_text.strip() or handle.latest_content.strip()
        if not text:
            text = "Codex finished without a final response."
        handle.clear_interaction_notice()
        handle.set_content(text, processing=False)
        message_ids = self._render(handle, chat_id)
        handle.stop()
        return message_ids

    def _run(
        self,
        session: Session,
        chat_id: int,
        reply_to_message_id: int,
        handle: "ProgressHandle",
    ) -> None:
        typing_enabled = self._effective_bool(session.typing_indicator, self.config.typing_indicator)
        last_rendered: Optional[str] = None
        while not handle.stop_event.is_set():
            if typing_enabled:
                try:
                    self.telegram.send_chat_action(chat_id, "typing")
                except TelegramApiError as exc:
                    LOG.debug("Failed to send typing indicator: %s", exc)

            rendered = handle.render_text(self.PROCESSING_MARKER)
            if handle.live_updates and rendered != last_rendered:
                self._render(handle, chat_id, reply_to_message_id)
                last_rendered = rendered
            handle.stop_event.wait(min(max(0.25, self.config.typing_interval_seconds), 4.0))

    def _render(
        self,
        handle: "ProgressHandle",
        chat_id: int,
        reply_to_message_id: Optional[int] = None,
    ) -> list[int]:
        text = handle.render_text(self.PROCESSING_MARKER)
        if not text:
            return list(handle.message_ids)
        chunks = split_message(text, self.max_chars)
        existing = list(handle.message_ids)
        message_ids: list[int] = []
        replaced_ids: list[int] = []

        for index, chunk in enumerate(chunks):
            if index < len(existing):
                try:
                    self.telegram.edit_message_text(chat_id, existing[index], chunk)
                    message_ids.append(existing[index])
                    continue
                except TelegramApiError as exc:
                    if "message is not modified" in str(exc).lower():
                        message_ids.append(existing[index])
                        continue
                    LOG.debug("Failed to edit live mirror message: %s", exc)
                    replaced_ids.append(existing[index])
            try:
                new_id = self.telegram.send_message(
                    chat_id,
                    chunk,
                    reply_to_message_id=reply_to_message_id if index == 0 else None,
                )
            except TelegramApiError as exc:
                LOG.warning("Failed to send live mirror message: %s", exc)
                continue
            if new_id is not None:
                message_ids.append(new_id)

        for old_id in replaced_ids + existing[len(chunks):]:
            self._delete_message(chat_id, old_id)
        handle.set_message_ids(message_ids)
        return message_ids

    def _delete_message(self, chat_id: int, message_id: int) -> None:
        delete = getattr(self.telegram, "delete_message", None)
        if delete is None:
            return
        try:
            delete(chat_id, message_id)
        except TelegramApiError as exc:
            LOG.debug("Failed to delete stale live mirror message: %s", exc)

    @staticmethod
    def _effective_bool(value: Optional[bool], default: bool) -> bool:
        return default if value is None else bool(value)


class ProgressHandle:
    """Thread-safe content and Telegram message state for one live mirror."""

    def __init__(
        self,
        stop_event: threading.Event,
        message_ids: Optional[list[int]] = None,
        content: str = "",
        live_updates: bool = True,
    ) -> None:
        self.stop_event = stop_event
        self.started_at = time.monotonic()
        self.thread: Optional[threading.Thread] = None
        self._message_ids = list(message_ids or [])
        self._latest_content = content.strip()
        self._processing = True
        self._interaction_notice = ""
        self.live_updates = live_updates
        self._lock = threading.RLock()

    @property
    def message_ids(self) -> list[int]:
        with self._lock:
            return list(self._message_ids)

    @property
    def message_id(self) -> Optional[int]:
        with self._lock:
            return self._message_ids[0] if self._message_ids else None

    @message_id.setter
    def message_id(self, value: Optional[int]) -> None:
        with self._lock:
            self._message_ids = [] if value is None else [value]

    @property
    def latest_content(self) -> str:
        with self._lock:
            return self._latest_content

    @property
    def latest_status(self) -> str:
        # Kept as a compatibility alias for integrations that used the old API.
        return self.latest_content

    @property
    def processing(self) -> bool:
        with self._lock:
            return self._processing

    def set_message_ids(self, message_ids: list[int]) -> None:
        with self._lock:
            self._message_ids = list(message_ids)

    def set_content(self, text: str, processing: bool = True) -> None:
        with self._lock:
            self._latest_content = text.strip()
            self._processing = processing

    def set_interaction_notice(self, request: dict) -> None:
        method = str(request.get("method") or "ChatGPT interaction")
        params = request.get("params")
        params = params if isinstance(params, dict) else {}
        if method == "item/commandExecution/requestApproval":
            notice = "Approval needed for a command. Reply yes/allow or no/deny."
        elif method == "item/fileChange/requestApproval":
            notice = "Approval needed for a file change. Reply yes/allow or no/deny."
        elif method == "item/permissions/requestApproval":
            notice = "Permission request from ChatGPT. Reply yes/allow or no/deny."
        elif method == "mcpServer/elicitation/request":
            notice = "ChatGPT is asking for an external-tool response. Reply with your answer, or no/deny."
        else:
            question = params.get("question")
            notice = str(question).strip() if isinstance(question, str) and question.strip() else (
                "ChatGPT is asking for input. Reply with the answer."
            )
        with self._lock:
            self._interaction_notice = notice

    def clear_interaction_notice(self) -> None:
        with self._lock:
            self._interaction_notice = ""

    def render_text(self, processing_marker: str) -> str:
        with self._lock:
            parts: list[str] = []
            if self._latest_content:
                parts.append(self._latest_content)
            if self._interaction_notice:
                parts.append(f"⚠️ {self._interaction_notice}")
            if self._processing:
                parts.append(processing_marker)
            return "\n\n".join(parts)

    def update_from_event(self, event: dict) -> None:
        if event.get("type") == "conexgram.interaction.requested":
            request = event.get("request")
            if isinstance(request, dict):
                self.set_interaction_notice(request)
            return
        item = event.get("item")
        if not isinstance(item, dict):
            payload = event.get("payload")
            if isinstance(payload, dict):
                item = payload
        if not isinstance(item, dict):
            return
        if item.get("type") not in {"agent_message", "message"}:
            return
        text = item.get("text") or item.get("message")
        if isinstance(text, str) and text.strip():
            self.set_content(text, processing=True)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None and self.thread is not threading.current_thread():
            self.thread.join(timeout=2)
