import threading
import unittest
from typing import Optional

from conexgram.config import ProgressConfig
from conexgram.progress import ProgressHandle, ProgressNotifier


class FakeTelegram:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str, Optional[int]]] = []
        self.edited: list[tuple[int, int, str]] = []
        self.deleted: list[tuple[int, int]] = []
        self.typing: list[int] = []

    def send_message(self, chat_id: int, text: str, reply_to_message_id: Optional[int] = None) -> int:
        self.sent.append((chat_id, text, reply_to_message_id))
        return 100 + len(self.sent)

    def edit_message_text(self, chat_id: int, message_id: int, text: str) -> None:
        self.edited.append((chat_id, message_id, text))

    def delete_message(self, chat_id: int, message_id: int) -> None:
        self.deleted.append((chat_id, message_id))

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        if action == "typing":
            self.typing.append(chat_id)


class ProgressNotifierTests(unittest.TestCase):
    def test_live_updates_reuse_one_telegram_message(self):
        telegram = FakeTelegram()
        notifier = ProgressNotifier(telegram, ProgressConfig())
        handle = ProgressHandle(threading.Event())

        notifier.render_once(handle, 10, "still working", processing=True)
        notifier.render_once(handle, 10, "still working more", processing=True)

        self.assertEqual(telegram.sent, [(10, "still working\n\n⏳ Processing…", None)])
        self.assertEqual(
            telegram.edited,
            [(10, 101, "still working more\n\n⏳ Processing…")],
        )
        self.assertEqual(handle.message_ids, [101])

    def test_live_content_comes_from_codex_agent_event(self):
        handle = ProgressHandle(threading.Event())

        handle.update_from_event({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": "Typecheck is still running."},
        })

        self.assertEqual(handle.latest_content, "Typecheck is still running.")

    def test_completion_edits_processing_message_to_final_response(self):
        telegram = FakeTelegram()
        notifier = ProgressNotifier(telegram, ProgressConfig())
        handle = ProgressHandle(threading.Event())
        notifier.render_once(handle, 10, "interim", processing=True)

        notifier.complete(handle, 10, final_text="Final answer")

        self.assertEqual(
            telegram.edited[-1],
            (10, 101, "Final answer"),
        )
        self.assertTrue(handle.stop_event.is_set())

    def test_empty_processing_mirror_has_only_processing_marker(self):
        telegram = FakeTelegram()
        notifier = ProgressNotifier(telegram, ProgressConfig())
        handle = ProgressHandle(threading.Event())

        notifier.render_once(handle, 10, "", processing=True)

        self.assertEqual(telegram.sent, [(10, "⏳ Processing…", None)])

    def test_stale_chunks_are_deleted_when_final_response_shrinks(self):
        telegram = FakeTelegram()
        notifier = ProgressNotifier(telegram, ProgressConfig(), max_chars=100)
        handle = ProgressHandle(threading.Event())
        notifier.render_once(handle, 10, "a" * 180, processing=True)

        notifier.render_once(handle, 10, "short", processing=False)

        self.assertEqual(handle.message_ids, [101])
        self.assertEqual(telegram.deleted, [(10, 102)])


if __name__ == "__main__":
    unittest.main()
