"""Path helpers."""

from __future__ import annotations

from pathlib import Path


DEFAULT_STATE_DIR = Path.home() / ".conexgram"
DEFAULT_CONFIG_PATH = DEFAULT_STATE_DIR / "config.json"


def expand_path(value: str | Path) -> Path:
    """Expand a user-provided filesystem path."""
    return Path(value).expanduser().resolve()


def ensure_dir(path: Path) -> Path:
    """Create a directory if needed and return it."""
    path.mkdir(parents=True, exist_ok=True)
    return path
