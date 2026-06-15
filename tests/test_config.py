import json
import shutil
import tempfile
import unittest
from pathlib import Path

from conexgram.config import load_config


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
                        "audio_transcription": {
                            "enabled": True,
                            "provider": "openai",
                            "model": "gpt-4o-transcribe",
                            "api_key_env": "CUSTOM_OPENAI_KEY",
                            "language": "id",
                            "prompt": "Telegram voice note in Indonesian.",
                            "timeout_seconds": 45,
                            "max_audio_bytes": 12345,
                            "convert_unsupported": True,
                            "ffmpeg_binary": "ffmpeg",
                        },
                    }
                ),
                encoding="utf-8",
            )

            config = load_config(config_path)

            self.assertEqual(config.codex.max_turn_seconds, 120)
            self.assertEqual(config.gateway.worker_count, 2)
            self.assertEqual(config.gateway.max_log_days, 7)
            self.assertEqual(config.gateway.max_log_mb, 50)
            self.assertTrue(config.audio_transcription.enabled)
            self.assertEqual(config.audio_transcription.provider, "openai")
            self.assertEqual(config.audio_transcription.model, "gpt-4o-transcribe")
            self.assertEqual(config.audio_transcription.api_key_env, "CUSTOM_OPENAI_KEY")
            self.assertEqual(config.audio_transcription.language, "id")
            self.assertEqual(config.audio_transcription.prompt, "Telegram voice note in Indonesian.")
            self.assertEqual(config.audio_transcription.timeout_seconds, 45)
            self.assertEqual(config.audio_transcription.max_audio_bytes, 12345)


if __name__ == "__main__":
    unittest.main()
