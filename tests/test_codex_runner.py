import tempfile
import unittest
from pathlib import Path

from conexgram.codex_runner import CodexRunner
from conexgram.config import CodexConfig
from conexgram.session_store import Session


class CodexRunnerTests(unittest.TestCase):
    def test_build_command_includes_model_reasoning_and_full_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = CodexConfig(
                binary="codex",
                default_working_dir=Path(tmp),
                max_turn_seconds=60,
            )
            runner = CodexRunner(config, Path(tmp) / "logs")
            session = Session(
                id="s1",
                scope_key="chat:1",
                chat_id=1,
                user_id=2,
                working_dir=tmp,
                model="gpt-test",
                reasoning_effort="high",
                mode="full",
                full_access=True,
            )

            command = runner._build_command(session, Path(tmp) / "final.txt")

            self.assertIn("--model", command)
            self.assertIn("gpt-test", command)
            self.assertIn("--reasoning-effort", command)
            self.assertIn("high", command)
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)


if __name__ == "__main__":
    unittest.main()
