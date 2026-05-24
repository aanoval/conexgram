"""Interactive terminal shell for Conexgram-managed Codex sessions."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
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


@dataclass(frozen=True)
class FileChange:
    kind: str
    path: str
    added: int = 0
    deleted: int = 0


class TerminalTheme:
    RESET = "\033[0m"
    BLUE = "\033[34m"
    CYAN = "\033[36m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    RED = "\033[31m"
    DIM = "\033[2m"
    BOLD = "\033[1m"
    INPUT_BG = "\033[48;5;238m"
    INPUT_FG = "\033[38;5;252m"
    CHANGE_ADD_BG = "\033[48;5;24m"
    CHANGE_DELETE_BG = "\033[48;5;52m"
    CHANGE_EDIT_BG = "\033[48;5;236m"

    def __init__(self) -> None:
        self.enabled = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def color(self, text: str, code: str) -> str:
        if not self.enabled:
            return text
        return f"{code}{text}{self.RESET}"

    def prompt_color(self, text: str, code: str) -> str:
        if not self.enabled:
            return text
        return f"\001{code}\002{text}\001{self.RESET}\002"


class TerminalUI:
    def __init__(self) -> None:
        self.theme = TerminalTheme()
        self._prompt_tail_lines = 0

    @property
    def interactive(self) -> bool:
        return sys.stdout.isatty()

    def prompt(self, cwd: str, profile_id: str, mode: str, model: str, reasoning: str) -> str:
        if not self.theme.enabled:
            return "› "
        width = max(24, shutil.get_terminal_size((88, 24)).columns - 1)
        meta = f"  {model} {reasoning} · {cwd}"
        top = TerminalTheme.INPUT_BG + " " * width + TerminalTheme.RESET
        middle = TerminalTheme.INPUT_BG + " " * width + TerminalTheme.RESET
        bottom = TerminalTheme.INPUT_BG + " " * width + TerminalTheme.RESET
        sys.stdout.write(top + "\n")
        sys.stdout.write(middle + "\n")
        sys.stdout.write(bottom + "\n")
        sys.stdout.write("\n")
        sys.stdout.write(self.dim(meta[:width]) + "\n")
        sys.stdout.write("\033[4A\r")
        sys.stdout.flush()
        self._prompt_tail_lines = 3
        field = (
            "\001"
            + TerminalTheme.INPUT_BG
            + TerminalTheme.INPUT_FG
            + "\002"
            + "› "
        )
        return field

    def finish_prompt(self) -> None:
        if self.theme.enabled:
            tail_lines = self._prompt_tail_lines
            self._prompt_tail_lines = 0
            sys.stdout.write(TerminalTheme.RESET)
            if tail_lines:
                sys.stdout.write(f"\033[{tail_lines}B\r")
            sys.stdout.flush()

    def box(self, title: str, body: str) -> str:
        width = self._width(body, title)
        title_text = f" {title} " if title else ""
        top = "+" + title_text + "-" * max(0, width - len(title_text) - 2) + "+"
        bottom = "+" + "-" * (width - 2) + "+"
        lines = [top]
        for raw_line in body.splitlines() or [""]:
            wrapped = self._wrap(raw_line, width - 4)
            for line in wrapped:
                lines.append(f"| {line.ljust(width - 4)} |")
        lines.append(bottom)
        return "\n".join(lines)

    def table(self, title: str, rows: list[tuple[str, str]]) -> str:
        key_width = max([len(key) for key, _ in rows] or [0])
        body_lines: list[str] = []
        for key, value in rows:
            body_lines.append(f"{key.ljust(key_width)} : {value}")
        return self.box(title, "\n".join(body_lines))

    def ok(self, text: str) -> str:
        return self.theme.color(text, TerminalTheme.GREEN)

    def warn(self, text: str) -> str:
        return self.theme.color(text, TerminalTheme.YELLOW)

    def error(self, text: str) -> str:
        return self.theme.color(text, TerminalTheme.RED)

    def accent(self, text: str) -> str:
        return self.theme.color(text, TerminalTheme.CYAN)

    def command(self, text: str) -> str:
        return self.theme.color(text, TerminalTheme.BLUE)

    def dim(self, text: str) -> str:
        return self.theme.color(text, TerminalTheme.DIM)

    def file_change(self, change: FileChange) -> str:
        if change.kind == "add":
            marker = self.ok("+")
            label = self.ok("added")
            background = TerminalTheme.CHANGE_ADD_BG
            detail = f"lines +{change.added}" if change.added else "new file"
        elif change.kind == "delete":
            marker = self.error("-")
            label = self.error("deleted")
            background = TerminalTheme.CHANGE_DELETE_BG
            detail = f"lines -{change.deleted}" if change.deleted else "removed"
        else:
            marker = self.warn("~")
            label = self.warn("edited")
            background = TerminalTheme.CHANGE_EDIT_BG
            parts = []
            if change.added:
                parts.append(self.ok(f"+{change.added}"))
            if change.deleted:
                parts.append(self.error(f"-{change.deleted}"))
            detail = "lines " + " ".join(parts) if parts else "changed"

        line = f"{marker} {label} {change.path} {detail}"
        if not self.theme.enabled:
            return line
        width = shutil.get_terminal_size((88, 24)).columns
        padded = line + " " * max(0, width - self._visible_len(line) - 1)
        return f"{background}{padded}{TerminalTheme.RESET}"

    @staticmethod
    def _visible_len(text: str) -> int:
        return len(re.sub(r"\033\[[0-9;]*m", "", text))

    def highlight_response(self, text: str) -> str:
        if not self.theme.enabled:
            return text
        highlighted = text
        highlighted = re.sub(
            r"\b(error|failed|failure|exception|rejected|blocked)\b",
            lambda item: self.error(item.group(0)),
            highlighted,
            flags=re.IGNORECASE,
        )
        highlighted = re.sub(
            r"\b(warning|warn|caution|risk|risky)\b",
            lambda item: self.warn(item.group(0)),
            highlighted,
            flags=re.IGNORECASE,
        )
        highlighted = re.sub(
            r"\b(success|succeeded|done|completed|passed|ok|valid)\b",
            lambda item: self.ok(item.group(0)),
            highlighted,
            flags=re.IGNORECASE,
        )
        highlighted = re.sub(
            r"`([^`]+)`",
            lambda item: self.accent(item.group(0)),
            highlighted,
        )
        return highlighted

    def divider(self, label: str = "") -> str:
        width = shutil.get_terminal_size((88, 24)).columns
        if not label:
            line = "─" * max(24, width)
            return self.dim(line)
        label_text = f" {label} "
        side = max(3, (width - len(label_text)) // 2)
        line = "─" * side + label_text + "─" * max(3, width - side - len(label_text))
        return self.dim(line[:width])

    @staticmethod
    def format_duration(seconds: float) -> str:
        total = max(0, int(seconds))
        minutes, secs = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}h {minutes:02d}m {secs:02d}s"
        if minutes:
            return f"{minutes}m {secs:02d}s"
        return f"{secs}s"

    def _width(self, body: str, title: str) -> int:
        terminal_width = shutil.get_terminal_size((88, 24)).columns
        max_line = max([len(line) for line in body.splitlines()] + [len(title) + 4, 48])
        return max(48, min(terminal_width, max_line + 4))

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        if width <= 0:
            return [text]
        if not text:
            return [""]
        lines: list[str] = []
        current = text
        while len(current) > width:
            lines.append(current[:width])
            current = current[width:]
        lines.append(current)
        return lines


class ProgressSpinner:
    FRAMES = ["|", "/", "-", "\\"]

    def __init__(self, ui: TerminalUI, label: str = "Codex working") -> None:
        self.ui = ui
        self.label = label
        self.status = "starting"
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._started_at = 0.0
        self._lock = threading.Lock()
        self._io_lock = threading.Lock()
        self._last_log = ""

    def start(self) -> None:
        self._started_at = time.monotonic()
        if not self.ui.interactive:
            print(f"{self.label}...")
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def update(self, status: str) -> None:
        self.log(status)

    def log(self, status: str) -> None:
        status = status.strip()
        if not status or status == self._last_log:
            return
        self._last_log = status
        if not self.ui.interactive:
            print(f"- {status}")
            return
        with self._io_lock:
            self._clear_line()
            print(self.ui.dim("- ") + self.ui.accent(status))

    def stop(self) -> None:
        if not self.ui.interactive:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=1)
        with self._io_lock:
            self._clear_line()

    def elapsed_seconds(self) -> float:
        if not self._started_at:
            return 0.0
        return time.monotonic() - self._started_at

    def _run(self) -> None:
        index = 0
        while not self._stop.is_set():
            elapsed = int(time.monotonic() - self._started_at)
            minutes, seconds = divmod(elapsed, 60)
            with self._lock:
                status = self.status
            frame = self.FRAMES[index % len(self.FRAMES)]
            index += 1
            line = f"{frame} {self.label} {minutes:02d}:{seconds:02d}"
            with self._io_lock:
                sys.stdout.write("\r" + self.ui.accent(line[: shutil.get_terminal_size((88, 24)).columns - 1]))
                sys.stdout.flush()
            time.sleep(0.25)

    @staticmethod
    def _clear_line() -> None:
        width = shutil.get_terminal_size((88, 24)).columns
        sys.stdout.write("\r" + " " * max(1, width - 1) + "\r")
        sys.stdout.flush()


class FileChangeTracker:
    def __init__(self, working_dir: Path) -> None:
        self.working_dir = working_dir.expanduser().resolve()
        self.git_root = self._git_root()

    def snapshot(self) -> dict[str, str]:
        if self.git_root is None:
            return {}
        result = self._git(["status", "--porcelain=v1", "--untracked-files=all"])
        if result is None:
            return {}
        status: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            raw_status = line[:2]
            path_text = line[3:].strip()
            if " -> " in path_text:
                path_text = path_text.rsplit(" -> ", 1)[-1]
            status[path_text] = raw_status
        return status

    def changes_since(self, before: dict[str, str]) -> list[FileChange]:
        if self.git_root is None:
            return []
        after = self.snapshot()
        changes: list[FileChange] = []
        for path_text, raw_status in sorted(after.items()):
            if before.get(path_text) == raw_status:
                continue
            kind = self._kind(raw_status)
            added, deleted = self._line_stats(path_text, kind)
            changes.append(FileChange(kind=kind, path=path_text, added=added, deleted=deleted))
        return changes

    def _git_root(self) -> Optional[Path]:
        result = subprocess.run(
            ["git", "-C", str(self.working_dir), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        root = result.stdout.strip()
        return Path(root).resolve() if root else None

    def _git(self, args: list[str]) -> Optional[subprocess.CompletedProcess[str]]:
        if self.git_root is None:
            return None
        try:
            return subprocess.run(
                ["git", "-C", str(self.git_root), *args],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception:
            return None

    def _line_stats(self, path_text: str, kind: str) -> tuple[int, int]:
        if kind == "add" and self.git_root is not None:
            path = self.git_root / path_text
            if path.exists() and path.is_file():
                return self._count_file_lines(path), 0

        result = self._git(["diff", "--numstat", "--", path_text])
        if result is None or result.returncode != 0:
            return 0, 0
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) >= 3 and parts[2] == path_text:
                return self._parse_numstat(parts[0]), self._parse_numstat(parts[1])
        return 0, 0

    @staticmethod
    def _kind(raw_status: str) -> str:
        if raw_status == "??" or "A" in raw_status:
            return "add"
        if "D" in raw_status:
            return "delete"
        return "edit"

    @staticmethod
    def _parse_numstat(value: str) -> int:
        return int(value) if value.isdigit() else 0

    @staticmethod
    def _count_file_lines(path: Path) -> int:
        try:
            with path.open("rb") as handle:
                return sum(1 for _ in handle)
        except OSError:
            return 0


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
        self.ui = TerminalUI()
        self.renderer = TerminalEventRenderer()
        self.session: Optional[Session] = None

    def run(self, cwd: Optional[Path] = None, resume_selector: Optional[str] = None) -> int:
        working_dir = (cwd or Path.cwd()).expanduser().resolve()
        if not working_dir.exists() or not working_dir.is_dir():
            print(f"Working directory not found: {working_dir}", file=sys.stderr)
            return 1

        self._install_completer()
        if resume_selector:
            session = self.store.find_for_scope(CLI_SCOPE_KEY, resume_selector)
            if session is None:
                print(f"Conexgram CLI session not found: {resume_selector}", file=sys.stderr)
                return 1
            self.store.set_active(CLI_SCOPE_KEY, session.id)
            self.session = session
        else:
            self.session = self._create_session(working_dir)
        profile = self.active_profile()
        print(
            self.ui.table(
                "Conexgram CLI",
                [
                    ("Workspace", str(self.session.working_dir)),
                    ("Profile", self._profile_label(profile)),
                    ("Session", self.session.id[:8]),
                    ("Mode", self.session.mode),
                ],
            )
        )
        print(self.ui.accent("Type /help for commands, Ctrl-C or /exit to quit."))
        if not self.active_profile_has_auth():
            print(self.ui.warn(self.codex_not_ready_message()))

        while True:
            try:
                text = input(self._prompt())
                self.ui.finish_prompt()
            except EOFError:
                self.ui.finish_prompt()
                print()
                self.print_exit_summary()
                return 0
            except KeyboardInterrupt:
                self.ui.finish_prompt()
                print()
                self.print_exit_summary()
                return 0

            text = text.strip()
            if not text:
                continue
            if text.startswith("/"):
                try:
                    should_continue = self._handle_command(text)
                except Exception as exc:
                    print(self.ui.error(f"Command error: {exc}"), file=sys.stderr)
                    should_continue = True
                if not should_continue:
                    self.print_exit_summary()
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
        cwd = self._display_path(Path(session.working_dir))
        model = session.model or self.config.codex.model or "codex default"
        reasoning = session.reasoning_effort or self.config.codex.reasoning_effort or "default"
        profile = self.active_profile()
        return self.ui.prompt(str(cwd), profile.id, session.mode, str(model), str(reasoning))

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
        change_tracker = FileChangeTracker(Path(session.working_dir))
        before_changes = change_tracker.snapshot()
        progress = ProgressSpinner(self.ui)
        renderer = TerminalEventRenderer(status_callback=progress.update)
        progress.start()
        try:
            result = self.runner.run_turn(
                session,
                text,
                profile_home=profile_home,
                event_callback=renderer.render,
            )
        finally:
            progress.stop()
        worked_seconds = progress.elapsed_seconds()
        if result.thread_id and not session.codex_thread_id:
            session.codex_thread_id = result.thread_id
        session.turn_count += 1
        session.last_message_at = now_iso()
        self.store.update(session)

        if result.return_code != 0:
            print(self.ui.error(f"Codex exited with code {result.return_code}."))
        file_changes = change_tracker.changes_since(before_changes)
        if file_changes:
            print()
            for change in file_changes:
                print(self.ui.file_change(change))
        if result.text.strip():
            print()
            print(self.ui.highlight_response(result.text.strip()))
            print(self.ui.divider(f"Worked for {self.ui.format_duration(worked_seconds)}"))

    def _handle_command(self, text: str) -> bool:
        parts = text.split()
        command = parts[0].lower()
        args = parts[1:]
        if command in {"/exit", "/quit"}:
            return False
        if command == "/help":
            print(self.ui.box("Commands", self.help_text()))
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
        print(self.ui.warn("Unknown command. Type /help for available commands."))
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
            "/exit - leave the shell and print resume info\n\n"
            "Any non-command text is sent to the active Codex session."
        )

    def print_exit_summary(self) -> None:
        session = self._require_session()
        usage = self._session_token_usage(session)
        resume_command = f"conexgram resume {session.id}"
        print()
        print(
            "Token usage: "
            f"total={self._format_number(usage['total'])} "
            f"input={self._format_number(usage['input'])} "
            f"(+ {self._format_number(usage['cached'])} cached) "
            f"output={self._format_number(usage['output'])} "
            f"(reasoning {self._format_number(usage['reasoning'])})"
        )
        print(
            "To continue this session, run "
            + self.ui.command(resume_command)
        )

    def _session_token_usage(self, session: Session) -> dict[str, int]:
        usage = {"input": 0, "cached": 0, "output": 0, "reasoning": 0, "total": 0}
        session_logs = self.config.gateway.state_dir / "logs" / session.id
        if not session_logs.exists():
            return usage
        for path in sorted(session_logs.glob("turn-*.jsonl")):
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except OSError:
                continue
            for line in lines:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                raw_usage = event.get("usage") if isinstance(event, dict) else None
                if not isinstance(raw_usage, dict):
                    continue
                usage["input"] += int(raw_usage.get("input_tokens") or 0)
                usage["cached"] += int(raw_usage.get("cached_input_tokens") or 0)
                usage["output"] += int(raw_usage.get("output_tokens") or 0)
                usage["reasoning"] += int(raw_usage.get("reasoning_output_tokens") or 0)
        usage["total"] = usage["input"] + usage["output"]
        return usage

    @staticmethod
    def _format_number(value: int) -> str:
        return f"{value:,}".replace(",", ".")

    def status_text(self) -> str:
        session = self._require_session()
        profile = self.active_profile()
        thread = session.codex_thread_id or "not started yet"
        return self.ui.table(
            "Active Session",
            [
                ("Session", session.id),
                ("Codex thread", thread),
                ("Workspace", session.working_dir),
                ("Profile", self._profile_label(profile)),
                ("Model", session.model or "Codex default"),
                ("Reasoning", session.reasoning_effort or "Codex default"),
                ("Mode", session.mode),
                ("Turns", str(session.turn_count)),
            ],
        )

    def sessions_text(self) -> str:
        sessions = self.store.list_for_scope(CLI_SCOPE_KEY)
        if not sessions:
            return "No CLI sessions yet."
        active = self._require_session()
        lines = []
        for index, session in enumerate(sessions[:30], start=1):
            marker = "*" if session.id == active.id else " "
            thread = "started" if session.codex_thread_id else "fresh"
            lines.append(
                f"{marker} {index}. {session.id[:8]} {thread} "
                f"turns={session.turn_count} cwd={session.working_dir}"
            )
        return self.ui.box("CLI Sessions", "\n".join(lines))

    def switch_session(self, args: list[str]) -> str:
        if not args:
            return "Usage: /switch <session-number-or-id>"
        session = self.store.find_for_scope(CLI_SCOPE_KEY, args[0])
        if session is None:
            return "Session not found. Use /sessions to list available sessions."
        self.store.set_active(CLI_SCOPE_KEY, session.id)
        self.session = session
        return self.ui.table(
            "Session Switched",
            [("Session", session.id), ("Workspace", session.working_dir)],
        )

    def new_session(self, args: list[str]) -> str:
        working_dir = Path(" ".join(args)).expanduser().resolve() if args else Path.cwd().resolve()
        if not working_dir.exists() or not working_dir.is_dir():
            return f"Working directory not found: {working_dir}"
        self.session = self._create_session(working_dir)
        return self.ui.table(
            "New Session",
            [("Session", self.session.id), ("Workspace", self.session.working_dir)],
        )

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
        return self.ui.table(
            "Active Profile",
            [
                ("id", profile.id),
                ("name", profile.display_name),
                ("email", profile.email),
                ("home", profile.home_dir),
            ],
        )

    def profile_list(self) -> str:
        active_profile_id = self.store.active_profile_id(CLI_SCOPE_KEY)
        lines = []
        for profile in self.store.list_profiles():
            marker = "*" if profile.id == active_profile_id else " "
            lines.append(f"{marker} {profile.id} | {profile.display_name} | {profile.email}")
        return self.ui.box("Codex Profiles", "\n".join(lines)) if lines else "No profiles registered yet."

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
        return self.ui.table(
            "Profile Switched",
            [
                ("Profile", self._profile_label(target)),
                ("Session", session.id[:8]),
                ("Saved", "old CLI threads from other profiles"),
            ],
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
    def _display_path(path: Path) -> str:
        try:
            home = Path.home().resolve()
            resolved = path.expanduser().resolve()
            relative = resolved.relative_to(home)
            return "~/" + str(relative)
        except Exception:
            return str(path)

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
    """Convert Codex JSON events into compact progress status text."""

    def __init__(self, status_callback: Optional[Callable[[str], None]] = None) -> None:
        self._status_callback = status_callback or (lambda _status: None)

    def reset(self) -> None:
        return None

    def render(self, event: dict) -> None:
        event_type = str(event.get("type") or "")
        if event_type == "thread.started":
            return
        if event_type == "turn.started":
            return
        if event_type == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict):
                self._status_callback(str(error.get("message") or "turn failed"))
            else:
                self._status_callback("turn failed")
            return
        if event_type == "error":
            self._status_callback(str(event.get("message") or "error"))
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
            self._status_callback(label)
        elif event_type.endswith(".completed") or event_type == "item.completed":
            self._status_callback(f"completed {label}")

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
