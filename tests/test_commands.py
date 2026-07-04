import base64
import json
import io
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from conexgram.commands import (
    CommandHandler,
    FileCommandResponse,
    MessageCommandResponse,
    ProfileCommandResponse,
)
from conexgram.config import AppConfig, CodexConfig, GatewayConfig, TelegramConfig
from conexgram.session_store import SessionStore


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


def make_handler(tmp: str, max_upload_bytes: int = 1024) -> CommandHandler:
    root = Path(tmp)
    config = AppConfig(
        telegram=TelegramConfig(
            bot_token="token",
            allowed_user_ids={2},
            owner_user_id=2,
            owner_chat_id=1,
        ),
        codex=CodexConfig(
            binary="codex",
            default_working_dir=root,
            allow_runtime_full_access=True,
            presets={"computer": {"mode": "full", "full_access": True}},
        ),
        gateway=GatewayConfig(state_dir=root / "state", max_upload_bytes=max_upload_bytes),
        config_path=root / "config.json",
    )
    return CommandHandler(config, SessionStore(root / "sessions.json"))


def make_codex_state(profile_home: Path, rows: list[dict]) -> Path:
    codex_dir = profile_home / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    db_path = codex_dir / "state_5.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            CREATE TABLE threads (
                id TEXT PRIMARY KEY,
                cwd TEXT NOT NULL,
                title TEXT NOT NULL,
                tokens_used INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,
                model TEXT,
                reasoning_effort TEXT,
                preview TEXT NOT NULL DEFAULT ''
            )
            """
        )
        for row in rows:
            db.execute(
                """
                INSERT INTO threads (
                    id, cwd, title, tokens_used, updated_at, archived,
                    model, reasoning_effort, preview
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["id"],
                    row["cwd"],
                    row["title"],
                    row.get("tokens_used", 0),
                    row.get("updated_at", int(time.time())),
                    row.get("archived", 0),
                    row.get("model"),
                    row.get("reasoning_effort"),
                    row.get("preview", ""),
                ),
            )
    return db_path


class CommandHandlerTests(unittest.TestCase):
    def test_sendfile_returns_file_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "artifact.txt"
            path.write_text("hello", encoding="utf-8")
            handler = make_handler(tmp)

            response = handler.handle_command(f'/sendfile "{path}" sample caption', 1, 2)

            self.assertIsInstance(response, FileCommandResponse)
            assert isinstance(response, FileCommandResponse)
            self.assertEqual(response.path, path.resolve())
            self.assertEqual(response.caption, "sample caption")

    def test_sendfile_resolves_relative_path_from_session_working_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "project"
            work.mkdir()
            path = work / "artifact.txt"
            path.write_text("hello", encoding="utf-8")
            handler = make_handler(tmp)
            handler.ensure_session(1, 2).working_dir = str(work)

            response = handler.handle_command("/sendfile artifact.txt", 1, 2)

            self.assertIsInstance(response, FileCommandResponse)
            assert isinstance(response, FileCommandResponse)
            self.assertEqual(response.path, path.resolve())

    def test_sendfile_rejects_large_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.bin"
            path.write_bytes(b"abcd")
            handler = make_handler(tmp, max_upload_bytes=3)

            response = handler.handle_command(f'/sendfile "{path}"', 1, 2)

            self.assertIsInstance(response, str)
            self.assertIn("File too large", response)

    def test_computer_access_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/computer on", 1, 2)

            self.assertIsInstance(response, str)
            self.assertIn("/confirm computer", response)

    def test_confirm_computer_enables_session_full_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/confirm computer", 1, 2)
            session = handler.ensure_session(1, 2)

            self.assertEqual(response, "Computer Access enabled for this session.")
            self.assertTrue(session.full_access)
            self.assertEqual(session.mode, "full")
            self.assertEqual(session.sandbox_mode, "danger-full-access")

    def test_sandbox_menu_and_workspace_write_update_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            menu = handler.handle_command("/sandbox", 1, 2)
            response = handler.handle_command("/sandbox workspace-write", 1, 2)
            session = handler.ensure_session(1, 2)

            self.assertIsInstance(menu, MessageCommandResponse)
            assert isinstance(menu, MessageCommandResponse)
            self.assertIn("Sandbox:", menu.text)
            self.assertEqual(response, "Sandbox updated: workspace-write")
            self.assertEqual(session.sandbox_mode, "workspace-write")
            self.assertEqual(session.mode, "workspace")
            self.assertFalse(session.full_access)

    def test_sandbox_danger_full_access_requires_confirmation(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/sandbox danger-full-access", 1, 2)
            session = handler.ensure_session(1, 2)

            self.assertIn("/confirm sandbox", response)
            self.assertIsNone(session.sandbox_mode)

            confirmed = handler.handle_command("/confirm sandbox", 1, 2)

            self.assertEqual(confirmed, "Sandbox updated: danger-full-access for this session.")
            self.assertEqual(session.sandbox_mode, "danger-full-access")
            self.assertTrue(session.full_access)
            self.assertEqual(session.mode, "full")

    def test_approval_menu_and_policy_update_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            menu = handler.handle_command("/approval", 1, 2)
            response = handler.handle_command("/approval on-request", 1, 2)
            session = handler.ensure_session(1, 2)

            self.assertIsInstance(menu, MessageCommandResponse)
            assert isinstance(menu, MessageCommandResponse)
            self.assertIn("Approval policy:", menu.text)
            self.assertEqual(response, "Approval policy updated: on-request")
            self.assertEqual(session.approval_policy, "on-request")

    def test_settings_returns_inline_keyboard(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/settings", 1, 2)

            self.assertIsInstance(response, MessageCommandResponse)
            assert isinstance(response, MessageCommandResponse)
            self.assertIn("Settings:", response.text)
            self.assertIn("Sandbox:", response.text)
            self.assertIn("Approval:", response.text)
            self.assertIsNotNone(response.reply_markup)

    def test_help_returns_interactive_menu(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/help", 1, 2)

            self.assertIsInstance(response, MessageCommandResponse)
            assert isinstance(response, MessageCommandResponse)
            self.assertIn("Conexgram command menu", response.text)
            self.assertEqual(
                response.reply_markup["inline_keyboard"][0][0]["callback_data"],
                "/help session",
            )

    def test_help_category_returns_command_buttons(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/help model", 1, 2)

            self.assertIsInstance(response, MessageCommandResponse)
            assert isinstance(response, MessageCommandResponse)
            self.assertIn("Model & Mode commands:", response.text)
            self.assertIn("/settings", response.text)
            self.assertEqual(
                response.reply_markup["inline_keyboard"][0][0]["callback_data"],
                "/settings",
            )
            self.assertEqual(
                response.reply_markup["inline_keyboard"][0][0]["text"],
                "Settings",
            )

    def test_menu_alias_returns_interactive_help(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/menu", 1, 2)

            self.assertIsInstance(response, MessageCommandResponse)
            assert isinstance(response, MessageCommandResponse)
            self.assertIn("Conexgram command menu", response.text)
            self.assertIsNotNone(response.reply_markup)

    def test_help_command_detail_does_not_run_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/help cmd reset", 1, 2)

            self.assertIsInstance(response, MessageCommandResponse)
            assert isinstance(response, MessageCommandResponse)
            self.assertIn("/reset", response.text)
            self.assertEqual(len(handler.store.list_for_scope(handler.scope_key(1, 2))), 0)

    def test_sessions_browses_codex_workspaces_and_switches_thread(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "project-a"
            work.mkdir()
            profile_home = root / "profile-a"
            make_fake_auth(profile_home, "dev@example.com", "Dev")
            make_codex_state(
                profile_home,
                [
                    {
                        "id": "019thread-a",
                        "cwd": str(work),
                        "title": "Build project A",
                        "tokens_used": 12345,
                        "updated_at": 1800000000,
                        "model": "gpt-test",
                        "reasoning_effort": "high",
                    }
                ],
            )
            handler = make_handler(tmp)
            profile = handler.store.register_profile_from_home(profile_home)
            handler.store.set_active_profile(handler.scope_key(1, 2), profile.id)

            workspaces = handler.handle_command("/sessions", 1, 2)

            self.assertIsInstance(workspaces, MessageCommandResponse)
            assert isinstance(workspaces, MessageCommandResponse)
            self.assertIn("Choose a Codex workspace", workspaces.text)
            self.assertIn("project-a", workspaces.reply_markup["inline_keyboard"][0][0]["text"])
            self.assertEqual(
                workspaces.reply_markup["inline_keyboard"][0][0]["callback_data"],
                "/sessions 1",
            )

            threads = handler.handle_command("/sessions 1", 1, 2)

            self.assertIsInstance(threads, MessageCommandResponse)
            assert isinstance(threads, MessageCommandResponse)
            self.assertIn("Build project A", threads.reply_markup["inline_keyboard"][0][0]["text"])
            self.assertEqual(
                threads.reply_markup["inline_keyboard"][0][0]["callback_data"],
                "/switch codex 019thread-a",
            )

            switched = handler.handle_command("/switch codex 019thread-a", 1, 2)
            session = handler.ensure_session(1, 2)

            self.assertIn("Switched to Codex thread 019thread-a", switched)
            self.assertEqual(session.codex_thread_id, "019thread-a")
            self.assertEqual(session.working_dir, str(work.resolve()))
            self.assertEqual(session.title, "Build project A")
            self.assertEqual(session.model, "gpt-test")
            self.assertEqual(session.reasoning_effort, "high")

    def test_codex_command_runs_native_binary_without_shell(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "fake-codex"
            script.write_text(
                "#!/usr/bin/env python3\n"
                "import sys\n"
                "print('ARGS=' + repr(sys.argv[1:]))\n",
                encoding="utf-8",
            )
            script.chmod(0o755)
            handler = make_handler(tmp)
            handler.config = AppConfig(
                telegram=handler.config.telegram,
                codex=CodexConfig(
                    binary=str(script),
                    default_working_dir=Path(tmp),
                    allow_runtime_full_access=True,
                ),
                gateway=handler.config.gateway,
                config_path=handler.config.config_path,
            )

            make_fake_auth(Path(tmp), "dev@example.com", "Dev")
            profile = handler.store.register_profile_from_home(Path(tmp))
            handler.store.set_active_profile(
                scope_key=handler.scope_key(1, 2), profile_id=profile.id
            )

            response = handler.handle_command('/codex debug "two words"', 1, 2)

            self.assertIsInstance(response, str)
            self.assertIn("ARGS=['debug', 'two words']", response)

    def test_codex_command_prompts_when_auth_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            missing_home = Path(tmp) / "missing-profile"
            missing_home.mkdir()
            missing_profile = handler.store.register_profile(
                email="missing-local@example.com",
                home_dir=missing_home,
                display_name="Missing Profile",
            )
            handler.store.set_active_profile(
                scope_key=handler.scope_key(1, 2), profile_id=missing_profile.id
            )

            response = handler.handle_command('/codex debug "two words"', 1, 2)

            self.assertIn("Codex auth not found", response)
            self.assertIn("/codexlogin", response)

    def test_format_codex_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            response = handler._format_codex_usage(
                {
                    "plan_type": "prolite",
                    "rate_limit": {
                        "allowed": True,
                        "limit_reached": False,
                        "primary_window": {
                            "used_percent": 16,
                            "limit_window_seconds": 18000,
                            "reset_after_seconds": 3600,
                        },
                        "secondary_window": {
                            "used_percent": 92,
                            "limit_window_seconds": 604800,
                            "reset_after_seconds": 86400,
                        },
                    },
                    "credits": {
                        "has_credits": False,
                        "unlimited": False,
                        "balance": "0",
                    },
                }
            )

            self.assertIn("Plan: prolite", response)
            self.assertIn("5h: 16% used", response)
            self.assertIn("weekly: 92% used", response)
            self.assertIn("Credits: balance 0, no credits", response)

    def test_extract_device_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            self.assertEqual(
                handler._extract_device_code("Open this page and enter code: abc-123-xyz"),
                "ABC123XYZ",
            )
            self.assertEqual(handler._extract_device_code("Verification code: X9Y8Z7"), "X9Y8Z7")

    def test_extract_device_code_ignores_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            self.assertIsNone(handler._extract_device_code("Welcome to Conexgram CLI"))
            self.assertIsNone(handler._extract_device_code("WELCOME"))
            self.assertIsNone(handler._extract_device_code("AUTHORIZATION"))

    def test_extract_device_code_with_ansi_and_dash(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            self.assertEqual(
                handler._extract_device_code(
                    "\x1b[90m   WNQ3-7KX1  \x1b[0m"
                ),
                "WNQ3-7KX1",
            )
            self.assertEqual(
                handler._extract_device_code(
                    "Open this URL:\n\x1b[94mhttps://auth.openai.com/codex/device\x1b[0m"
                ),
                None,
            )
            self.assertEqual(
                handler._extract_device_code(
                    "Open this link and enter code: HWVN-TI03A"
                ),
                "HWVN-TI03A",
            )

    def test_codexlogin_rejects_non_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)

            response = handler.handle_command("/codexlogin", 99, 3)
            self.assertEqual(response, "Only the owner can start Codex device auth.")

    def test_codexlogin_registers_new_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = make_handler(tmp)
            notifications: list[str] = []
            handler.set_notify_callback(lambda _, text: notifications.append(text))

            fake_profile_home = root / "login-home"
            fake_profile_home.mkdir()

            class FakePopen:
                def __init__(self, *args, **kwargs):
                    self.cwd = kwargs.get("cwd")
                    self.stdout = io.StringIO(
                        "Open this URL:\n"
                        "Verification code: ABC123\n"
                    )
                    self.return_code = 0

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def wait(self):
                    if self.cwd:
                        make_fake_auth(Path(self.cwd), "dev@example.com", "Dev")
                    return self.return_code

            with patch("conexgram.commands.subprocess.Popen", FakePopen), patch.object(
                handler,
                "_create_login_profile_home",
                return_value=fake_profile_home,
            ):
                response = handler.handle_command("/codexlogin", 1, 2)
                self.assertIn("Started Codex device-auth", response)

            for _ in range(50):
                if any("Profile registered and set as active." in item for item in notifications):
                    break
                time.sleep(0.05)

            self.assertTrue(any("Verification code: ABC123" in item for item in notifications))
            self.assertTrue(any("Profile registered and set as active." in item for item in notifications))

    def test_codexlogin_uses_next_line_after_prompt_and_skips_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = make_handler(tmp)
            notifications: list[str] = []
            handler.set_notify_callback(lambda _, text: notifications.append(text))

            fake_profile_home = root / "login-home"
            fake_profile_home.mkdir()

            class FakePopen:
                def __init__(self, *args, **kwargs):
                    self.cwd = kwargs.get("cwd")
                    self.stdout = io.StringIO(
                        "Open this page and enter code:\n"
                        "AUTHORIZATION\n"
                        "ABCD-12345\n"
                    )
                    self.return_code = 0

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return False

                def wait(self):
                    if self.cwd:
                        make_fake_auth(Path(self.cwd), "dev@example.com", "Dev")
                    return self.return_code

            with patch("conexgram.commands.subprocess.Popen", FakePopen), patch.object(
                handler,
                "_create_login_profile_home",
                return_value=fake_profile_home,
            ):
                response = handler.handle_command("/codexlogin", 1, 2)
                self.assertIn("Started Codex device-auth", response)

            for _ in range(50):
                if any("Profile registered and set as active." in item for item in notifications):
                    break
                time.sleep(0.05)

            output = "\n".join(notifications)
            self.assertIn("Open this page and enter code:", output)
            self.assertIn("AUTHORIZATION", output)
            self.assertIn("ABCD-12345", output)

    def test_profile_add_registers_and_lists_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = make_handler(tmp)
            profile_home = root / "alt-profile"
            make_fake_auth(profile_home, "alt@example.com", "Alternate")

            response = handler.handle_command(f"/profile add {profile_home}", 1, 2)
            self.assertIn("Profile added/updated.", response)

            response = handler.handle_command("/profile list", 1, 2)
            self.assertIn("alt", response)
            self.assertIn("alt@example.com", response)

    def test_profile_add_without_path_scans_default_profile_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            handler = make_handler(tmp)
            profile_root = root / ".codex-profiles"
            profile_home = profile_root / "auto-profile"
            make_fake_auth(profile_home, "auto@example.com", "Auto Profile")
            handler.store.profile_root = profile_root

            response = handler.handle_command("/profile add", 1, 2)

            self.assertIn("Profiles added/updated from the default Conexgram profile directory", response)
            self.assertIn("auto@example.com", response)
            self.assertIsNotNone(handler.store.find_profile("auto"))

    def test_profile_switch_clears_other_profile_threads(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            scope = handler.scope_key(1, 2)
            home = Path(tmp) / "next"
            make_fake_auth(home, "next@example.com", "Next Profile")
            add_response = handler.handle_command(f"/profile add {home}", 1, 2)
            self.assertIn("Profile added/updated.", add_response)

            target = handler.store.find_profile("next")
            assert target is not None

            session_other = handler.store.create(
                scope_key=scope,
                chat_id=1,
                user_id=2,
                working_dir=Path(tmp),
                model=None,
                reasoning_effort=None,
                mode="safe",
                fast_mode=False,
                title="other profile session",
                profile_id="other",
            )
            session_other.codex_thread_id = "thread-other"
            handler.store.update(session_other)
            handler.store.set_active(scope, session_other.id)

            session_target = handler.store.create(
                scope_key=scope,
                chat_id=1,
                user_id=2,
                working_dir=Path(tmp),
                model=None,
                reasoning_effort=None,
                mode="safe",
                fast_mode=False,
                title="target profile session",
                profile_id=target.id,
            )
            session_target.codex_thread_id = "thread-target"
            handler.store.update(session_target)

            response = handler.handle_command(f"/profile switch {target.id}", 1, 2)
            self.assertIsInstance(response, ProfileCommandResponse)
            assert isinstance(response, ProfileCommandResponse)
            self.assertIn(target.id, response.text)
            self.assertEqual(response.stop_session_ids, [session_other.id])

            session_other = handler.store.sessions[session_other.id]
            session_target = handler.store.sessions[session_target.id]
            self.assertIsNone(session_other.codex_thread_id)
            self.assertEqual(session_target.codex_thread_id, "thread-target")

            response_again = handler.handle_command(f"/profile switch {target.id}", 1, 2)
            self.assertIn("rate-limited", response_again)

    def test_invite_code_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            response = handler.handle_command("/invite", 1, 2)
            invite_code = next(
                (line.strip() for line in response.splitlines() if len(line.strip()) == 6),
                None,
            )
            self.assertIsNotNone(invite_code)
            self.assertNotIn(3, handler.config.telegram.allowed_user_ids)
            self.assertTrue(handler.claim_invite_if_valid(invite_code, 3, 30))
            self.assertIn(3, handler.config.telegram.allowed_user_ids)

    def test_non_owner_cannot_invite(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            response = handler.invite(99, 3, [])
            self.assertEqual(response, "Only the owner can generate invite codes.")

    def test_revoke_access(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            handler._authorize_user(user_id=5, chat_id=50)
            self.assertIn(5, handler.config.telegram.allowed_user_ids)

            response = handler.handle_command("/revoke 5", 1, 2)
            self.assertEqual(response, "Revoked access for 5.")
            self.assertNotIn(5, handler.config.telegram.allowed_user_ids)

    def test_owner_cannot_revoke_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            response = handler.handle_command("/revoke 2", 1, 2)
            self.assertEqual(response, "Owner cannot revoke itself.")

    def test_users_lists_connected_identities(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            handler.store.record_user_identity(
                user_id=2,
                chat_id=1,
                username="owner_telegram",
                first_name="Nunu",
                last_name="Admin",
            )
            handler.store.record_user_identity(
                user_id=5,
                chat_id=50,
                username="friend",
                first_name="Ada",
                last_name="User",
            )
            handler._authorize_user(user_id=5, chat_id=50)
            response = handler.handle_command("/users", 1, 2)

            self.assertIn("Connected users:", response)
            self.assertIn("Owner", response)
            self.assertIn("Nunu Admin", response)
            self.assertIn("friend", response)
            self.assertIn("ids: 5", response)

    def test_users_restricted_to_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            handler = make_handler(tmp)
            response = handler.handle_command("/users", 99, 3)
            self.assertEqual(response, "Only the owner can list connected users.")


if __name__ == "__main__":
    unittest.main()
