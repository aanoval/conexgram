import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

from conexgram.telegram_api import TelegramApiError, TelegramClient


class TelegramClientTests(unittest.TestCase):
    def test_local_send_document_uses_file_uri_without_multipart_buffer(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "movie.mp4"
            path.write_bytes(b"video")
            client = TelegramClient(
                "token",
                api_base_url="http://127.0.0.1:8081",
                local_bot_api=True,
            )

            with patch.object(client, "_streaming_multipart_request", return_value={}) as request:
                client.send_document(123, path, caption="movie")

            method, payload, file_field, sent_path = request.call_args.args[:4]
            self.assertEqual(method, "sendDocument")
            self.assertEqual(payload["chat_id"], "123")
            self.assertEqual(file_field, "document")
            self.assertEqual(sent_path, path)
            self.assertEqual(payload["caption"], "movie")
            self.assertEqual(request.call_args.kwargs["timeout"], 600)

    def test_streaming_multipart_sends_file_in_chunks(self):
        class FakeResponse:
            status = 200

            @staticmethod
            def read():
                return b'{"ok":true,"result":{"message_id":1}}'

        class FakeConnection:
            instance = None

            def __init__(self, host, port, timeout):
                self.host = host
                self.port = port
                self.timeout = timeout
                self.sent = []
                self.headers = {}
                FakeConnection.instance = self

            def putrequest(self, method, path):
                self.method = method
                self.path = path

            def putheader(self, name, value):
                self.headers[name] = value

            def endheaders(self):
                pass

            def send(self, data):
                self.sent.append(bytes(data))

            def getresponse(self):
                return FakeResponse()

            def close(self):
                pass

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            path.write_bytes(b"x" * (600 * 1024))
            client = TelegramClient(
                "token",
                api_base_url="http://127.0.0.1:8081",
                local_bot_api=True,
            )

            with patch("http.client.HTTPConnection", FakeConnection):
                client._streaming_multipart_request(
                    "sendDocument",
                    {"chat_id": "123"},
                    "document",
                    path,
                    timeout=600,
                )

            connection = FakeConnection.instance
            assert connection is not None
            self.assertEqual(connection.host, "127.0.0.1")
            self.assertEqual(connection.port, 8081)
            self.assertEqual(connection.path, "/bottoken/sendDocument")
            self.assertEqual(connection.timeout, 600)
            self.assertEqual(
                connection.headers["Content-Length"],
                str(sum(map(len, connection.sent))),
            )
            file_chunks = connection.sent[1:-1]
            self.assertEqual(
                [len(chunk) for chunk in file_chunks],
                [256 * 1024, 256 * 1024, 88 * 1024],
            )

    def test_large_document_upload_timeout_scales_with_file_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.zip"
            path.touch()
            with patch.object(Path, "stat") as stat:
                stat.return_value.st_size = 170 * 1024 * 1024
                timeout = TelegramClient._document_upload_timeout(path)

            self.assertEqual(timeout, 800)

    def test_local_download_copies_absolute_get_file_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "telegram-local.bin"
            destination = root / "download" / "copy.bin"
            source.write_bytes(b"payload")
            client = TelegramClient(
                "token",
                api_base_url="http://127.0.0.1:8081",
                local_bot_api=True,
            )

            with patch.object(client, "_request", return_value={"file_path": str(source)}):
                result = client.download_file("file-id", destination)

            self.assertEqual(result, destination)
            self.assertEqual(destination.read_bytes(), b"payload")

    def test_custom_api_base_url_is_used_for_requests(self):
        client = TelegramClient("token", api_base_url="http://127.0.0.1:8081")

        with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")) as urlopen:
            with self.assertRaises(TelegramApiError):
                client.get_me()

        self.assertEqual(urlopen.call_args.args[0].full_url, "http://127.0.0.1:8081/bottoken/getMe")

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
