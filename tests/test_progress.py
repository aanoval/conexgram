import threading
import unittest
from typing import Optional

from conexgram.config import ProgressConfig
from conexgram.progress import ProgressHandle, ProgressNotifier


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Optional[int]]] = []
        self.edited: list[tuple[int, int, str]] = []

    def send_message(self, chat_id: int, text: str, reply_to_message_id: Optional[int] = None) -> int:
        self.sent.append((chat_id, text, reply_to_message_id))
        return 100 + len(self.sent)

    def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        self.edited.append((chat_id, message_id, text))


class ProgressNotifierTests(unittest.TestCase):
    def test_progress_updates_reuse_one_telegram_message(self):
        telegram = FakeTelegram()
        notifier = ProgressNotifier(telegram, ProgressConfig())
        handle = ProgressHandle(threading.Event())

        notifier._upsert_progress_message(handle, 10, "still working", 99)
        notifier._upsert_progress_message(handle, 10, "still working more", 99)

        self.assertEqual(telegram.sent, [(10, "still working", 99)])
        self.assertEqual(telegram.edited, [(10, 101, "still working more")])
        self.assertEqual(handle.message_id, 101)

    def test_progress_status_comes_from_codex_event(self):
        handle = ProgressHandle(threading.Event())

        handle.update_from_event({
            "type": "item.started",
            "item": {"type": "shell_command", "command": "npm test\nwith newline"},
        })

        self.assertEqual(handle.latest_status, "shell_command: npm test with newline")


if __name__ == "__main__":
    unittest.main()
