import threading
import time
import unittest
from typing import Optional

from conexgram.config import ProgressConfig
from conexgram.progress import ProgressHandle, ProgressNotifier
from conexgram.session_store import Session


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

        self.assertEqual(handle.latest_status, "Codex is running verification.")

    def test_progress_status_hides_raw_command_text(self):
        handle = ProgressHandle(threading.Event())

        handle.update_from_event({
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "/bin/bash -lc \"sed -n '600,635p' /srv/app/file.tsx\"",
            },
        })

        self.assertEqual(handle.latest_status, "Codex finished inspecting the workspace.")
        self.assertNotIn("sed -n", handle.latest_status)

    def test_complete_replaces_progress_with_elapsed_time(self):
        telegram = FakeTelegram()
        notifier = ProgressNotifier(telegram, ProgressConfig())
        handle = ProgressHandle(threading.Event())
        handle.message_id = 101
        handle.started_at = time.monotonic() - 127

        notifier.complete(handle, 10)

        self.assertEqual(telegram.edited, [(10, 101, "Completed in 2m 7s.")])

    def test_complete_marks_failed_turn_as_stopped(self):
        telegram = FakeTelegram()
        notifier = ProgressNotifier(telegram, ProgressConfig())
        handle = ProgressHandle(threading.Event())
        handle.message_id = 101
        handle.started_at = time.monotonic() - 5

        notifier.complete(handle, 10, success=False)

        self.assertEqual(telegram.edited, [(10, 101, "Stopped after 5s.")])

    def test_ultra_checkpoint_preserves_interim_and_starts_new_progress_message(self):
        telegram = FakeTelegram()
        notifier = ProgressNotifier(telegram, ProgressConfig())
        handle = ProgressHandle(threading.Event())
        handle.message_id = 101
        handle.started_at = time.monotonic() - 1800
        handle.update_from_event({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Typecheck passed; Rust tests are still running."},
        })
        handle.update_from_event({
            "type": "item.started",
            "item": {"type": "command_execution", "command": "cargo test"},
        })

        notifier._publish_checkpoint(handle, 10, 99)

        self.assertEqual(len(telegram.edited), 1)
        self.assertEqual(telegram.edited[0][1], 101)
        self.assertIn("Interim update after 30m 0s", telegram.edited[0][2])
        self.assertIn("Typecheck passed", telegram.edited[0][2])
        self.assertEqual(telegram.sent, [(10, "Codex is running verification.", 99)])
        self.assertEqual(handle.message_id, 101)

    def test_checkpoint_mode_only_applies_to_max_and_ultra(self):
        base = dict(id="s1", scope_key="chat:1", chat_id=1, user_id=2, working_dir="/tmp")

        self.assertTrue(ProgressNotifier._checkpoint_mode(Session(**base, reasoning_effort="max")))
        self.assertTrue(ProgressNotifier._checkpoint_mode(Session(**base, reasoning_effort="ULTRA")))
        self.assertFalse(ProgressNotifier._checkpoint_mode(Session(**base, reasoning_effort="xhigh")))


if __name__ == "__main__":
    unittest.main()
