import unittest
from unittest.mock import patch

from conexgram.telegram_api import TelegramApiError, TelegramClient


class TelegramClientTests(unittest.TestCase):
    def test_timeout_is_wrapped_as_telegram_api_error(self):
        client = TelegramClient("token")

        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaises(TelegramApiError) as context:
                client.send_message(123, "hello")

        self.assertIn("Telegram network timeout", str(context.exception))

    def test_parse_photo_message_uses_largest_photo(self):
        client = TelegramClient("token")

        message = client.parse_text_message({
            "update_id": 11,
            "message": {
                "message_id": 22,
                "chat": {"id": 33},
                "from": {"id": 44},
                "caption": "analyze this image",
                "photo": [
                    {"file_id": "small", "width": 90, "height": 90, "file_size": 100},
                    {"file_id": "large", "width": 1280, "height": 720, "file_size": 2000},
                ],
            },
        })

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.text, "analyze this image")
        self.assertEqual(message.document_file_id, "large")
        self.assertEqual(message.document_file_name, "telegram-photo-22.jpg")
        self.assertEqual(message.media_type, "photo")

    def test_parse_voice_message_without_caption_is_upload(self):
        client = TelegramClient("token")

        message = client.parse_text_message({
            "update_id": 12,
            "message": {
                "message_id": 23,
                "chat": {"id": 33},
                "from": {"id": 44},
                "voice": {"file_id": "voice-file", "mime_type": "audio/ogg"},
            },
        })

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.text, "/upload")
        self.assertEqual(message.document_file_id, "voice-file")
        self.assertEqual(message.document_file_name, "telegram-voice-23.ogg")
        self.assertEqual(message.media_type, "voice")

    def test_parse_audio_message_keeps_file_name(self):
        client = TelegramClient("token")

        message = client.parse_text_message({
            "update_id": 13,
            "message": {
                "message_id": 24,
                "chat": {"id": 33},
                "from": {"id": 44},
                "audio": {
                    "file_id": "audio-file",
                    "file_name": "../song.mp3",
                    "mime_type": "audio/mpeg",
                },
            },
        })

        self.assertIsNotNone(message)
        assert message is not None
        self.assertEqual(message.document_file_id, "audio-file")
        self.assertEqual(message.document_file_name, "song.mp3")
        self.assertEqual(message.media_type, "audio")


if __name__ == "__main__":
    unittest.main()
