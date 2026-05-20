"""Persistent session state for Telegram and Codex threads."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from .paths import ensure_dir, expand_path


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Session:
    id: str
    scope_key: str
    chat_id: int
    user_id: int
    working_dir: str
    model: str | None = None
    reasoning_effort: str | None = None
    mode: str = "safe"
    fast_mode: bool = False
    full_access: bool | None = None
    typing_indicator: bool | None = None
    progress_messages: bool | None = None
    codex_thread_id: str | None = None
    title: str = "Untitled session"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    last_message_at: str | None = None
    turn_count: int = 0


class SessionStore:
    def __init__(self, path: Path) -> None:
        self.path = expand_path(path)
        ensure_dir(self.path.parent)
        self._lock = threading.RLock()
        self.active_by_scope: dict[str, str] = {}
        self.sessions: dict[str, Session] = {}
        self.update_offset: int | None = None
        self.load()

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.active_by_scope = dict(raw.get("active_by_scope", {}))
            self.update_offset = raw.get("update_offset")
            self.sessions = {
                sid: Session(**data) for sid, data in raw.get("sessions", {}).items()
            }

    def save(self) -> None:
        with self._lock:
            data = {
                "update_offset": self.update_offset,
                "active_by_scope": self.active_by_scope,
                "sessions": {sid: asdict(session) for sid, session in self.sessions.items()},
            }
            tmp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self.path.chmod(0o600)

    def set_update_offset(self, offset: int) -> None:
        with self._lock:
            self.update_offset = offset
            self.save()

    def get_active(self, scope_key: str) -> Session | None:
        with self._lock:
            session_id = self.active_by_scope.get(scope_key)
            if not session_id:
                return None
            return self.sessions.get(session_id)

    def set_active(self, scope_key: str, session_id: str) -> Session:
        with self._lock:
            if session_id not in self.sessions:
                raise KeyError(f"Unknown session: {session_id}")
            self.active_by_scope[scope_key] = session_id
            self.save()
            return self.sessions[session_id]

    def create(
        self,
        scope_key: str,
        chat_id: int,
        user_id: int,
        working_dir: Path,
        model: str | None,
        reasoning_effort: str | None = None,
        mode: str = "safe",
        fast_mode: bool = False,
        full_access: bool | None = None,
        title: str | None = None,
    ) -> Session:
        with self._lock:
            session = Session(
                id=str(uuid.uuid4()),
                scope_key=scope_key,
                chat_id=chat_id,
                user_id=user_id,
                working_dir=str(expand_path(working_dir)),
                model=model,
                reasoning_effort=reasoning_effort,
                mode=mode,
                fast_mode=fast_mode,
                full_access=full_access,
                title=title or "Fresh Codex session",
            )
            self.sessions[session.id] = session
            self.active_by_scope[scope_key] = session.id
            self.save()
            return session

    def update(self, session: Session) -> None:
        with self._lock:
            session.updated_at = now_iso()
            self.sessions[session.id] = session
            self.save()

    def list_for_scope(self, scope_key: str) -> list[Session]:
        with self._lock:
            items = [s for s in self.sessions.values() if s.scope_key == scope_key]
            return sorted(items, key=lambda s: s.updated_at, reverse=True)

    def find_for_scope(self, scope_key: str, selector: str) -> Session | None:
        selector = selector.strip()
        scoped = self.list_for_scope(scope_key)
        if selector.isdigit():
            index = int(selector) - 1
            if 0 <= index < len(scoped):
                return scoped[index]
        for session in scoped:
            if session.id == selector or session.id.startswith(selector):
                return session
        return None
