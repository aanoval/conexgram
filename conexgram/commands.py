"""Telegram command handling."""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import AppConfig
from .session_store import Session, SessionStore


@dataclass(frozen=True)
class FileCommandResponse:
    path: Path
    caption: str | None = None


@dataclass(frozen=True)
class MessageCommandResponse:
    text: str
    reply_markup: dict | None = None


class CommandHandler:
    def __init__(self, config: AppConfig, store: SessionStore) -> None:
        self.config = config
        self.store = store

    def scope_key(self, chat_id: int, user_id: int) -> str:
        if self.config.gateway.session_scope == "user":
            return f"user:{user_id}"
        return f"chat:{chat_id}"

    def is_allowed(self, chat_id: int, user_id: int) -> bool:
        telegram = self.config.telegram
        return user_id in telegram.allowed_user_ids or chat_id in telegram.allowed_chat_ids

    def ensure_session(self, chat_id: int, user_id: int) -> Session:
        scope_key = self.scope_key(chat_id, user_id)
        session = self.store.get_active(scope_key)
        if session is not None:
            return session
        return self.store.create(
            scope_key=scope_key,
            chat_id=chat_id,
            user_id=user_id,
            working_dir=self.config.codex.default_working_dir,
            model=self.config.codex.model,
            reasoning_effort=self.config.codex.reasoning_effort,
            mode=self.config.codex.mode,
            fast_mode=self.config.codex.fast_mode,
        )

    def handle_command(
        self,
        text: str,
        chat_id: int,
        user_id: int,
    ) -> str | FileCommandResponse | MessageCommandResponse | None:
        if not text.startswith("/"):
            return None
        try:
            parts = shlex.split(text)
        except ValueError as exc:
            return f"Command parse error: {exc}"
        if not parts:
            return None
        command = parts[0].split("@", 1)[0].lower()
        args = parts[1:]

        if command in {"/start", "/help"}:
            return self.help_text()
        if command == "/new":
            return self.new_session(chat_id, user_id, args)
        if command == "/status":
            return self.status(chat_id, user_id)
        if command == "/sessions":
            return self.sessions(chat_id, user_id)
        if command == "/switch":
            return self.switch(chat_id, user_id, args)
        if command == "/cwd":
            return self.cwd(chat_id, user_id, args)
        if command == "/model":
            return self.model(chat_id, user_id, args)
        if command == "/models":
            return self.models()
        if command == "/reasoning":
            return self.reasoning(chat_id, user_id, args)
        if command == "/mode":
            return self.mode(chat_id, user_id, args)
        if command == "/preset":
            return self.preset(chat_id, user_id, args)
        if command == "/fast":
            return self.fast(chat_id, user_id, args)
        if command == "/fullaccess":
            return self.fullaccess(chat_id, user_id, args)
        if command == "/computer":
            return self.computer(chat_id, user_id, args)
        if command == "/confirm":
            return self.confirm(chat_id, user_id, args)
        if command == "/workspace":
            return self.workspace(chat_id, user_id, args)
        if command == "/permissions":
            return self.permissions(chat_id, user_id)
        if command == "/settings":
            return self.settings(chat_id, user_id)
        if command == "/typing":
            return self.toggle_session_bool(chat_id, user_id, args, "typing_indicator", "Typing indicator")
        if command == "/progress":
            return self.toggle_session_bool(chat_id, user_id, args, "progress_messages", "Progress messages")
        if command == "/silent":
            return self.silent(chat_id, user_id, args)
        if command == "/rename":
            return self.rename(chat_id, user_id, args)
        if command == "/summary":
            return self.summary(chat_id, user_id)
        if command == "/reset":
            return self.new_session(chat_id, user_id, [])
        if command == "/logs":
            return self.logs(args)
        if command == "/tail":
            return self.tail(chat_id, user_id)
        if command == "/doctor":
            return self.doctor()
        if command == "/version":
            return self.version_text()
        if command == "/config":
            return self.config_text()
        if command in {"/quota", "/codexstatus"}:
            return self.codex_usage()
        if command == "/codex":
            return self.codex_cli(args)
        if command == "/sendfile":
            return self.sendfile(chat_id, user_id, args)
        if command == "/stop":
            return "__STOP_CODEX__"
        return "Unknown command. Send /help for available commands."

    def new_session(self, chat_id: int, user_id: int, args: list[str]) -> str:
        working_dir = self.config.codex.default_working_dir
        if args:
            requested = Path(" ".join(args)).expanduser()
            working_dir = requested.resolve()
            if not working_dir.exists() or not working_dir.is_dir():
                return f"Working directory not found: {working_dir}"
            if not self._path_allowed(working_dir):
                return f"Working directory is outside configured workspace roots: {working_dir}"
        scope_key = self.scope_key(chat_id, user_id)
        session = self.store.create(
            scope_key=scope_key,
            chat_id=chat_id,
            user_id=user_id,
            working_dir=working_dir,
            model=self.config.codex.model,
            reasoning_effort=self.config.codex.reasoning_effort,
            mode=self.config.codex.mode,
            fast_mode=self.config.codex.fast_mode,
            title=f"Session in {working_dir.name}",
        )
        return (
            "New Codex session created.\n"
            f"Gateway session: {session.id}\n"
            f"Working directory: {session.working_dir}\n"
            "Context is fresh. Send a normal message to start a new Codex thread."
        )

    def status(self, chat_id: int, user_id: int) -> str:
        session = self.ensure_session(chat_id, user_id)
        thread = session.codex_thread_id or "not started yet"
        return (
            "Active session:\n"
            f"- Gateway session: {session.id}\n"
            f"- Codex thread: {thread}\n"
            f"- Working directory: {session.working_dir}\n"
            f"- Model: {session.model or 'Codex default'}\n"
            f"- Reasoning: {session.reasoning_effort or 'Codex default'}\n"
            f"- Mode: {session.mode}\n"
            f"- Fast mode: {'on' if session.fast_mode else 'off'}\n"
            f"- Typing indicator: {'on' if self._effective_bool(session.typing_indicator, self.config.progress.typing_indicator) else 'off'}\n"
            f"- Progress messages: {'on' if self._effective_bool(session.progress_messages, self.config.progress.progress_messages) else 'off'}\n"
            f"- Turns: {session.turn_count}\n"
            f"- Updated: {session.updated_at}"
        )

    def sessions(self, chat_id: int, user_id: int) -> str:
        scope_key = self.scope_key(chat_id, user_id)
        sessions = self.store.list_for_scope(scope_key)
        if not sessions:
            return "No sessions yet. Send /new to create one."
        active = self.store.get_active(scope_key)
        lines = ["Sessions:"]
        for index, session in enumerate(sessions[:20], start=1):
            marker = "*" if active and active.id == session.id else " "
            thread = "started" if session.codex_thread_id else "fresh"
            lines.append(
                f"{marker} {index}. {session.id[:8]} {thread} "
                f"turns={session.turn_count} cwd={session.working_dir}"
            )
        return "\n".join(lines)

    def switch(self, chat_id: int, user_id: int, args: list[str]) -> str:
        if not args:
            return "Usage: /switch <session-number-or-id>"
        scope_key = self.scope_key(chat_id, user_id)
        session = self.store.find_for_scope(scope_key, args[0])
        if session is None:
            return "Session not found. Use /sessions to list available sessions."
        self.store.set_active(scope_key, session.id)
        return f"Switched to session {session.id}\nWorking directory: {session.working_dir}"

    def cwd(self, chat_id: int, user_id: int, args: list[str]) -> str:
        session = self.ensure_session(chat_id, user_id)
        if not args:
            return f"Current working directory: {session.working_dir}"
        if session.codex_thread_id:
            return "This session already has a Codex thread. Use /new <path> to start fresh in another directory."
        requested = Path(" ".join(args)).expanduser().resolve()
        if not requested.exists() or not requested.is_dir():
            return f"Working directory not found: {requested}"
        session.working_dir = str(requested)
        self.store.update(session)
        return f"Working directory updated: {session.working_dir}"

    def model(self, chat_id: int, user_id: int, args: list[str]) -> str:
        session = self.ensure_session(chat_id, user_id)
        if not args:
            return f"Current model: {session.model or 'Codex default'}"
        model = args[0].strip()
        preset = self.config.codex.model_presets.get(model)
        if preset is not None:
            model = preset
        session.model = None if model.lower() in {"default", "none", ""} else model
        self.store.update(session)
        return f"Model updated for this session: {session.model or 'Codex default'}"

    def models(self) -> str:
        presets = self.config.codex.model_presets
        if not presets:
            return "No model presets configured. Use /model <model-name>."
        lines = ["Model presets:"]
        for name, model in sorted(presets.items()):
            lines.append(f"- {name}: {model or 'Codex default'}")
        return "\n".join(lines)

    def reasoning(self, chat_id: int, user_id: int, args: list[str]) -> str:
        session = self.ensure_session(chat_id, user_id)
        if not args:
            return f"Current reasoning effort: {session.reasoning_effort or 'Codex default'}"
        value = args[0].lower()
        if value in {"default", "none", "off"}:
            session.reasoning_effort = None
            self.store.update(session)
            return "Reasoning effort updated: Codex default"
        if value not in {"low", "medium", "high", "xhigh"}:
            return "Usage: /reasoning default|low|medium|high|xhigh"
        session.reasoning_effort = value
        self.store.update(session)
        return f"Reasoning effort updated: {value}"

    def mode(self, chat_id: int, user_id: int, args: list[str]) -> str:
        session = self.ensure_session(chat_id, user_id)
        if not args:
            return f"Current mode: {session.mode}"
        value = args[0].lower()
        if value in self.config.codex.presets:
            if self._preset_requests_full_access(value) and not self.config.codex.allow_runtime_full_access:
                return "Computer Access preset requires allow_runtime_full_access=true in config."
            if self._preset_requests_full_access(value):
                return (
                    "Computer Access can give Codex broad local machine access.\n"
                    "Send /confirm computer to enable it for this session."
                )
            self._apply_preset(session, value)
            self.store.update(session)
            return f"Applied preset: {value}"
        if value not in {"safe", "workspace", "full"}:
            return "Usage: /mode safe|workspace|full|<preset>"
        if value == "full" and not self.config.codex.allow_runtime_full_access:
            return "Runtime full access is disabled in config."
        if value == "full":
            return (
                "Computer Access can give Codex broad local machine access.\n"
                "Send /confirm computer to enable it for this session."
            )
        session.mode = value
        if value in {"safe", "workspace"}:
            session.full_access = False
        self.store.update(session)
        return f"Mode updated: {value}"

    def preset(self, chat_id: int, user_id: int, args: list[str]) -> str:
        if not args or args[0].lower() in {"list", "status"}:
            if not self.config.codex.presets:
                return "No presets configured."
            lines = ["Presets:"]
            for name in sorted(self.config.codex.presets):
                lines.append(f"- {name}")
            return "\n".join(lines)
        name = args[0].lower()
        if name not in self.config.codex.presets:
            return "Preset not found. Use /preset list."
        if self._preset_requests_full_access(name) and not self.config.codex.allow_runtime_full_access:
            return "Computer Access preset requires allow_runtime_full_access=true in config."
        if self._preset_requests_full_access(name):
            return (
                "Computer Access can give Codex broad local machine access.\n"
                "Send /confirm computer to enable it for this session."
            )
        session = self.ensure_session(chat_id, user_id)
        self._apply_preset(session, name)
        self.store.update(session)
        return f"Applied preset: {name}"

    def fast(self, chat_id: int, user_id: int, args: list[str]) -> str:
        session = self.ensure_session(chat_id, user_id)
        if not args:
            return f"Fast mode: {'on' if session.fast_mode else 'off'}"
        value = args[0].lower()
        if value not in {"on", "off"}:
            return "Usage: /fast on|off"
        session.fast_mode = value == "on"
        if session.fast_mode:
            session.reasoning_effort = "low"
            fast_model = self.config.codex.model_presets.get("fast")
            if fast_model:
                session.model = fast_model
        self.store.update(session)
        return f"Fast mode updated: {'on' if session.fast_mode else 'off'}"

    def fullaccess(self, chat_id: int, user_id: int, args: list[str]) -> str:
        session = self.ensure_session(chat_id, user_id)
        if not args or args[0].lower() == "status":
            active = self._session_full_access(session)
            return f"Full access: {'on' if active else 'off'}"
        value = args[0].lower()
        if value not in {"on", "off"}:
            return "Usage: /fullaccess status|on|off"
        if value == "on" and not self.config.codex.allow_runtime_full_access:
            return "Runtime full access is disabled in config."
        if value == "on":
            return (
                "Computer Access can give Codex broad local machine access.\n"
                "Send /confirm computer to enable it for this session."
            )
        session.full_access = value == "on"
        session.mode = "full" if session.full_access else "safe"
        self.store.update(session)
        return f"Full access updated: {'on' if session.full_access else 'off'}"

    def computer(self, chat_id: int, user_id: int, args: list[str]) -> str:
        if not args:
            args = ["status"]
        response = self.fullaccess(chat_id, user_id, args)
        return response.replace("Full access", "Computer Access").replace("full access", "Computer Access")

    def confirm(self, chat_id: int, user_id: int, args: list[str]) -> str:
        if not args or args[0].lower() != "computer":
            return "Usage: /confirm computer"
        if not self.config.codex.allow_runtime_full_access:
            return "Computer Access is disabled in config."
        session = self.ensure_session(chat_id, user_id)
        session.full_access = True
        session.mode = "full"
        self.store.update(session)
        return "Computer Access enabled for this session."

    def workspace(self, chat_id: int, user_id: int, args: list[str]) -> str:
        if not args:
            return self.cwd(chat_id, user_id, [])
        action = args[0].lower()
        if action == "list":
            lines = ["Configured workspaces:"]
            for index, root in enumerate(self.config.codex.workspace_roots, start=1):
                lines.append(f"{index}. {root}")
            return "\n".join(lines)
        if action == "switch":
            if len(args) < 2:
                return "Usage: /workspace switch <number-or-path>"
            target = self._workspace_target(args[1:])
            if isinstance(target, str):
                return target
            return self._set_workspace(chat_id, user_id, target)
        if action == "add":
            return "Runtime workspace add is not enabled. Add workspace_roots in config.json."
        target = self._workspace_target(args)
        if isinstance(target, str):
            return target
        return self._set_workspace(chat_id, user_id, target)

    def permissions(self, chat_id: int, user_id: int) -> str:
        session = self.ensure_session(chat_id, user_id)
        return (
            "Permissions:\n"
            f"- Mode: {session.mode}\n"
            f"- Full access: {'on' if self._session_full_access(session) else 'off'}\n"
            f"- Runtime full access allowed: {self.config.codex.allow_runtime_full_access}\n"
            f"- Current workspace: {session.working_dir}\n"
            f"- Workspace roots: {', '.join(str(root) for root in self.config.codex.workspace_roots)}\n"
            f"- Extra writable dirs: {', '.join(str(root) for root in self.config.codex.additional_writable_dirs) or 'none'}"
        )

    def settings(self, chat_id: int, user_id: int) -> str:
        session = self.ensure_session(chat_id, user_id)
        typing = self._effective_bool(session.typing_indicator, self.config.progress.typing_indicator)
        progress = self._effective_bool(session.progress_messages, self.config.progress.progress_messages)
        text = (
            "Settings:\n"
            f"1. Model: {session.model or 'Codex default'}\n"
            f"2. Reasoning: {session.reasoning_effort or 'Codex default'}\n"
            f"3. Mode: {session.mode}\n"
            f"4. Computer Access: {'on' if self._session_full_access(session) else 'off'}\n"
            f"5. Typing: {'on' if typing else 'off'}\n"
            f"6. Progress text: {'on' if progress else 'off'}\n"
            f"7. Workspace: {session.working_dir}\n\n"
            "Commands: /model, /reasoning, /mode, /preset, /computer, /typing, /progress, /workspace"
        )
        keyboard = {
            "inline_keyboard": [
                [
                    {"text": "Safe", "callback_data": "/mode safe"},
                    {"text": "Work", "callback_data": "/preset work"},
                    {"text": "Fast", "callback_data": "/preset fast"},
                ],
                [
                    {"text": "Power", "callback_data": "/preset power"},
                    {"text": "Computer", "callback_data": "/computer on"},
                ],
                [
                    {"text": "Typing On", "callback_data": "/typing on"},
                    {"text": "Typing Off", "callback_data": "/typing off"},
                ],
                [
                    {"text": "Progress On", "callback_data": "/progress on"},
                    {"text": "Progress Off", "callback_data": "/progress off"},
                ],
            ]
        }
        return MessageCommandResponse(text=text, reply_markup=keyboard)

    def toggle_session_bool(
        self,
        chat_id: int,
        user_id: int,
        args: list[str],
        attr: str,
        label: str,
    ) -> str:
        session = self.ensure_session(chat_id, user_id)
        current = self._effective_bool(getattr(session, attr), getattr(self.config.progress, attr))
        if not args or args[0].lower() == "status":
            return f"{label}: {'on' if current else 'off'}"
        value = args[0].lower()
        if value not in {"on", "off", "default"}:
            return f"Usage: /{attr.replace('_indicator', '').replace('_messages', '')} status|on|off|default"
        if value == "default":
            setattr(session, attr, None)
        else:
            setattr(session, attr, value == "on")
        self.store.update(session)
        updated = self._effective_bool(getattr(session, attr), getattr(self.config.progress, attr))
        return f"{label} updated: {'on' if updated else 'off'}"

    def silent(self, chat_id: int, user_id: int, args: list[str]) -> str:
        session = self.ensure_session(chat_id, user_id)
        if not args or args[0].lower() == "status":
            typing = self._effective_bool(session.typing_indicator, self.config.progress.typing_indicator)
            progress = self._effective_bool(session.progress_messages, self.config.progress.progress_messages)
            return f"Silent mode: {'on' if not typing and not progress else 'off'}"
        value = args[0].lower()
        if value not in {"on", "off", "default"}:
            return "Usage: /silent status|on|off|default"
        if value == "on":
            session.typing_indicator = False
            session.progress_messages = False
        elif value == "off":
            session.typing_indicator = True
            session.progress_messages = True
        else:
            session.typing_indicator = None
            session.progress_messages = None
        self.store.update(session)
        return f"Silent mode updated: {value}"

    def rename(self, chat_id: int, user_id: int, args: list[str]) -> str:
        if not args:
            return "Usage: /rename <session-title>"
        session = self.ensure_session(chat_id, user_id)
        session.title = " ".join(args).strip()[:80] or session.title
        self.store.update(session)
        return f"Session renamed: {session.title}"

    def summary(self, chat_id: int, user_id: int) -> str:
        session = self.ensure_session(chat_id, user_id)
        return (
            "Session summary:\n"
            f"- Title: {session.title}\n"
            f"- Gateway session: {session.id}\n"
            f"- Codex thread: {session.codex_thread_id or 'not started yet'}\n"
            f"- Working directory: {session.working_dir}\n"
            f"- Model: {session.model or 'Codex default'}\n"
            f"- Reasoning: {session.reasoning_effort or 'Codex default'}\n"
            f"- Turns: {session.turn_count}\n"
            f"- Last message: {session.last_message_at or 'none'}"
        )

    def logs(self, args: list[str]) -> str | FileCommandResponse:
        kind = args[0].lower() if args else "gateway"
        if kind == "gateway":
            path = self.config.gateway.state_dir / "gateway.log"
            caption = "Conexgram gateway log"
        elif kind == "launchd":
            path = self.config.gateway.state_dir / "launchd.err.log"
            caption = "Conexgram launchd error log"
        else:
            return "Usage: /logs [gateway|launchd]"
        if not path.exists():
            return f"Log file not found: {path}"
        return FileCommandResponse(path=path, caption=caption)

    def tail(self, chat_id: int, user_id: int) -> str:
        session = self.ensure_session(chat_id, user_id)
        logs_dir = self.config.gateway.state_dir / "logs" / session.id
        files = sorted(logs_dir.glob("*.final.txt"), key=lambda item: item.stat().st_mtime, reverse=True)
        if not files:
            return "No Codex output log found for this session yet."
        text = files[0].read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            return "The latest Codex output log is empty."
        return text[-3000:]

    def doctor(self) -> str:
        checks = ["Doctor:"]
        checks.append(f"- Python: {sys.version.split()[0]}")
        codex_path = shutil.which(self.config.codex.binary)
        checks.append(f"- Codex binary: {codex_path or 'not found'}")
        if codex_path:
            try:
                result = subprocess.run(
                    [self.config.codex.binary, "--version"],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                version_text = (result.stdout or result.stderr).strip() or "no version output"
                checks.append(f"- Codex version: {version_text}")
            except Exception as exc:
                checks.append(f"- Codex version check failed: {exc}")
        checks.append(f"- Config path: {self.config.config_path}")
        checks.append(f"- State dir writable: {self.config.gateway.state_dir.exists()}")
        checks.append(f"- Default workspace exists: {self.config.codex.default_working_dir.exists()}")
        checks.append(f"- Allowlist configured: {bool(self.config.telegram.allowed_user_ids or self.config.telegram.allowed_chat_ids)}")
        return "\n".join(checks)

    def version_text(self) -> str:
        try:
            package_version = version("conexgram")
        except PackageNotFoundError:
            package_version = "source checkout"
        return (
            "Conexgram version:\n"
            f"- Conexgram: {package_version}\n"
            f"- Python: {sys.version.split()[0]}\n"
            f"- Codex binary: {self.config.codex.binary}"
        )

    def config_text(self) -> str:
        access = "full access" if self.config.codex.full_access else "configured Codex default"
        return (
            "Gateway config:\n"
            f"- Config path: {self.config.config_path}\n"
            f"- State dir: {self.config.gateway.state_dir}\n"
            f"- Session scope: {self.config.gateway.session_scope}\n"
            f"- Codex binary: {self.config.codex.binary}\n"
            f"- Codex access: {access}\n"
            f"- Upload limit: {self._format_bytes(self.config.gateway.max_upload_bytes)}\n"
            f"- Default cwd: {self.config.codex.default_working_dir}"
        )

    def codex_cli(self, args: list[str]) -> str:
        if args and args[0].lower() == "status":
            return self.codex_usage()

        command = [self.config.codex.binary] + (args or ["--help"])
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=120,
                cwd=str(self.config.codex.default_working_dir),
            )
        except subprocess.TimeoutExpired:
            return "Codex command timed out after 120 seconds."
        except Exception as exc:
            return f"Codex command failed to start: {exc}"

        output = "\n".join(
            item.strip()
            for item in (result.stdout, result.stderr)
            if item and item.strip()
        ).strip()
        if not output:
            output = "(no output)"
        if result.returncode != 0:
            output = f"Codex exited with code {result.returncode}.\n\n{output}"

        limit = max(1000, self.config.gateway.max_telegram_message_chars - 500)
        if len(output) > limit:
            output = output[:limit].rstrip() + "\n\n... output truncated ..."
        return output

    def codex_usage(self) -> str:
        auth_path = Path.home() / ".codex" / "auth.json"
        if not auth_path.exists():
            return "Codex auth not found. Run `/codex login` first."
        try:
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            token = auth["tokens"]["access_token"]
        except Exception as exc:
            return f"Could not read Codex auth token: {exc}"

        request = Request(
            "https://chatgpt.com/backend-api/wham/usage",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=20) as response:
                usage = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {401, 403}:
                return "Codex usage request was unauthorized. Run `/codex login` again, then retry `/quota`."
            return f"Codex usage request failed: HTTP {exc.code}"
        except Exception as exc:
            return f"Codex usage request failed: {exc}"

        return self._format_codex_usage(usage)

    def _format_codex_usage(self, usage: dict) -> str:
        lines = ["Codex usage:"]
        plan_type = usage.get("plan_type")
        if plan_type:
            lines.append(f"- Plan: {plan_type}")

        rate_limit = usage.get("rate_limit") or {}
        lines.extend(self._format_rate_limit("Main", rate_limit))

        for item in usage.get("additional_rate_limits") or []:
            name = item.get("limit_name") or item.get("metered_feature") or "Additional"
            lines.extend(self._format_rate_limit(str(name), item.get("rate_limit") or {}))

        credits = usage.get("credits") or {}
        if credits:
            balance = credits.get("balance")
            unlimited = credits.get("unlimited")
            has_credits = credits.get("has_credits")
            credit_bits = []
            if balance is not None:
                credit_bits.append(f"balance {balance}")
            if unlimited:
                credit_bits.append("unlimited")
            elif has_credits is not None:
                credit_bits.append("has credits" if has_credits else "no credits")
            if credit_bits:
                lines.append(f"- Credits: {', '.join(credit_bits)}")

        reached_type = usage.get("rate_limit_reached_type")
        if reached_type:
            lines.append(f"- Limit reached type: {reached_type}")
        lines.append("- Details: https://chatgpt.com/codex/settings/usage")
        return "\n".join(lines)

    def _format_rate_limit(self, label: str, rate_limit: dict) -> list[str]:
        if not rate_limit:
            return [f"- {label}: unavailable"]
        allowed = rate_limit.get("allowed")
        reached = rate_limit.get("limit_reached")
        status = "allowed" if allowed else "limited"
        if reached:
            status = "limit reached"

        primary = self._format_window(rate_limit.get("primary_window"), "5h")
        secondary = self._format_window(rate_limit.get("secondary_window"), "weekly")
        return [f"- {label}: {status}", f"  - {primary}", f"  - {secondary}"]

    def _format_window(self, window: dict | None, fallback_label: str) -> str:
        if not window:
            return f"{fallback_label}: unavailable"
        seconds = int(window.get("limit_window_seconds") or 0)
        label = self._window_label(seconds, fallback_label)
        used = window.get("used_percent")
        reset_after = window.get("reset_after_seconds")
        reset_at = window.get("reset_at")
        parts = [f"{label}: {used}% used" if used is not None else f"{label}: usage unavailable"]
        if reset_after is not None:
            parts.append(f"resets in {self._format_duration(int(reset_after))}")
        elif reset_at is not None:
            dt = datetime.fromtimestamp(int(reset_at), tz=timezone.utc).astimezone()
            parts.append(f"resets at {dt.strftime('%Y-%m-%d %H:%M:%S %Z')}")
        return ", ".join(parts)

    @staticmethod
    def _window_label(seconds: int, fallback: str) -> str:
        if 17_000 <= seconds <= 19_000:
            return "5h"
        if 600_000 <= seconds <= 610_000:
            return "weekly"
        return fallback

    @staticmethod
    def _format_duration(seconds: int) -> str:
        days, seconds = divmod(max(0, seconds), 86_400)
        hours, seconds = divmod(seconds, 3_600)
        minutes, _ = divmod(seconds, 60)
        chunks = []
        if days:
            chunks.append(f"{days}d")
        if hours:
            chunks.append(f"{hours}h")
        if minutes or not chunks:
            chunks.append(f"{minutes}m")
        return " ".join(chunks)

    def sendfile(self, chat_id: int, user_id: int, args: list[str]) -> str | FileCommandResponse:
        if not args:
            return "Usage: /sendfile <path> [caption]"
        raw_path = Path(args[0]).expanduser()
        if raw_path.is_absolute():
            requested = raw_path.resolve()
        else:
            session = self.ensure_session(chat_id, user_id)
            requested = (Path(session.working_dir) / raw_path).resolve()
        if not requested.exists():
            return f"File not found: {requested}"
        if not requested.is_file():
            return f"Not a file: {requested}"
        if not self._path_allowed(requested):
            return f"File is outside configured workspace roots: {requested}"

        size = requested.stat().st_size
        max_bytes = self.config.gateway.max_upload_bytes
        if size > max_bytes:
            return (
                f"File too large: {self._format_bytes(size)}. "
                f"Limit: {self._format_bytes(max_bytes)}."
            )

        caption = " ".join(args[1:]).strip() or None
        if caption and len(caption) > 1024:
            caption = caption[:1021] + "..."
        return FileCommandResponse(path=requested, caption=caption)

    @staticmethod
    def help_text() -> str:
        return (
            "Conexgram commands:\n"
            "/new [working_dir] - start a fresh Codex session\n"
            "/status - show the active session\n"
            "/sessions - list sessions\n"
            "/switch <number_or_id> - switch active session\n"
            "/cwd [path] - show or set cwd before Codex thread starts\n"
            "/model [name|default] - show or set model for this session\n"
            "/models - list configured model presets\n"
            "/reasoning default|low|medium|high|xhigh - set reasoning effort\n"
            "/mode safe|workspace|full|<preset> - set execution mode\n"
            "/preset list|safe|work|fast|power|computer - apply a common setup\n"
            "/fast on|off - toggle fast mode\n"
            "/fullaccess status|on|off - inspect or toggle full access if config allows it\n"
            "/computer status|on|off - user-friendly alias for full access\n"
            "/confirm computer - confirm enabling Computer Access\n"
            "/workspace [list|switch <path-or-number>|<path>] - show or set workspace\n"
            "/permissions - show effective access settings\n"
            "/settings - show friendly settings panel\n"
            "/typing status|on|off|default - control typing indicator for this session\n"
            "/progress status|on|off|default - control long-running progress messages\n"
            "/silent status|on|off|default - quickly silence or restore progress UX\n"
            "/rename <title> - rename active session\n"
            "/summary - show active session summary\n"
            "/reset - start a fresh default session\n"
            "/logs [gateway|launchd] - send a local log file\n"
            "/tail - show the latest Codex output for this session\n"
            "/doctor - run setup checks\n"
            "/version - show local versions\n"
            "/config - show gateway config summary\n"
            "/quota - show Codex usage and rate-limit status\n"
            "/codexstatus - alias for /quota\n"
            "/codex <args> - run a native Codex CLI command\n"
            "/sendfile <path> [caption] - send a local file to Telegram\n"
            "/stop - stop the currently running Codex process\n"
            "/help - show this help\n\n"
            "Any non-command message is sent to the active Codex session."
        )

    def _apply_preset(self, session: Session, name: str) -> None:
        preset = self.config.codex.presets[name]
        if "model" in preset:
            session.model = str(preset["model"]) or None
        if "reasoning_effort" in preset:
            value = str(preset["reasoning_effort"]).lower()
            if value in {"low", "medium", "high", "xhigh"}:
                session.reasoning_effort = value
        if "fast_mode" in preset:
            session.fast_mode = bool(preset["fast_mode"])
        if "mode" in preset:
            value = str(preset["mode"]).lower()
            if value in {"safe", "workspace", "full"}:
                if value != "full" or self.config.codex.allow_runtime_full_access:
                    session.mode = value
        if "full_access" in preset:
            enabled = bool(preset["full_access"])
            if enabled and not self.config.codex.allow_runtime_full_access:
                return
            session.full_access = enabled

    def _preset_requests_full_access(self, name: str) -> bool:
        preset = self.config.codex.presets.get(name, {})
        return str(preset.get("mode", "")).lower() == "full" or bool(preset.get("full_access", False))

    def _workspace_target(self, args: list[str]) -> Path | str:
        selector = " ".join(args).strip()
        if selector.isdigit():
            index = int(selector) - 1
            if 0 <= index < len(self.config.codex.workspace_roots):
                return self.config.codex.workspace_roots[index]
            return "Workspace number not found. Use /workspace list."
        requested = Path(selector).expanduser().resolve()
        if not requested.exists() or not requested.is_dir():
            return f"Working directory not found: {requested}"
        if not self._path_allowed(requested):
            return f"Workspace is outside configured workspace roots: {requested}"
        return requested

    def _set_workspace(self, chat_id: int, user_id: int, target: Path) -> str:
        session = self.ensure_session(chat_id, user_id)
        if session.codex_thread_id:
            return "This session already has a Codex thread. Use /new <path> to start fresh in another directory."
        session.working_dir = str(target)
        self.store.update(session)
        return f"Workspace updated: {session.working_dir}"

    def _path_allowed(self, path: Path) -> bool:
        roots = list(self.config.codex.workspace_roots) or [self.config.codex.default_working_dir]
        roots += list(self.config.codex.additional_writable_dirs)
        for root in roots:
            try:
                path.resolve().relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False

    def _session_full_access(self, session: Session) -> bool:
        if session.full_access is not None:
            return bool(session.full_access)
        return bool(self.config.codex.full_access)

    @staticmethod
    def _effective_bool(value: bool | None, default: bool) -> bool:
        return default if value is None else bool(value)

    @staticmethod
    def _format_bytes(value: int) -> str:
        units = ["B", "KB", "MB", "GB"]
        size = float(value)
        for unit in units:
            if size < 1024 or unit == units[-1]:
                if unit == "B":
                    return f"{int(size)} {unit}"
                return f"{size:.1f} {unit}"
            size /= 1024
