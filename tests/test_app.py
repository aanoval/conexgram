import tempfile
import unittest
from pathlib import Path

from conexgram.app import AttachmentDirective, GatewayApp
from conexgram.commands import FileCommandResponse
from conexgram.config import AppConfig, CodexConfig, GatewayConfig, TelegramConfig
from conexgram.session_store import Session


def make_app(tmp: str, max_upload_bytes: int = 1024) -> GatewayApp:
    root = Path(tmp)
    config = AppConfig(
        telegram=TelegramConfig(bot_token="token", allowed_user_ids={2}),
        codex=CodexConfig(binary="codex", default_working_dir=root),
        gateway=GatewayConfig(state_dir=root / "state", max_upload_bytes=max_upload_bytes),
        config_path=root / "config.json",
    )
    return GatewayApp(config)


class GatewayAppTests(unittest.TestCase):
    def test_extract_attachment_directives_removes_protocol_lines(self):
        text, directives = GatewayApp._extract_attachment_directives(
            "Done\n"
            "CONEXGRAM_SEND_FILE: /tmp/app.zip\n"
            "CONEXGRAM_SEND_FILE_CAPTION: app.zip\n"
        )

        self.assertEqual(text, "Done")
        self.assertEqual(directives, [AttachmentDirective("/tmp/app.zip", "app.zip")])

    def test_prepare_attachment_validates_relative_path_from_session_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(tmp)
            path = Path(tmp) / "artifact.zip"
            path.write_text("hello", encoding="utf-8")
            session = Session(
                id="s1",
                scope_key="chat:1",
                chat_id=1,
                user_id=2,
                working_dir=tmp,
            )

            response = app._prepare_attachment(AttachmentDirective("artifact.zip", "artifact"), session)

            self.assertIsInstance(response, FileCommandResponse)
            assert isinstance(response, FileCommandResponse)
            self.assertEqual(response.path, path.resolve())
            self.assertEqual(response.caption, "artifact")

    def test_prepare_attachment_rejects_large_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(tmp, max_upload_bytes=3)
            path = Path(tmp) / "artifact.zip"
            path.write_bytes(b"abcd")
            session = Session(
                id="s1",
                scope_key="chat:1",
                chat_id=1,
                user_id=2,
                working_dir=tmp,
            )

            response = app._prepare_attachment(AttachmentDirective("artifact.zip"), session)

            self.assertIsInstance(response, str)
            self.assertIn("Attachment file too large", response)


if __name__ == "__main__":
    unittest.main()
