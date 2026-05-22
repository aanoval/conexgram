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


if __name__ == "__main__":
    unittest.main()
