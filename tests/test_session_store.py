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


if __name__ == "__main__":
    unittest.main()
