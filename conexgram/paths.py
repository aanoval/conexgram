"""Path helpers."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Union


DEFAULT_STATE_DIR = Path.home() / ".conexgram"
DEFAULT_CONFIG_PATH = DEFAULT_STATE_DIR / "config.json"
DEFAULT_PROFILE_ROOT = Path.home() / ".codex-profiles"


def expand_path(value: Union[str, Path]) -> Path:
    """Expand a user-provided filesystem path."""
    return Path(value).expanduser().resolve()


def ensure_dir(path: Path) -> Path:
    """Create a directory if needed and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path


def workspace_access_error(path: Union[str, Path], timeout_seconds: int = 10) -> str | None:
    """Return a user-facing error when a workspace can't be opened promptly."""
    candidate = Path(path).expanduser()
    probe = (
        "import os,sys; "
        "path=sys.argv[1]; "
        "os.chdir(path); "
        "next(iter(os.scandir('.')), None); "
        "os.getcwd()"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", probe, str(candidate)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(1, timeout_seconds),
            check=False,
            cwd=str(Path.home()),
        )
    except subprocess.TimeoutExpired:
        return f"Workspace access timed out after {timeout_seconds}s: {candidate}"
    except OSError as exc:
        return f"Workspace access check failed for {candidate}: {exc}"
    if result.returncode != 0:
        detail = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "not accessible"
        return f"Workspace is not accessible: {candidate} ({detail})"
    return None
