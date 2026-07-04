"""Progress notifications while Codex is running."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .config import ProgressConfig
from .session_store import Session
from .telegram_api import TelegramApiError, TelegramClient

LOG = logging.getLogger(__name__)


class ProgressNotifier:
    """Send Telegram typing actions and optional progress messages for a turn."""

    def __init__(
        self,
        telegram: TelegramClient,
        config: ProgressConfig,
    ) -> None:
        self.telegram = telegram
        self.config = config

    def start(self, session: Session, chat_id: int, reply_to_message_id: int) -> "ProgressHandle":
        stop_event = threading.Event()
        handle = ProgressHandle(stop_event)
        thread = threading.Thread(
            target=self._run,
            args=(session, chat_id, reply_to_message_id, handle),
            name=f"progress-{chat_id}",
            daemon=True,
        )
        handle.thread = thread
        thread.start()
        return handle

    def _run(
        self,
        session: Session,
        chat_id: int,
        reply_to_message_id: int,
        handle: "ProgressHandle",
    ) -> None:
        typing_enabled = self._effective_bool(session.typing_indicator, self.config.typing_indicator)
        messages_enabled = self._effective_bool(session.progress_messages, self.config.progress_messages)
        last_message_at = time.monotonic()

        while not handle.stop_event.is_set():
            if typing_enabled:
                try:
                    self.telegram.send_chat_action(chat_id, "typing")
                except TelegramApiError as exc:
                    LOG.debug("Failed to send typing indicator: %s", exc)

            now = time.monotonic()
            if messages_enabled and now - last_message_at >= self.config.progress_interval_seconds:
                status = handle.latest_status
                if not status:
                    handle.stop_event.wait(self.config.typing_interval_seconds if typing_enabled else 1)
                    continue
                self._upsert_progress_message(
                    handle,
                    chat_id,
                    status,
                    reply_to_message_id,
                )
                last_message_at = now

            wait_seconds = self.config.typing_interval_seconds if typing_enabled else 1
            handle.stop_event.wait(wait_seconds)

    def _upsert_progress_message(
        self,
        handle: "ProgressHandle",
        chat_id: int,
        text: str,
        reply_to_message_id: int,
    ) -> None:
        message_id = handle.message_id
        if message_id is not None:
            try:
                self.telegram.edit_message_text(chat_id, message_id, text)
                return
            except TelegramApiError as exc:
                if "message is not modified" in str(exc).lower():
                    return
                LOG.debug("Failed to edit progress message: %s", exc)
        try:
            new_message_id = self.telegram.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
            handle.message_id = new_message_id
        except TelegramApiError as exc:
            LOG.debug("Failed to send progress message: %s", exc)

    @staticmethod
    def _effective_bool(value: Optional[bool], default: bool) -> bool:
        return default if value is None else bool(value)

    @staticmethod
    def event_status(event: dict) -> str:
        event_type = str(event.get("type") or "")
        if event_type == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict):
                return ProgressNotifier._compact(str(error.get("message") or "turn failed"))
            return "turn failed"
        if event_type == "error":
            return ProgressNotifier._compact(str(event.get("message") or "error"))

        item = event.get("item")
        if not isinstance(item, dict):
            return ""
        item_type = str(item.get("type") or "item")
        if item_type == "agent_message":
            return ""
        label = ProgressNotifier._item_label(item_type, item)
        if not label:
            return ""
        if event_type.endswith(".completed") or event_type == "item.completed":
            return ProgressNotifier._compact(f"completed {label}")
        if event_type.endswith(".started") or event_type == "item.started":
            return ProgressNotifier._compact(label)
        return ""

    @staticmethod
    def _item_label(item_type: str, item: dict) -> str:
        if item_type == "file_change":
            changes = item.get("changes")
            if isinstance(changes, list) and changes:
                first = changes[0]
                if isinstance(first, dict):
                    kind = str(first.get("kind") or "edit")
                    path = str(first.get("path") or "").strip()
                    if path:
                        return f"file_change: {kind} {path}"
            return "file_change"
        for key in ("command", "cmd", "title", "name", "text"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return f"{item_type}: {value}"
        return item_type

    @staticmethod
    def _compact(text: str, limit: int = 220) -> str:
        cleaned = " ".join(text.strip().split())
        if len(cleaned) > limit:
            return cleaned[: limit - 3].rstrip() + "..."
        return cleaned


class ProgressHandle:
    """Stop handle returned by ProgressNotifier.start."""

    def __init__(self, stop_event: threading.Event) -> None:
        self.stop_event = stop_event
        self.thread: Optional[threading.Thread] = None
        self.message_id: Optional[int] = None
        self._status_lock = threading.Lock()
        self._latest_status = ""

    @property
    def latest_status(self) -> str:
        with self._status_lock:
            return self._latest_status

    def update_from_event(self, event: dict) -> None:
        status = ProgressNotifier.event_status(event)
        if not status:
            return
        with self._status_lock:
            self._latest_status = status

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
