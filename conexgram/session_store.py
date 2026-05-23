"""Persistent session state for Telegram and Codex threads."""

from __future__ import annotations

import base64
import json
import re
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Optional

from .paths import DEFAULT_PROFILE_ROOT, ensure_dir, expand_path


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _decode_jwt_payload(value: str) -> dict[str, object]:
    parts = value.split(".")
    if len(parts) < 2 or not parts[1]:
        return {}
    payload = parts[1].replace("-", "+").replace("_", "/")
    while len(payload) % 4:
        payload += "="
    try:
        raw = base64.b64decode(payload.encode("utf-8"))
    except Exception:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _extract_profile_identity(auth_dir: Path) -> tuple[str, str]:
    auth_path = auth_dir / ".codex" / "auth.json"
    if not auth_path.exists():
        return "local", "local"

    try:
        raw = json.loads(auth_path.read_text(encoding="utf-8"))
    except Exception:
        return "local", "local"

    tokens = raw.get("tokens") if isinstance(raw, dict) else None
    if not isinstance(tokens, dict):
        return "local", "local"

    claims = _decode_jwt_payload(str(tokens.get("id_token", "")))
    email = str(claims.get("email") or "local")
    name = str(claims.get("name") or email.split("@", 1)[0] or "local")
    return email, name


def _profile_id_from_email(email: str) -> str:
    local = email.split("@", 1)[0].lower() if "@" in email else email.lower()
    safe = re.sub(r"[^a-z0-9]+", "-", local).strip("-")
    return safe or "profile"


@dataclass
class Session:
    id: str
    scope_key: str
    chat_id: int
    user_id: int
    working_dir: str
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    mode: str = "safe"
    fast_mode: bool = False
    full_access: Optional[bool] = None
    typing_indicator: Optional[bool] = None
    progress_messages: Optional[bool] = None
    codex_thread_id: Optional[str] = None
    title: str = "Untitled session"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    last_message_at: Optional[str] = None
    turn_count: int = 0
    profile_id: Optional[str] = None


@dataclass
class CodexProfile:
    id: str
    email: str
    display_name: str
    home_dir: str
    last_seen_at: Optional[str] = None
    last_switched_at: Optional[str] = None
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)


@dataclass
class PendingInvite:
    code: str
    owner_user_id: Optional[int]
    owner_chat_id: Optional[int]
    created_at: str
    expires_at: str


@dataclass
class ConnectedUser:
    user_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    chat_ids: list[int] = field(default_factory=list)
    last_chat_id: Optional[int] = None
    last_seen_at: Optional[str] = None


class SessionStore:
    def __init__(self, path: Path, profile_root: Path = DEFAULT_PROFILE_ROOT) -> None:
        self.path = expand_path(path)
        ensure_dir(self.path.parent)
        self._lock = threading.RLock()
        self.active_by_scope: dict[str, str] = {}
        self.sessions: dict[str, Session] = {}
        self.active_profile_by_scope: dict[str, str] = {}
        self.pending_invites: dict[str, PendingInvite] = {}
        self.profiles: dict[str, CodexProfile] = {}
        self.connected_users: dict[int, ConnectedUser] = {}
        self.profile_root = expand_path(profile_root)
        ensure_dir(self.profile_root)
        self.update_offset: Optional[int] = None
        self.last_profile_switch_by_scope: dict[str, str] = {}
        self.load()
        self.ensure_default_profile()

    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                return
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.active_by_scope = dict(raw.get("active_by_scope", {}))
            self.active_profile_by_scope = dict(raw.get("active_profile_by_scope", {}))
            self.last_profile_switch_by_scope = {
                str(key): str(value)
                for key, value in raw.get("last_profile_switch_by_scope", {}).items()
            }
            self.update_offset = raw.get("update_offset")
            self.profiles = {
                profile_id: CodexProfile(**data)
                for profile_id, data in raw.get("profiles", {}).items()
            }
            self.sessions = {
                sid: Session(**{**data, "profile_id": data.get("profile_id")})
                for sid, data in raw.get("sessions", {}).items()
            }
            self.pending_invites = {
                code: PendingInvite(**value)
                for code, value in raw.get("pending_invites", {}).items()
            }
            self.connected_users = {
                int(user_id): ConnectedUser(**{
                    "user_id": int(user_id),
                    "chat_ids": list(data.get("chat_ids") or []),
                    "last_chat_id": data.get("last_chat_id"),
                    "last_seen_at": data.get("last_seen_at"),
                    "username": data.get("username"),
                    "first_name": data.get("first_name"),
                    "last_name": data.get("last_name"),
                })
                for user_id, data in raw.get("connected_users", {}).items()
            }
            self._scan_profile_root()

    def save(self) -> None:
        with self._lock:
            data = {
                "update_offset": self.update_offset,
                "active_by_scope": self.active_by_scope,
                "active_profile_by_scope": self.active_profile_by_scope,
                "last_profile_switch_by_scope": self.last_profile_switch_by_scope,
                "profiles": {pid: asdict(profile) for pid, profile in self.profiles.items()},
                "sessions": {sid: asdict(session) for sid, session in self.sessions.items()},
                "pending_invites": {
                    code: asdict(invite) for code, invite in self.pending_invites.items()
                },
                "connected_users": {
                    str(user_id): asdict(user)
                    for user_id, user in self.connected_users.items()
                },
            }
            tmp = self.path.with_name(f"{self.path.name}.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(self.path)
            self.path.chmod(0o600)

    def record_user_identity(
        self,
        user_id: int,
        chat_id: int,
        username: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> None:
        with self._lock:
            user = self.connected_users.get(user_id)
            if user is None:
                user = ConnectedUser(user_id=user_id)
                self.connected_users[user_id] = user
            if username is not None:
                user.username = username
            if first_name is not None:
                user.first_name = first_name
            if last_name is not None:
                user.last_name = last_name
            if chat_id not in user.chat_ids:
                user.chat_ids.append(chat_id)
            user.last_chat_id = chat_id
            user.last_seen_at = now_iso()
            self.save()

    def get_connected_user(self, user_id: int) -> Optional[ConnectedUser]:
        with self._lock:
            return self.connected_users.get(user_id)

    def list_connected_users(self) -> list[ConnectedUser]:
        with self._lock:
            return sorted(
                self.connected_users.values(),
                key=lambda item: (
                    (item.last_name or "").lower(),
                    (item.first_name or "").lower(),
                    (item.username or "").lower(),
                    str(item.user_id),
                ),
            )

    def set_update_offset(self, offset: int) -> None:
        with self._lock:
            self.update_offset = offset
            self.save()

    def get_active(self, scope_key: str) -> Optional[Session]:
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

    def _default_profile_id(self) -> str:
        if not self.profiles:
            raise RuntimeError("No Codex profile configured.")
        return sorted(self.profiles)[0]

    def ensure_default_profile(self) -> None:
        if not self.profiles:
            email, display = _extract_profile_identity(Path.home())
            self.register_profile(
                email=email,
                display_name=display,
                home_dir=Path.home(),
            )

        default_profile_id = self._default_profile_id()
        needs_save = False
        for session in self.sessions.values():
            if not session.profile_id:
                session.profile_id = default_profile_id
                needs_save = True
        self.active_profile_by_scope = {
            scope_key: profile_id
            for scope_key, profile_id in self.active_profile_by_scope.items()
            if profile_id in self.profiles
        }
        for scope_key in list(self.active_by_scope.keys()):
            if scope_key not in self.active_profile_by_scope:
                self.active_profile_by_scope[scope_key] = default_profile_id
                needs_save = True
        if needs_save or not self.profiles.get(default_profile_id):
            self.save()

    def _scan_profile_root(self) -> None:
        if not self.profile_root.exists():
            return
        for item in self.profile_root.iterdir():
            if not item.is_dir():
                continue
            email, display = _extract_profile_identity(item)
            if email == "local":
                continue
            self.register_profile(
                email=email,
                display_name=display,
                home_dir=item,
            )

    def register_profile_from_home(
        self,
        home_dir: Path,
        profile_id: Optional[str] = None,
    ) -> CodexProfile:
        email, display = _extract_profile_identity(home_dir)
        if email == "local":
            raise ValueError(f"Codex auth not found at {home_dir / '.codex' / 'auth.json'}")
        return self.register_profile(
            email=email,
            display_name=display,
            home_dir=home_dir,
            profile_id=profile_id,
        )

    def register_profile(
        self,
        email: str,
        home_dir: Path,
        display_name: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> CodexProfile:
        normalized_email = (email or "").strip().lower()
        target_home = expand_path(home_dir)
        display = (display_name or normalized_email.split("@", 1)[0] or normalized_email or "profile").strip()
        existing_profile = next(
            (
                item
                for item in self.profiles.values()
                if item.email.lower() == normalized_email and Path(item.home_dir) == target_home
            ),
            None,
        )
        if existing_profile:
            return existing_profile

        requested_id = profile_id or _profile_id_from_email(normalized_email)
        if not requested_id:
            requested_id = "profile"
        candidate = requested_id
        counter = 1
        existing_ids = set(self.profiles)
        while candidate in existing_ids:
            counter += 1
            candidate = f"{requested_id}-{counter}"

        profile = CodexProfile(
            id=candidate,
            email=normalized_email,
            display_name=display,
            home_dir=str(target_home),
        )
        self.profiles[candidate] = profile
        self.save()
        return profile

    def list_profiles(self) -> list[CodexProfile]:
        with self._lock:
            return sorted(self.profiles.values(), key=lambda item: item.created_at)

    def get_profile(self, profile_id: Optional[str]) -> Optional[CodexProfile]:
        if not profile_id:
            return None
        return self.profiles.get(profile_id)

    def active_profile_id(self, scope_key: str) -> str:
        active_profile_id = self.active_profile_by_scope.get(scope_key)
        if active_profile_id and active_profile_id in self.profiles:
            return active_profile_id
        default_profile_id = self._default_profile_id()
        self.active_profile_by_scope[scope_key] = default_profile_id
        profile = self.profiles[default_profile_id]
        profile.last_seen_at = now_iso()
        self.save()
        return default_profile_id

    def active_profile(self, scope_key: str) -> CodexProfile:
        return self.profiles[self.active_profile_id(scope_key)]

    def set_active_profile(self, scope_key: str, profile_id: str) -> CodexProfile:
        profile = self.profiles.get(profile_id)
        if profile is None:
            raise KeyError(f"Unknown profile: {profile_id}")
        self.active_profile_by_scope[scope_key] = profile.id
        profile.last_switched_at = now_iso()
        profile.last_seen_at = now_iso()
        self.save()
        return profile

    def profile_switch_cooldown_remaining(self, scope_key: str, cooldown_seconds: int) -> int:
        raw = self.last_profile_switch_by_scope.get(scope_key)
        if not raw:
            return 0
        try:
            last = datetime.fromisoformat(raw)
        except ValueError:
            return 0
        elapsed = (datetime.now(UTC) - last).total_seconds()
        remain = int(cooldown_seconds - elapsed)
        return max(0, remain)

    def mark_profile_switch(self, scope_key: str) -> None:
        self.last_profile_switch_by_scope[scope_key] = now_iso()
        self.save()

    def find_profile(self, selector: str) -> Optional[CodexProfile]:
        selector = selector.strip().lower()
        if not selector:
            return None

        direct = self.profiles.get(selector)
        if direct:
            return direct
        for profile in self.profiles.values():
            if profile.id.lower().startswith(selector):
                return profile
        for profile in self.profiles.values():
            if profile.email.lower() == selector:
                return profile
        for profile in self.profiles.values():
            if profile.email.lower().startswith(selector):
                return profile
        for profile in self.profiles.values():
            if selector in profile.display_name.lower():
                return profile
        return None

    def create(
        self,
        scope_key: str,
        chat_id: int,
        user_id: int,
        working_dir: Path,
        model: Optional[str],
        reasoning_effort: Optional[str] = None,
        mode: str = "safe",
        fast_mode: bool = False,
        full_access: Optional[bool] = None,
        title: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> Session:
        with self._lock:
            active_profile_id = profile_id or self.active_profile_id(scope_key)
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
                profile_id=active_profile_id,
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

    def find_for_scope(self, scope_key: str, selector: str) -> Optional[Session]:
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

    def clear_threads_for_profile(
        self,
        scope_key: str,
        profile_id: str,
        keep_session_id: Optional[str] = None,
    ) -> list[str]:
        with self._lock:
            changed = False
            affected: list[str] = []
            for session in self.list_for_scope(scope_key):
                if session.id == keep_session_id:
                    continue
                if session.profile_id == profile_id:
                    continue
                if session.codex_thread_id is not None:
                    session.codex_thread_id = None
                    affected.append(session.id)
                    changed = True
            if changed:
                self.save()
            return affected

    def generate_invite_code(
        self,
        owner_user_id: Optional[int],
        owner_chat_id: Optional[int],
        ttl_seconds: int = 5 * 60,
    ) -> str:
        """Create a one-time invite code with expiry."""
        expires_at = (datetime.now(UTC) + timedelta(seconds=ttl_seconds)).isoformat(timespec="seconds")
        code = _random_invite_code(6)
        while code in self.pending_invites:
            code = _random_invite_code(6)
        self.pending_invites[code] = PendingInvite(
            code=code,
            owner_user_id=owner_user_id,
            owner_chat_id=owner_chat_id,
            created_at=now_iso(),
            expires_at=expires_at,
        )
        self.save()
        return code

    def consume_invite_code(self, raw_code: str) -> bool:
        code = _normalize_code(raw_code)
        invite = self.pending_invites.get(code)
        if invite is None:
            return False

        try:
            expires_at = datetime.fromisoformat(invite.expires_at)
        except ValueError:
            self.pending_invites.pop(code, None)
            self.save()
            return False

        if datetime.now(UTC) > expires_at:
            self.pending_invites.pop(code, None)
            self.save()
            return False

        self.pending_invites.pop(code, None)
        self.save()
        return True


def _normalize_code(value: str) -> str:
    return "".join(ch for ch in value.strip().upper() if ch.isalnum())


def _random_invite_code(length: int) -> str:
    import secrets
    import string

    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
