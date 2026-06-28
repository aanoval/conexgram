"""Read local Codex thread metadata."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class CodexWorkspace:
    cwd: str
    thread_count: int
    total_tokens: int
    updated_at: int


@dataclass(frozen=True)
class CodexThread:
    id: str
    cwd: str
    title: str
    tokens_used: int
    updated_at: int
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    preview: str = ""


class CodexIndex:
    def __init__(self, profile_home: Path) -> None:
        self.profile_home = profile_home
        self.db_path = self._find_state_db(profile_home)

    @staticmethod
    def _find_state_db(profile_home: Path) -> Optional[Path]:
        roots = [
            profile_home / ".codex",
            Path.home() / ".codex",
        ]
        candidates: list[Path] = []
        for root in roots:
            if root.exists():
                candidates.extend(root.glob("state_*.sqlite"))
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.stat().st_mtime)

    def available(self) -> bool:
        return self.db_path is not None and self.db_path.exists()

    def list_workspaces(self, limit: int = 20) -> list[CodexWorkspace]:
        if not self.available():
            return []
        query = """
            SELECT cwd, COUNT(*) AS thread_count, COALESCE(SUM(tokens_used), 0) AS total_tokens,
                   MAX(updated_at) AS updated_at
            FROM threads
            WHERE archived = 0
            GROUP BY cwd
            ORDER BY MAX(updated_at) DESC
            LIMIT ?
        """
        try:
            with closing(self._connect()) as db:
                rows = db.execute(query, (limit,)).fetchall()
        except sqlite3.Error:
            return []
        return [
            CodexWorkspace(
                cwd=str(row["cwd"]),
                thread_count=int(row["thread_count"] or 0),
                total_tokens=int(row["total_tokens"] or 0),
                updated_at=int(row["updated_at"] or 0),
            )
            for row in rows
        ]

    def list_threads(self, cwd: str, limit: int = 20) -> list[CodexThread]:
        if not self.available():
            return []
        query = """
            SELECT id, cwd, title, tokens_used, updated_at, model, reasoning_effort, preview
            FROM threads
            WHERE archived = 0 AND cwd = ?
            ORDER BY updated_at DESC
            LIMIT ?
        """
        try:
            with closing(self._connect()) as db:
                rows = db.execute(query, (cwd, limit)).fetchall()
        except sqlite3.Error:
            return []
        return [self._thread_from_row(row) for row in rows]

    def find_thread(self, thread_id: str) -> Optional[CodexThread]:
        if not self.available():
            return None
        query = """
            SELECT id, cwd, title, tokens_used, updated_at, model, reasoning_effort, preview
            FROM threads
            WHERE id = ?
            LIMIT 1
        """
        try:
            with closing(self._connect()) as db:
                row = db.execute(query, (thread_id,)).fetchone()
        except sqlite3.Error:
            return None
        return self._thread_from_row(row) if row is not None else None

    def _connect(self) -> sqlite3.Connection:
        if self.db_path is None:
            raise sqlite3.OperationalError("Codex state database not found")
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=1.0)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _thread_from_row(row: sqlite3.Row) -> CodexThread:
        return CodexThread(
            id=str(row["id"]),
            cwd=str(row["cwd"]),
            title=str(row["title"] or "Untitled Codex thread"),
            tokens_used=int(row["tokens_used"] or 0),
            updated_at=int(row["updated_at"] or 0),
            model=str(row["model"]) if row["model"] else None,
            reasoning_effort=str(row["reasoning_effort"]) if row["reasoning_effort"] else None,
            preview=str(row["preview"] or ""),
        )
