"""Progress notifications while Codex is running."""

from __future__ import annotations

import logging
import random
import threading
import time
from collections.abc import Callable
from typing import Optional

from .config import ProgressConfig
from .session_store import Session
from .telegram_api import TelegramApiError, TelegramClient

LOG = logging.getLogger(__name__)


SendText = Callable[[int, str, Optional[int]], None]


class ProgressNotifier:
    """Send Telegram typing actions and optional progress messages for a turn."""

    def __init__(
        self,
        telegram: TelegramClient,
        config: ProgressConfig,
        send_text: SendText,
    ) -> None:
        self.telegram = telegram
        self.config = config
        self.send_text = send_text

    def start(self, session: Session, chat_id: int, reply_to_message_id: int) -> "ProgressHandle":
        stop_event = threading.Event()
        thread = threading.Thread(
            target=self._run,
            args=(session, chat_id, reply_to_message_id, stop_event),
            name=f"progress-{chat_id}",
            daemon=True,
        )
        thread.start()
        return ProgressHandle(stop_event, thread)

    def _run(
        self,
        session: Session,
        chat_id: int,
        reply_to_message_id: int,
        stop_event: threading.Event,
    ) -> None:
        typing_enabled = self._effective_bool(session.typing_indicator, self.config.typing_indicator)
        messages_enabled = self._effective_bool(session.progress_messages, self.config.progress_messages)
        last_message_at = time.monotonic()

        while not stop_event.is_set():
            if typing_enabled:
                try:
                    self.telegram.send_chat_action(chat_id, "typing")
                except TelegramApiError as exc:
                    LOG.debug("Failed to send typing indicator: %s", exc)

            now = time.monotonic()
            if messages_enabled and now - last_message_at >= self.config.progress_interval_seconds:
                self.send_text(
                    chat_id,
                    random.choice(self.config.messages),
                    reply_to_message_id,
                )
                last_message_at = now

            wait_seconds = self.config.typing_interval_seconds if typing_enabled else 1
            stop_event.wait(wait_seconds)

    @staticmethod
    def _effective_bool(value: Optional[bool], default: bool) -> bool:
        return default if value is None else bool(value)


class ProgressHandle:
    """Stop handle returned by ProgressNotifier.start."""

    def __init__(self, stop_event: threading.Event, thread: threading.Thread) -> None:
        self.stop_event = stop_event
        self.thread = thread

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)
