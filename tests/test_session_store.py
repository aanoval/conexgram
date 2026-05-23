import tempfile
import unittest
from pathlib import Path

from conexgram.session_store import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_create_and_reload_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            store = SessionStore(path)
            session = store.create(
                scope_key="chat:1",
                chat_id=1,
                user_id=2,
                working_dir=Path(tmp),
                model=None,
            )

            reloaded = SessionStore(path)
            active = reloaded.get_active("chat:1")
            self.assertIsNotNone(active)
            self.assertEqual(active.id, session.id)

    def test_generate_and_consume_invite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            store = SessionStore(path)

            code = store.generate_invite_code(owner_user_id=1, owner_chat_id=10)
            self.assertEqual(len(code), 6)

            self.assertTrue(store.consume_invite_code(code))
            self.assertFalse(store.consume_invite_code(code))

    def test_expired_invite_code_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            store = SessionStore(path)
            code = store.generate_invite_code(owner_user_id=1, owner_chat_id=10)
            store.pending_invites[code].expires_at = "2000-01-01T00:00:00+00:00"
            store.save()
            self.assertFalse(store.consume_invite_code(code))

    def test_record_user_identity_tracks_multiple_chats_and_updates_labels(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sessions.json"
            store = SessionStore(path)

            store.record_user_identity(
                user_id=7,
                chat_id=100,
                username="old_name",
                first_name="Alice",
                last_name="Ng",
            )
            store.record_user_identity(
                user_id=7,
                chat_id=101,
                first_name="Alice2",
                username=None,
            )

            reloaded = SessionStore(path)
            user = reloaded.get_connected_user(7)
            self.assertIsNotNone(user)
            assert user is not None
            self.assertEqual(user.username, "old_name")
            self.assertEqual(user.first_name, "Alice2")
            self.assertEqual(user.last_name, "Ng")
            self.assertEqual(user.chat_ids, [100, 101])


if __name__ == "__main__":
    unittest.main()
