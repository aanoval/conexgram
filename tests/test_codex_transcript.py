import json
import tempfile
import unittest
from pathlib import Path

from conexgram.codex_transcript import CodexTranscriptReader


class CodexTranscriptReaderTests(unittest.TestCase):
    def _write_rollout(self, root: Path, thread_id: str, records: list[dict]) -> Path:
        path = root / ".codex" / "sessions" / "2026" / "08" / "19"
        path.mkdir(parents=True)
        rollout = path / f"rollout-2026-08-19T00-00-00-{thread_id}.jsonl"
        rollout.write_text(
            "".join(json.dumps(record) + "\n" for record in records),
            encoding="utf-8",
        )
        return rollout

    def test_reads_latest_final_agent_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            thread_id = "thread-final"
            self._write_rollout(root, thread_id, [
                {"type": "event_msg", "payload": {"type": "task_started"}},
                {"type": "event_msg", "payload": {"type": "agent_message", "message": "interim"}},
                {"type": "event_msg", "payload": {
                    "type": "task_complete",
                    "last_agent_message": "final response",
                }},
            ])

            snapshot = CodexTranscriptReader(root).snapshot(thread_id)

            self.assertFalse(snapshot.processing)
            self.assertTrue(snapshot.final)
            self.assertEqual(snapshot.content, "final response")

    def test_marks_unfinished_rollout_processing_without_fake_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            thread_id = "thread-running"
            self._write_rollout(root, thread_id, [
                {"type": "event_msg", "payload": {"type": "task_started"}},
                {"type": "event_msg", "payload": {
                    "type": "agent_message",
                    "message": "real interim response",
                }},
            ])

            snapshot = CodexTranscriptReader(root).snapshot(thread_id)

            self.assertTrue(snapshot.processing)
            self.assertFalse(snapshot.final)
            self.assertEqual(snapshot.content, "real interim response")

    def test_ignores_partial_json_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            thread_id = "thread-partial"
            rollout = self._write_rollout(root, thread_id, [
                {"type": "event_msg", "payload": {
                    "type": "task_complete",
                    "last_agent_message": "done",
                }},
            ])
            rollout.write_text(rollout.read_text(encoding="utf-8") + '{"type":', encoding="utf-8")

            snapshot = CodexTranscriptReader(root).snapshot(thread_id)

            self.assertEqual(snapshot.content, "done")
            self.assertTrue(snapshot.final)


if __name__ == "__main__":
    unittest.main()
