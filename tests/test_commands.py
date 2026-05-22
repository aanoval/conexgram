import tempfile
import unittest
from pathlib import Path

from conexgram.commands import CommandHandler, FileCommandResponse, MessageCommandResponse
from conexgram.config import AppConfig, CodexConfig, GatewayConfig, TelegramConfig
from conexgram.session_store import SessionStore


def make_handler(tmp: str, max_upload_bytes: int = 1024) -> CommandHandler:
    root = Path(tmp)
    config = AppConfig(
        telegram=TelegramConfig(bot_token="token", allowed_user_ids={2}),
        codex=CodexConfig(
            binary="codex",
            default_working_dir=root,
            allow_runtime_full_access=True,
            presets={"computer": {"mode": "full", "full_access": True}},
        ),
        gateway=GatewayConfig(state_dir=root / "state", max_upload_bytes=max_upload_bytes),
        config_path=root / "config.json",
    )
    return CommandHandler(config, SessionStore(root / "sessions.json"))


class CommandHandlerTests(unittest.TestCase):
    def test_sendfile_returns_file_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.txt"
            path.write_text("hello", encoding="utf-8")
            handler = make_handler(tmp)

            response = handler.handle_command(f'/sendfile "{path}" sample caption', 1, 2)

            self.assertIsInstance(response, FileCommandResponse)
            assert isinstance(response, FileCommandResponse)
            self.assertEqual(response.path, path.resolve())
            self.assertEqual(response.caption, "sample caption")

    def test_sendfile_rejects_large_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            path.write_bytes(b"abcd")
            handler = make_handler(tmp, max_upload_bytes=3)

            response = handler.handle_command(f'/sendfile "{path}"', 1, 2)

            self.assertIsInstance(response, str)
            self.assertIn("File too large", response)

    def test_computer_access_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/computer on", 1, 2)

            self.assertIsInstance(response, str)
            self.assertIn("/confirm computer", response)

    def test_confirm_computer_enables_session_full_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/confirm computer", 1, 2)
            session = handler.ensure_session(1, 2)

            self.assertEqual(response, "Computer Access enabled for this session.")
            self.assertTrue(session.full_access)
            self.assertEqual(session.mode, "full")

    def test_settings_returns_inline_keyboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/settings", 1, 2)

            self.assertIsInstance(response, MessageCommandResponse)
            assert isinstance(response, MessageCommandResponse)
            self.assertIn("Settings:", response.text)
            self.assertIsNotNone(response.reply_markup)

    def test_codex_command_runs_native_binary_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake-codex"
            script.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "print('ARGS=' + repr(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            handler = make_handler(tmp)
            handler.config = AppConfig(
                telegram=handler.config.telegram,
                codex=CodexConfig(
                    binary=str(script),
                    default_working_dir=Path(tmp),
                    allow_runtime_full_access=True,
                ),
                gateway=handler.config.gateway,
                config_path=handler.config.config_path,
            )

            response = handler.handle_command('/codex debug "two words"', 1, 2)

            self.assertIsInstance(response, str)
            self.assertIn("ARGS=['debug', 'two words']", response)

    def test_format_codex_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            response = handler._format_codex_usage(
                {
                    "plan_type": "prolite",
                    "rate_limit": {
                        "allowed": True,
                        "limit_reached": False,
                        "primary_window": {
                            "used_percent": 16,
                            "limit_window_seconds": 18000,
                            "reset_after_seconds": 3600,
                        },
                        "secondary_window": {
                            "used_percent": 92,
                            "limit_window_seconds": 604800,
                            "reset_after_seconds": 86400,
                        },
                    },
                    "credits": {
                        "has_credits": False,
                        "unlimited": False,
                        "balance": "0",
                    },
                }
            )

            self.assertIn("Plan: prolite", response)
            self.assertIn("5h: 16% used", response)
            self.assertIn("weekly: 92% used", response)
            self.assertIn("Credits: balance 0, no credits", response)


if __name__ == "__main__":
    unittest.main()
