import json
import shutil
import tempfile
import unittest
from pathlib import Path

from conexgram.config import load_config, save_config


class ConfigTests(unittest.TestCase):
    def test_load_config_reads_runtime_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config_path = root / "config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "telegram": {
                            "bot_token": "123:abc",
                            "allowed_user_ids": [1],
                            "allowed_chat_ids": [],
                            "api_base_url": "http://127.0.0.1:8081/",
                            "local_bot_api": True,
                        },
                        "codex": {
                            "binary": shutil.which("python3") or "python3",
                            "default_working_dir": str(root),
                            "workspace_roots": [str(root)],
                            "max_turn_seconds": 120,
                        },
                        "gateway": {
                            "state_dir": str(root / "state"),
                            "worker_count": 2,
                            "max_log_days": 7,
                            "max_log_mb": 50,
                        },
                        "stt": {
                            "enabled": True,
                            "python": str(root / ".venv-stt" / "bin" / "python"),
                            "model": "tiny",
                            "language": "id",
                            "device": "cpu",
                            "compute_type": "int8",
                            "media_types": ["voice", "audio"],
                            "timeout_seconds": 45,
                        },
                        "uploads": {
                            "retention_hours": 6,
                            "cleanup_interval_minutes": 30,
                            "keep_transcripts": True,
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.codex.max_turn_seconds, 120)
            self.assertEqual(config.telegram.api_base_url, "http://127.0.0.1:8081")
            self.assertTrue(config.telegram.local_bot_api)
            self.assertEqual(config.gateway.worker_count, 2)
            self.assertEqual(config.gateway.max_log_days, 7)
            self.assertEqual(config.gateway.max_log_mb, 50)
            self.assertTrue(config.stt.enabled)
            self.assertEqual(config.stt.model, "tiny")
            self.assertEqual(config.stt.language, "id")
            self.assertEqual(config.stt.device, "cpu")
            self.assertEqual(config.stt.compute_type, "int8")
            self.assertEqual(config.stt.media_types, ["voice", "audio"])
            self.assertEqual(config.stt.timeout_seconds, 45)
            self.assertEqual(config.uploads.retention_hours, 6)
            self.assertEqual(config.uploads.cleanup_interval_minutes, 30)
            self.assertTrue(config.uploads.keep_transcripts)

            save_config(config)
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(saved["telegram"]["api_base_url"], "http://127.0.0.1:8081")
            self.assertTrue(saved["telegram"]["local_bot_api"])


if __name__ == "__main__":
    unittest.main()
