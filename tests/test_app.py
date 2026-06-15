import os
import tempfile
import time
import unittest
from pathlib import Path

from conexgram.app import AttachmentDirective, GatewayApp
from conexgram.commands import FileCommandResponse
from conexgram.config import AppConfig, CodexConfig, GatewayConfig, TelegramConfig
from conexgram.session_store import Session
from conexgram.stt import SttResult
from conexgram.telegram_api import TelegramMessage


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

    def test_media_upload_is_queued_as_codex_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(tmp)
            app.commands.active_profile_has_auth = lambda chat_id, user_id: True  # type: ignore[method-assign]
            app.telegram.download_file = lambda file_id, destination: destination.write_bytes(b"image")  # type: ignore[method-assign]

            app._handle_message(TelegramMessage(
                update_id=1,
                message_id=10,
                chat_id=1,
                user_id=2,
                text="describe this",
                document_file_id="photo-file",
                document_file_name="telegram-photo-10.jpg",
                media_type="photo",
            ))

            queued = app.queue.get_nowait().message
            self.assertIn("Telegram media received.", queued.text)
            self.assertIn("- Type: photo", queued.text)
            self.assertIn("telegram_uploads/telegram-photo-10.jpg", queued.text)
            self.assertIn("User caption/instruction:\ndescribe this", queued.text)
            self.assertIsNone(queued.document_file_id)

    def test_media_upload_without_caption_is_still_queued_as_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(tmp)
            app.commands.active_profile_has_auth = lambda chat_id, user_id: True  # type: ignore[method-assign]
            app.telegram.download_file = lambda file_id, destination: destination.write_bytes(b"voice")  # type: ignore[method-assign]

            app._handle_message(TelegramMessage(
                update_id=1,
                message_id=11,
                chat_id=1,
                user_id=2,
                text="/upload",
                document_file_id="voice-file",
                document_file_name="telegram-voice-11.ogg",
                media_type="voice",
            ))

            queued = app.queue.get_nowait().message
            self.assertIn("- Type: voice", queued.text)
            self.assertIn("Audio transcript is not available.", queued.text)
            self.assertIn("Do not run other local audio transcription tools", queued.text)
            self.assertIn("No caption was provided.", queued.text)

    def test_voice_upload_queues_transcript_as_codex_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(tmp)
            app.commands.active_profile_has_auth = lambda chat_id, user_id: True  # type: ignore[method-assign]
            app.telegram.download_file = lambda file_id, destination: destination.write_bytes(b"voice")  # type: ignore[method-assign]
            app.stt_transcriber.transcribe = lambda path, media_type: SttResult(  # type: ignore[method-assign]
                text="tolong cek status project ini",
            )

            app._handle_message(TelegramMessage(
                update_id=1,
                message_id=12,
                chat_id=1,
                user_id=2,
                text="/upload",
                document_file_id="voice-file",
                document_file_name="telegram-voice-12.ogg",
                media_type="voice",
            ))

            queued = app.queue.get_nowait().message
            self.assertIn("Audio transcript:", queued.text)
            self.assertIn("tolong cek status project ini", queued.text)
            self.assertIn("Use the transcript as the user's voice instruction/context.", queued.text)
            self.assertIn("No caption was provided. Treat the audio transcript as the latest user message", queued.text)

    def test_cleanup_uploads_deletes_expired_files_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = make_app(tmp)
            upload_dir = Path(tmp) / "telegram_uploads"
            nested = upload_dir / "_transcoded"
            nested.mkdir(parents=True)
            old_file = upload_dir / "old.ogg"
            fresh_file = upload_dir / "fresh.jpg"
            old_nested = nested / "old.mp3"
            old_file.write_bytes(b"old")
            fresh_file.write_bytes(b"fresh")
            old_nested.write_bytes(b"old nested")
            old_time = time.time() - 8 * 3600
            for path in (old_file, old_nested):
                path.touch()
                os.utime(path, (old_time, old_time))

            app._cleanup_uploads_once()

            self.assertFalse(old_file.exists())
            self.assertFalse(old_nested.exists())
            self.assertTrue(fresh_file.exists())


if __name__ == "__main__":
    unittest.main()
