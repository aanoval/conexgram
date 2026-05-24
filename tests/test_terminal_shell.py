import base64
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from conexgram.config import AppConfig, CodexConfig, GatewayConfig, TelegramConfig
from conexgram.terminal_shell import FileChangeTracker, TerminalShell


def make_fake_auth(path: Path, email: str, name: str) -> None:
    payload = json.dumps({"email": email, "name": name}).encode("utf-8")
    payload_encoded = base64.urlsafe_b64encode(payload).decode("utf-8").rstrip("=")
    auth = {
        "tokens": {
            "id_token": f"header.{payload_encoded}.sig",
            "access_token": "test-access-token",
        }
    }
    auth_dir = path / ".codex"
    auth_dir.mkdir(parents=True, exist_ok=True)
    (auth_dir / "auth.json").write_text(json.dumps(auth), encoding="utf-8")


def make_config(tmp: str) -> AppConfig:
    root = Path(tmp)
    return AppConfig(
        telegram=TelegramConfig(bot_token="token", allowed_user_ids={1}),
        codex=CodexConfig(
            binary="codex",
            default_working_dir=root,
            allow_runtime_full_access=True,
        ),
        gateway=GatewayConfig(state_dir=root / "state"),
        config_path=root / "config.json",
    )


class TerminalShellTests(unittest.TestCase):
    def test_completion_includes_slash_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell = TerminalShell(make_config(tmp))

            completions = shell._completion_options("/s", "/s")

            self.assertIn("/status ", completions)
            self.assertIn("/sessions ", completions)

    def test_new_session_uses_current_cli_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            shell = TerminalShell(make_config(tmp))
            work = Path(tmp) / "project"
            work.mkdir()

            response = shell.new_session([str(work)])

            self.assertIn("New Session", response)
            self.assertEqual(shell.session.working_dir, str(work.resolve()))
            self.assertEqual(shell.session.scope_key, "cli:default")

    def test_profile_switch_updates_cli_active_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shell = TerminalShell(make_config(tmp))
            shell.new_session([str(root)])
            profile_home = root / "profile"
            make_fake_auth(profile_home, "dev@example.com", "Dev")
            profile = shell.store.register_profile_from_home(profile_home)

            response = shell.profile_switch(profile.id)

            self.assertIn("Profile Switched", response)
            self.assertEqual(shell.active_profile().id, profile.id)
            self.assertEqual(shell.session.profile_id, profile.id)

    def test_file_change_tracker_reports_new_modified_and_deleted_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
            tracked = root / "tracked.txt"
            deleted = root / "deleted.txt"
            tracked.write_text("old\n", encoding="utf-8")
            deleted.write_text("gone\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)

            tracker = FileChangeTracker(root)
            before = tracker.snapshot()

            (root / "new.txt").write_text("a\nb\n", encoding="utf-8")
            tracked.write_text("old\nnew\n", encoding="utf-8")
            deleted.unlink()

            changes = {item.path: item for item in tracker.changes_since(before)}

            self.assertEqual(changes["new.txt"].kind, "add")
            self.assertEqual(changes["new.txt"].added, 2)
            self.assertEqual(changes["tracked.txt"].kind, "edit")
            self.assertEqual(changes["tracked.txt"].added, 1)
            self.assertEqual(changes["deleted.txt"].kind, "delete")
            self.assertEqual(changes["deleted.txt"].deleted, 1)


if __name__ == "__main__":
    unittest.main()
