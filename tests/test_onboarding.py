import tempfile
import unittest

from conexgram import onboarding
from conexgram.cli import _is_first_run_config_error


class OnboardingTests(unittest.TestCase):
    def test_random_code_has_expected_length(self):
        code = onboarding._random_code(6)
        self.assertEqual(len(code), 6)
        for char in code:
            self.assertTrue(char.isupper() or char.isdigit())

    def test_load_or_seed_config_keeps_partial_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tempfile.NamedTemporaryFile(delete=False, dir=tmp, suffix=".json").name
            with open(path, "w", encoding="utf-8") as fp:
                fp.write('{"telegram":{"bot_token":"ABC"},"codex":{"default_working_dir":"/tmp"}}')

            data = onboarding._load_or_seed_config(onboarding.Path(path))

            self.assertEqual(data["telegram"]["bot_token"], "ABC")
            self.assertIn("codex", data)
            self.assertEqual(data["codex"].get("default_working_dir"), "/tmp")


class CliConfigErrorTests(unittest.TestCase):
    def test_first_run_config_errors(self):
        self.assertTrue(_is_first_run_config_error(FileNotFoundError("missing")))
        self.assertTrue(_is_first_run_config_error(Exception("telegram.bot_token is not configured")))
        self.assertTrue(_is_first_run_config_error(Exception("Configure telegram.allowed_user_ids or telegram.allowed_chat_ids")))

    def test_non_first_run_errors(self):
        self.assertFalse(_is_first_run_config_error(Exception("Something else")))


if __name__ == "__main__":
    unittest.main()
