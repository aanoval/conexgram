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

    def test_build_command_includes_sandbox_and_approval_policy(self):
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
                sandbox_mode="workspace-write",
                approval_policy="on-request",
            )

            command = runner._build_command(session, Path(tmp) / "final.txt")

            self.assertEqual(command[:4], ["codex", "-a", "on-request", "exec"])
            self.assertIn("--sandbox", command)
            self.assertIn("workspace-write", command)
            self.assertNotIn("--dangerously-bypass-approvals-and-sandbox", command)

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

    def test_terminal_prompt_excludes_telegram_gateway_protocol(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner = CodexRunner(CodexConfig(binary="codex", default_working_dir=Path(tmp)), Path(tmp) / "logs")
            session = Session(
                id="s1",
                scope_key="cli:default",
                chat_id=0,
                user_id=0,
                working_dir=tmp,
                codex_thread_id="thread-1",
            )

            prompt = runner._build_prompt(session, "halo", prompt_mode="terminal")

            self.assertEqual(prompt, "User message:\nhalo\n")
            self.assertNotIn("CONEXGRAM_SEND_FILE:", prompt)
            self.assertNotIn("Telegram-controlled", prompt)

    def test_run_turn_emits_json_events_to_callback(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            script = work / "fake-codex"
            script.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "args = sys.argv\n"
                "out = args[args.index('--output-last-message') + 1]\n"
                "print(json.dumps({'type': 'thread.started', 'thread_id': 'thread-1'}), flush=True)\n"
                "print(json.dumps({'type': 'turn.started'}), flush=True)\n"
                "open(out, 'w', encoding='utf-8').write('final text')\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            runner = CodexRunner(CodexConfig(binary=str(script), default_working_dir=work), work / "logs")
            session = Session(
                id="s1",
                scope_key="cli:default",
                chat_id=0,
                user_id=0,
                working_dir=str(work),
            )
            events = []

            result = runner.run_turn(session, "hello", event_callback=events.append)

            self.assertEqual(result.thread_id, "thread-1")
            self.assertEqual(result.text, "final text")
            self.assertEqual([event["type"] for event in events], ["thread.started", "turn.started"])

    def test_run_turn_retries_with_spark_when_primary_model_hits_quota(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            script = work / "fake-codex"
            script.write_text(
                "#!/usr/bin/env python3\n"
                "import json, sys\n"
                "args = sys.argv\n"
                "out = args[args.index('--output-last-message') + 1]\n"
                "model = args[args.index('--model') + 1] if '--model' in args else 'default'\n"
                "if model == 'gpt-5.3-codex-spark':\n"
                "    print(json.dumps({'type': 'thread.started', 'thread_id': 'thread-spark'}), flush=True)\n"
                "    open(out, 'w', encoding='utf-8').write('spark final')\n"
                "    raise SystemExit(0)\n"
                "print(json.dumps({'type': 'turn.failed', 'error': {'message': 'rate limit reached'}}), flush=True)\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            config = CodexConfig(
                binary=str(script),
                default_working_dir=work,
                model_presets={"fast": "gpt-5.3-codex-spark"},
            )
            runner = CodexRunner(config, work / "logs")
            session = Session(
                id="s1",
                scope_key="chat:1",
                chat_id=1,
                user_id=2,
                working_dir=str(work),
                model="gpt-main",
            )

            result = runner.run_turn(session, "hello")

            self.assertEqual(result.return_code, 0)
            self.assertEqual(result.thread_id, "thread-spark")
            self.assertEqual(result.text, "spark final")
            self.assertEqual(session.model, "gpt-5.3-codex-spark")
            self.assertIn('"type": "conexgram.fallback"', result.raw_log_path.read_text(encoding="utf-8"))

    def test_run_turn_returns_natural_message_when_spark_quota_also_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            script = work / "fake-codex"
            script.write_text(
                "#!/usr/bin/env python3\n"
                "import json\n"
                "print(json.dumps({'type': 'turn.failed', 'error': {'message': '429 Too Many Requests: quota exhausted'}}), flush=True)\n"
                "raise SystemExit(1)\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            config = CodexConfig(
                binary=str(script),
                default_working_dir=work,
                model_presets={"fast": "gpt-5.3-codex-spark"},
            )
            runner = CodexRunner(config, work / "logs")
            session = Session(
                id="s1",
                scope_key="chat:1",
                chat_id=1,
                user_id=2,
                working_dir=str(work),
                model="gpt-main",
            )

            result = runner.run_turn(session, "hello")

            self.assertEqual(result.return_code, 0)
            self.assertIn("Your Codex quota", result.text)
            self.assertIn("gpt-5.3-codex-spark", result.text)
            self.assertNotIn("Codex exited with code", result.text)


if __name__ == "__main__":
    unittest.main()
