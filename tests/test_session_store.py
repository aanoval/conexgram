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


if __name__ == "__main__":
    unittest.main()
