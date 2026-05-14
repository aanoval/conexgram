import unittest

from conexgram.message_format import split_message


class SplitMessageTests(unittest.TestCase):
    def test_short_message(self):
        self.assertEqual(split_message("hello", 100), ["hello"])

    def test_long_message_splits(self):
        chunks = split_message("a" * 250, 100)
        self.assertEqual(len(chunks), 3)
        self.assertTrue(all(len(chunk) <= 100 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
