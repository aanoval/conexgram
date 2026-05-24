"""Interactive terminal shell for Conexgram-managed Codex sessions."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .codex_runner import CodexRunner
from .config import AppConfig
from .paths import ensure_dir
from .session_store import CodexProfile, Session, SessionStore, now_iso

try:
    import readline
except ImportError:  # pragma: no cover - platform dependent
    readline = None  # type: ignore[assignment]


CLI_SCOPE_KEY = "cli:default"
CLI_CHAT_ID = 0
CLI_USER_ID = 0


class TerminalShell:
    """Run Codex turns directly from a terminal without Telegram."""

    COMMANDS = [
        "/cwd",
        "/exit",
        "/help",
        "/mode",
        "/model",
        "/new",
        "/profile",
        "/quit",
        "/quota",
        "/reasoning",
        "/sessions",
        "/status",
        "/switch",
    ]

    PROFILE_ACTIONS = ["current", "list", "switch", "use"]
    MODES = ["safe", "workspace", "full"]
    REASONING = ["default", "low", "medium", "high", "xhigh"]

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.store = SessionStore(config.gateway.state_dir / "sessions.json")
        self.runner = CodexRunner(
            config.codex,
            config.gateway.state_dir / "logs",
            max_log_days=config.gateway.max_log_days,
            max_log_mb=config.gateway.max_log_mb,
        )
        self.renderer = TerminalEventRenderer()
        self.session: Optional[Session] = None

    def run(self, cwd: Optional[Path] = None) -> int:
        working_dir = (cwd or Path.cwd()).expanduser().resolve()
        if not working_dir.exists() or not working_dir.is_dir():
            print(f"Working directory not found: {working_dir}", file=sys.stderr)
            return 1

        self._install_completer()
        self.session = self._create_session(working_dir)
        profile = self.active_profile()
        print("Conexgram CLI shell")
        print(f"Session: {self.session.id}")
        print(f"Workspace: {self.session.working_dir}")
        print(f"Profile: {self._profile_label(profile)}")
        print("Type /help for commands, /exit to quit.")
        if not self.active_profile_has_auth():
            print(self.codex_not_ready_message())

        while True:
            try:
                text = input(self._prompt())
            except EOFError:
                print()
                return 0
            except KeyboardInterrupt:
                print("\nInterrupted. Type /exit to quit.")
                continue

            text = text.strip()
            if not text:
                continue
            if text.startswith("/"):
                try:
                    should_continue = self._handle_command(text)
                except Exception as exc:
                    print(f"Command error: {exc}", file=sys.stderr)
                    should_continue = True
                if not should_continue:
                    return 0
                continue
            self._run_codex_turn(text)

    def run_codex_args(self, args: list[str], cwd: Optional[Path] = None) -> int:
        if not self.active_profile_has_auth():
            print(self.codex_not_ready_message(), file=sys.stderr)
            return 1
        profile_home = Path(self.active_profile().home_dir)
        env = self._profile_env(profile_home)
        command = [self.config.codex.binary] + args
        try:
            result = subprocess.run(
                command,
                cwd=str((cwd or Path.cwd()).resolve()),
                env=env,
                check=False,
            )
        except FileNotFoundError:
            print(f"Codex binary not found: {self.config.codex.binary}", file=sys.stderr)
            return 127
        return int(result.returncode)

    def _prompt(self) -> str:
        session = self._require_session()
        cwd = Path(session.working_dir).name or session.working_dir
        profile = self.active_profile()
        return f"conexgram:{cwd} [{profile.id}]> "

    def _create_session(self, working_dir: Path) -> Session:
        return self.store.create(
            scope_key=CLI_SCOPE_KEY,
            chat_id=CLI_CHAT_ID,
            user_id=CLI_USER_ID,
            working_dir=working_dir,
            model=self.config.codex.model,
            reasoning_effort=self.config.codex.reasoning_effort,
            mode=self.config.codex.mode,
            fast_mode=self.config.codex.fast_mode,
            full_access=self.config.codex.full_access,
            title=f"CLI in {working_dir.name or working_dir}",
        )

    def _require_session(self) -> Session:
        if self.session is None:
            raise RuntimeError("No active CLI session.")
        return self.session

    def _run_codex_turn(self, text: str) -> None:
        if not self.active_profile_has_auth():
            print(self.codex_not_ready_message())
            return

        session = self._require_session()
        profile_home = Path(self.active_profile().home_dir)
        print("Codex is working...")
        self.renderer.reset()
        result = self.runner.run_turn(
            session,
            text,
            profile_home=profile_home,
            event_callback=self.renderer.render,
        )
        if result.thread_id and not session.codex_thread_id:
            session.codex_thread_id = result.thread_id
        session.turn_count += 1
        session.last_message_at = now_iso()
        self.store.update(session)

        if result.return_code != 0:
            print(f"Codex exited with code {result.return_code}.")
        if result.text.strip():
            print("\nCodex response:")
            print(result.text.strip())

    def _handle_command(self, text: str) -> bool:
        parts = text.split()
        command = parts[0].lower()
        args = parts[1:]
        if command in {"/exit", "/quit"}:
            return False
        if command == "/help":
            print(self.help_text())
            return True
        if command == "/status":
            print(self.status_text())
            return True
        if command == "/sessions":
            print(self.sessions_text())
            return True
        if command == "/switch":
            print(self.switch_session(args))
            return True
        if command == "/new":
            print(self.new_session(args))
            return True
        if command == "/cwd":
            print(self.cwd(args))
            return True
        if command == "/profile":
            print(self.profile_command(args))
            return True
        if command == "/quota":
            print(self.codex_usage())
            return True
        if command == "/model":
            print(self.model_command(args))
            return True
        if command == "/reasoning":
            print(self.reasoning_command(args))
            return True
        if command == "/mode":
            print(self.mode_command(args))
            return True
        print("Unknown command. Type /help for available commands.")
        return True

    def help_text(self) -> str:
        return (
            "Conexgram CLI commands:\n"
            "/new [working_dir] - start a fresh CLI Codex session\n"
            "/status - show active CLI session\n"
            "/sessions - list CLI sessions\n"
            "/switch <number_or_id> - switch CLI session\n"
            "/cwd [path] - show or set cwd before Codex thread starts\n"
            "/profile list - list Codex auth profiles\n"
            "/profile current - show active CLI profile\n"
            "/profile switch <id|email|name> - switch active CLI profile\n"
            "/quota - show Codex usage for active profile\n"
            "/model [name|default] - show or set model for this session\n"
            "/reasoning default|low|medium|high|xhigh - set reasoning effort\n"
            "/mode safe|workspace|full - set execution mode\n"
            "/exit - leave the shell\n\n"
            "Any non-command text is sent to the active Codex session."
        )

    def status_text(self) -> str:
        session = self._require_session()
        profile = self.active_profile()
        thread = session.codex_thread_id or "not started yet"
        return (
            "Active CLI session:\n"
            f"- Session: {session.id}\n"
            f"- Codex thread: {thread}\n"
            f"- Workspace: {session.working_dir}\n"
            f"- Profile: {self._profile_label(profile)}\n"
            f"- Model: {session.model or 'Codex default'}\n"
            f"- Reasoning: {session.reasoning_effort or 'Codex default'}\n"
            f"- Mode: {session.mode}\n"
            f"- Turns: {session.turn_count}"
        )

    def sessions_text(self) -> str:
        sessions = self.store.list_for_scope(CLI_SCOPE_KEY)
        if not sessions:
            return "No CLI sessions yet."
        active = self._require_session()
        lines = ["CLI sessions:"]
        for index, session in enumerate(sessions[:30], start=1):
            marker = "*" if session.id == active.id else " "
            thread = "started" if session.codex_thread_id else "fresh"
            lines.append(
                f"{marker} {index}. {session.id[:8]} {thread} "
                f"turns={session.turn_count} cwd={session.working_dir}"
            )
        return "\n".join(lines)

    def switch_session(self, args: list[str]) -> str:
        if not args:
            return "Usage: /switch <session-number-or-id>"
        session = self.store.find_for_scope(CLI_SCOPE_KEY, args[0])
        if session is None:
            return "Session not found. Use /sessions to list available sessions."
        self.store.set_active(CLI_SCOPE_KEY, session.id)
        self.session = session
        return f"Switched to session {session.id}\nWorkspace: {session.working_dir}"

    def new_session(self, args: list[str]) -> str:
        working_dir = Path(" ".join(args)).expanduser().resolve() if args else Path.cwd().resolve()
        if not working_dir.exists() or not working_dir.is_dir():
            return f"Working directory not found: {working_dir}"
        self.session = self._create_session(working_dir)
        return f"New CLI session: {self.session.id}\nWorkspace: {self.session.working_dir}"

    def cwd(self, args: list[str]) -> str:
        session = self._require_session()
        if not args:
            return f"Current working directory: {session.working_dir}"
        if session.codex_thread_id:
            return "This session already has a Codex thread. Use /new <path> to start fresh."
        requested = Path(" ".join(args)).expanduser().resolve()
        if not requested.exists() or not requested.is_dir():
            return f"Working directory not found: {requested}"
        session.working_dir = str(requested)
        self.store.update(session)
        return f"Working directory updated: {session.working_dir}"

    def profile_command(self, args: list[str]) -> str:
        if not args or args[0].lower() in {"current", "status"}:
            return self.profile_status()
        action = args[0].lower()
        if action in {"list", "ls"}:
            return self.profile_list()
        if action in {"switch", "use"}:
            if len(args) < 2:
                return "Usage: /profile switch <id|email|name>"
            return self.profile_switch(" ".join(args[1:]))
        return "Usage: /profile [list|current|switch]"

    def profile_status(self) -> str:
        profile = self.active_profile()
        return (
            "Active CLI Codex profile:\n"
            f"- id: {profile.id}\n"
            f"- name: {profile.display_name}\n"
            f"- email: {profile.email}\n"
            f"- home: {profile.home_dir}"
        )

    def profile_list(self) -> str:
        active_profile_id = self.store.active_profile_id(CLI_SCOPE_KEY)
        lines = ["Codex profiles:"]
        for profile in self.store.list_profiles():
            marker = "*" if profile.id == active_profile_id else " "
            lines.append(f"{marker} {profile.id} | {profile.display_name} | {profile.email}")
        return "\n".join(lines) if len(lines) > 1 else "No profiles registered yet."

    def profile_switch(self, selector: str) -> str:
        target = self.store.find_profile(selector)
        if target is None:
            return "Profile not found. Use /profile list to see available profiles."
        current_profile_id = self.store.active_profile_id(CLI_SCOPE_KEY)
        if target.id == current_profile_id:
            return f"Profile already active: {self._profile_label(target)}"
        affected_sessions = self.store.clear_threads_for_profile(
            scope_key=CLI_SCOPE_KEY,
            profile_id=target.id,
        )
        for session_id in affected_sessions:
            self.runner.stop_session(session_id)
        self.store.set_active_profile(CLI_SCOPE_KEY, target.id)
        session = self._require_session()
        session.profile_id = target.id
        self.store.update(session)
        return (
            f"Switched active CLI profile to: {self._profile_label(target)}\n"
            "Old CLI Codex threads from other profiles were stopped and saved."
        )

    def model_command(self, args: list[str]) -> str:
        session = self._require_session()
        if not args:
            return f"Current model: {session.model or 'Codex default'}"
        model = args[0].strip()
        preset = self.config.codex.model_presets.get(model)
        if preset is not None:
            model = preset
        session.model = None if model.lower() in {"default", "none", ""} else model
        self.store.update(session)
        return f"Model updated: {session.model or 'Codex default'}"

    def reasoning_command(self, args: list[str]) -> str:
        session = self._require_session()
        if not args:
            return f"Current reasoning effort: {session.reasoning_effort or 'Codex default'}"
        value = args[0].lower()
        if value in {"default", "none", "off"}:
            session.reasoning_effort = None
            self.store.update(session)
            return "Reasoning effort updated: Codex default"
        if value not in set(self.REASONING):
            return "Usage: /reasoning default|low|medium|high|xhigh"
        session.reasoning_effort = value
        self.store.update(session)
        return f"Reasoning effort updated: {value}"

    def mode_command(self, args: list[str]) -> str:
        session = self._require_session()
        if not args:
            return f"Current mode: {session.mode}"
        value = args[0].lower()
        if value not in set(self.MODES):
            return "Usage: /mode safe|workspace|full"
        if value == "full" and not self.config.codex.allow_runtime_full_access:
            return "Runtime full access is disabled in config."
        session.mode = value
        session.full_access = True if value == "full" else session.full_access
        self.store.update(session)
        return f"Mode updated: {session.mode}"

    def codex_usage(self) -> str:
        profile = self.active_profile()
        auth_path = Path(profile.home_dir) / ".codex" / "auth.json"
        if not auth_path.exists():
            return self.codex_not_ready_message()
        try:
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            token = auth["tokens"]["access_token"]
        except Exception as exc:
            return f"Could not read Codex auth token: {exc}"
        request = Request(
            "https://chatgpt.com/backend-api/wham/usage",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=20) as response:
                usage = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in {401, 403}:
                return "Codex usage request was unauthorized. Run Codex login again, then retry /quota."
            return f"Codex usage request failed: HTTP {exc.code}"
        except Exception as exc:
            return f"Codex usage request failed: {exc}"
        return self._format_usage(usage)

    def active_profile(self) -> CodexProfile:
        return self.store.active_profile(CLI_SCOPE_KEY)

    def active_profile_has_auth(self) -> bool:
        auth_path = Path(self.active_profile().home_dir) / ".codex" / "auth.json"
        if not auth_path.exists():
            return False
        try:
            auth = json.loads(auth_path.read_text(encoding="utf-8"))
            tokens = auth.get("tokens") if isinstance(auth, dict) else None
            token = tokens.get("access_token") if isinstance(tokens, dict) else None
            return isinstance(token, str) and bool(token.strip())
        except Exception:
            return False

    def codex_not_ready_message(self) -> str:
        profile = self.active_profile()
        return (
            f"Active profile: {self._profile_label(profile)}\n"
            f"Profile home: {profile.home_dir}\n"
            "Codex auth not found. Run `conexgram codex login --device-auth` "
            "or use Telegram /codexlogin."
        )

    def _install_completer(self) -> None:
        if readline is None:
            return

        def complete(text: str, state: int) -> Optional[str]:
            buffer = readline.get_line_buffer()
            options = self._completion_options(buffer, text)
            try:
                return options[state]
            except IndexError:
                return None

        readline.set_completer(complete)
        readline.parse_and_bind("tab: complete")

    def _completion_options(self, buffer: str, text: str) -> list[str]:
        if not buffer.startswith("/"):
            return []
        parts = buffer.split()
        if len(parts) <= 1 and not buffer.endswith(" "):
            return [item + " " for item in self.COMMANDS if item.startswith(text)]
        if parts and parts[0] == "/profile":
            if len(parts) == 1 or (len(parts) == 2 and not buffer.endswith(" ")):
                return [item + " " for item in self.PROFILE_ACTIONS if item.startswith(text)]
            if len(parts) >= 2 and parts[1] in {"switch", "use"}:
                return [
                    profile.id + " "
                    for profile in self.store.list_profiles()
                    if profile.id.startswith(text)
                ]
        if parts and parts[0] == "/mode":
            return [item + " " for item in self.MODES if item.startswith(text)]
        if parts and parts[0] == "/reasoning":
            return [item + " " for item in self.REASONING if item.startswith(text)]
        return []

    @staticmethod
    def _profile_label(profile: CodexProfile) -> str:
        return f"{profile.display_name} <{profile.email}> [{profile.id}]"

    @staticmethod
    def _profile_env(profile_home: Path) -> dict[str, str]:
        ensure_dir(profile_home)
        env = dict(os.environ)
        env["HOME"] = str(profile_home)
        env["XDG_CONFIG_HOME"] = str(profile_home / ".config")
        env["XDG_STATE_HOME"] = str(profile_home / ".local" / "state")
        env["XDG_CACHE_HOME"] = str(profile_home / ".cache")
        ensure_dir(profile_home / ".config")
        ensure_dir(profile_home / ".local" / "state")
        ensure_dir(profile_home / ".cache")
        return env

    @staticmethod
    def _format_usage(usage: dict) -> str:
        lines = ["Codex usage:"]
        plan_type = usage.get("plan_type")
        if plan_type:
            lines.append(f"- Plan: {plan_type}")
        rate_limit = usage.get("rate_limit") or {}
        if rate_limit:
            allowed = rate_limit.get("allowed")
            reached = rate_limit.get("limit_reached")
            status = "limit reached" if reached else "allowed" if allowed else "limited"
            lines.append(f"- Main: {status}")
        reached_type = usage.get("rate_limit_reached_type")
        if reached_type:
            lines.append(f"- Limit reached type: {reached_type}")
        lines.append("- Details: https://chatgpt.com/codex/settings/usage")
        return "\n".join(lines)


class TerminalEventRenderer:
    """Render Codex JSON events in a compact terminal-friendly format."""

    def __init__(self) -> None:
        self._seen_thread = False

    def reset(self) -> None:
        self._seen_thread = False

    def render(self, event: dict) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            if thread_id and not self._seen_thread:
                print(f"Thread: {thread_id}")
                self._seen_thread = True
            return
        if event_type == "turn.started":
            print("Turn started.")
            return
        if event_type == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict):
                print(f"Turn failed: {error.get('message') or error}")
            else:
                print("Turn failed.")
            return
        if event_type == "error":
            print(f"Error: {event.get('message') or event}")
            return

        item = event.get("item")
        if isinstance(item, dict):
            self._render_item_event(event_type, item)

    def _render_item_event(self, event_type: str, item: dict) -> None:
        item_type = str(item.get("type") or "item")
        if item_type == "agent_message":
            return
        label = self._item_label(item_type, item)
        if event_type.endswith(".started") or event_type == "item.started":
            print(f"- started: {label}")
        elif event_type.endswith(".completed") or event_type == "item.completed":
            print(f"- completed: {label}")

    @staticmethod
    def _item_label(item_type: str, item: dict) -> str:
        for key in ("command", "cmd", "title", "name", "text"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                value = value.strip().replace("\n", " ")
                if len(value) > 160:
                    value = value[:157] + "..."
                return f"{item_type}: {value}"
        return item_type
