import tempfile
import unittest
from pathlib import Path

from conexgram.paths import workspace_access_error


class WorkspaceAccessTests(unittest.TestCase):
    def test_accessible_workspace_passes_preflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(workspace_access_error(Path(tmp), timeout_seconds=2))

    def test_missing_workspace_returns_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing"

            error = workspace_access_error(missing, timeout_seconds=2)

            self.assertIsNotNone(error)
            assert error is not None
            self.assertIn("Workspace is not accessible", error)


if __name__ == "__main__":
    unittest.main()
