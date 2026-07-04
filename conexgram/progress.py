"""Progress notifications while Codex is running."""

from __future__ import annotations

import logging
import random
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
                self._upsert_progress_message(
                    handle,
                    chat_id,
                    random.choice(self.config.messages),
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


class ProgressHandle:
    """Stop handle returned by ProgressNotifier.start."""

    def __init__(self, stop_event: threading.Event) -> None:
        self.stop_event = stop_event
        self.thread: Optional[threading.Thread] = None
        self.message_id: Optional[int] = None

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
