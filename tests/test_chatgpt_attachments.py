import tempfile
import unittest
from pathlib import Path

from conexgram.chatgpt_attachments import extract_local_files


class ChatGPTAttachmentTests(unittest.TestCase):
    def test_extracts_markdown_local_links_and_decodes_spaces(self):
        files = extract_local_files(
            "Done. [Report](</Users/aldayglobal/Documents/report%20final.pdf>)"
        )

        self.assertEqual(len(files), 1)
        self.assertEqual(files[0].path_text, "/Users/aldayglobal/Documents/report final.pdf")
        self.assertEqual(files[0].display_name, "Report")

    def test_extracts_file_and_sandbox_links_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.txt"
            text = f"[one](file://{path}) and [two](sandbox:{path}) and `{path}`"
            files = extract_local_files(text)

        self.assertEqual([item.path_text for item in files], [str(path)])

    def test_ignores_remote_links(self):
        files = extract_local_files("[Cloudflare](https://example.com/file.zip)")

        self.assertEqual(files, [])


if __name__ == "__main__":
    unittest.main()
