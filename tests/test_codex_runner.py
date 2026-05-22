import tempfile
import unittest
from pathlib import Path

from conexgram.codex_runner import CodexRunner
from conexgram.config import CodexConfig
from conexgram.session_store import Session


class CodexRunnerTests(unittest.TestCase):
    def test_build_command_includes_model_reasoning_config_and_full_access(self):
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
            self.assertNotIn("--reasoning-effort", command)
            self.assertIn("-c", command)
            self.assertIn('model_reasoning_effort="high"', command)
            self.assertIn("--dangerously-bypass-approvals-and-sandbox", command)

    def test_build_command_omits_reasoning_when_using_codex_default(self):
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
                reasoning_effort=None,
            )

            command = runner._build_command(session, Path(tmp) / "final.txt")

            self.assertNotIn("--reasoning-effort", command)
            self.assertNotIn("model_reasoning_effort", " ".join(command))

    def test_resume_prompt_includes_gateway_file_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = CodexRunner(CodexConfig(binary="codex", default_working_dir=Path(tmp)), Path(tmp) / "logs")
            session = Session(
                id="s1",
                scope_key="chat:1",
                chat_id=1,
                user_id=2,
                working_dir=tmp,
                codex_thread_id="thread-1",
            )

            prompt = runner._build_prompt(session, "send this file")

            self.assertIn("CONEXGRAM_SEND_FILE:", prompt)
            self.assertIn("User message:\nsend this file", prompt)


if __name__ == "__main__":
    unittest.main()
