"""Progress notifications while Codex is running."""

from __future__ import annotations

import logging
import threading
import time
from typing import Optional

from .config import ProgressConfig
from .session_store import Session
from .telegram_api import TelegramApiError, TelegramClient

LOG = logging.getLogger(__name__)


class ProgressNotifier:
    """Send Telegram typing actions and optional progress messages for a turn."""

    def __init__(
        self,
        telegram: TelegramClient,
        config: ProgressConfig,
    ) -> None:
        self.telegram = telegram
        self.config = config

    def start(self, session: Session, chat_id: int, reply_to_message_id: int) -> "ProgressHandle":
        stop_event = threading.Event()
        handle = ProgressHandle(stop_event)
        thread = threading.Thread(
            target=self._run,
            args=(session, chat_id, reply_to_message_id, handle),
            name=f"progress-{chat_id}",
            daemon=True,
        )
        handle.thread = thread
        thread.start()
        return handle

    def complete(self, handle: "ProgressHandle", chat_id: int, success: bool = True) -> None:
        if handle.message_id is None:
            return
        label = "Completed in" if success else "Stopped after"
        text = f"{label} {self._format_duration(handle.elapsed_seconds)}."
        try:
            self.telegram.edit_message_text(chat_id, handle.message_id, text)
        except TelegramApiError as exc:
            if "message is not modified" not in str(exc).lower():
                LOG.debug("Failed to complete progress message: %s", exc)

    def _run(
        self,
        session: Session,
        chat_id: int,
        reply_to_message_id: int,
        handle: "ProgressHandle",
    ) -> None:
        typing_enabled = self._effective_bool(session.typing_indicator, self.config.typing_indicator)
        messages_enabled = self._effective_bool(session.progress_messages, self.config.progress_messages)
        last_message_at = time.monotonic()

        while not handle.stop_event.is_set():
            if typing_enabled:
                try:
                    self.telegram.send_chat_action(chat_id, "typing")
                except TelegramApiError as exc:
                    LOG.debug("Failed to send typing indicator: %s", exc)

            now = time.monotonic()
            if messages_enabled and now - last_message_at >= self.config.progress_interval_seconds:
                status = handle.latest_status
                if not status:
                    handle.stop_event.wait(self.config.typing_interval_seconds if typing_enabled else 1)
                    continue
                self._upsert_progress_message(
                    handle,
                    chat_id,
                    status,
                    reply_to_message_id,
                )
                last_message_at = now

            wait_seconds = self.config.typing_interval_seconds if typing_enabled else 1
            handle.stop_event.wait(wait_seconds)

    def _upsert_progress_message(
        self,
        handle: "ProgressHandle",
        chat_id: int,
        text: str,
        reply_to_message_id: int,
    ) -> None:
        message_id = handle.message_id
        if message_id is not None:
            try:
                self.telegram.edit_message_text(chat_id, message_id, text)
                return
            except TelegramApiError as exc:
                if "message is not modified" in str(exc).lower():
                    return
                LOG.debug("Failed to edit progress message: %s", exc)
        try:
            new_message_id = self.telegram.send_message(chat_id, text, reply_to_message_id=reply_to_message_id)
            handle.message_id = new_message_id
        except TelegramApiError as exc:
            LOG.debug("Failed to send progress message: %s", exc)

    @staticmethod
    def _effective_bool(value: Optional[bool], default: bool) -> bool:
        return default if value is None else bool(value)

    @staticmethod
    def event_status(event: dict) -> str:
        event_type = str(event.get("type") or "")
        if event_type == "turn.failed":
            error = event.get("error")
            if isinstance(error, dict):
                return ProgressNotifier._compact(str(error.get("message") or "turn failed"))
            return "turn failed"
        if event_type == "error":
            return ProgressNotifier._compact(str(event.get("message") or "error"))

        item = event.get("item")
        if not isinstance(item, dict):
            return ""
        item_type = str(item.get("type") or "item")
        if item_type == "agent_message":
            return ""
        completed = event_type.endswith(".completed") or event_type == "item.completed"
        started = event_type.endswith(".started") or event_type == "item.started"
        if not completed and not started:
            return ""
        status = ProgressNotifier._natural_item_status(item_type, item, completed=completed)
        return ProgressNotifier._compact(status)

    @staticmethod
    def _natural_item_status(item_type: str, item: dict, completed: bool) -> str:
        if item_type in {"command_execution", "shell_command", "local_shell_command"}:
            command = ProgressNotifier._item_text(item, ("command", "cmd", "text"))
            category = ProgressNotifier._command_category(command)
            if category == "inspect":
                return "Codex finished inspecting the workspace." if completed else "Codex is inspecting the workspace."
            if category == "verify":
                return "Codex finished verification." if completed else "Codex is running verification."
            if category == "edit":
                return "Codex finished updating files." if completed else "Codex is updating files."
            if category == "git":
                return "Codex finished checking repository changes." if completed else "Codex is checking repository changes."
            if category == "install":
                return "Codex finished dependency work." if completed else "Codex is working with dependencies."
            return "Codex completed a project step." if completed else "Codex is working with the project."

        if item_type == "file_change":
            action = ProgressNotifier._file_change_action(item)
            return f"Codex finished {action}." if completed else f"Codex is {action}."

        text = ProgressNotifier._item_text(item, ("title", "name"))
        if text:
            return f"Codex completed {text}." if completed else f"Codex is working on {text}."
        return "Codex completed a step." if completed else "Codex is working through the task."

    @staticmethod
    def _command_category(command: str) -> str:
        lowered = command.lower()
        inspect_needles = (
            "sed ",
            "cat ",
            "rg ",
            "grep ",
            "find ",
            "ls ",
            " pwd",
            "tail ",
            "head ",
            "nl ",
            "git show",
            "git diff",
        )
        verify_needles = (
            " test",
            "pytest",
            "unittest",
            "npm test",
            "pnpm test",
            "yarn test",
            " xcodebuild",
            " fastlane",
            " build",
            " lint",
            " typecheck",
        )
        edit_needles = ("apply_patch", " python ", "node ", "perl ", "ruby ")
        install_needles = ("npm install", "pnpm install", "yarn install", "pip install", "bundle install")
        git_needles = ("git status", "git add", "git commit", "git push", "git pull")
        padded = f" {lowered} "
        if any(needle in lowered for needle in inspect_needles):
            return "inspect"
        if any(needle in padded for needle in verify_needles):
            return "verify"
        if any(needle in padded for needle in install_needles):
            return "install"
        if any(needle in padded for needle in git_needles):
            return "git"
        if any(needle in padded for needle in edit_needles):
            return "edit"
        return "work"

    @staticmethod
    def _file_change_action(item: dict) -> str:
        changes = item.get("changes")
        if not isinstance(changes, list) or not changes:
            return "updating files"
        kinds = {
            str(change.get("kind") or "edit").lower()
            for change in changes
            if isinstance(change, dict)
        }
        if kinds == {"add"}:
            return "adding files"
        if kinds == {"delete"}:
            return "removing files"
        return "updating files"

    @staticmethod
    def _item_text(item: dict, keys: tuple[str, ...]) -> str:
        for key in keys:
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return " ".join(value.strip().split())
        return ""

    @staticmethod
    def _compact(text: str, limit: int = 220) -> str:
        cleaned = " ".join(text.strip().split())
        if len(cleaned) > limit:
            return cleaned[: limit - 3].rstrip() + "..."
        return cleaned

    @staticmethod
    def _format_duration(elapsed_seconds: float) -> str:
        total_seconds = max(0, int(elapsed_seconds))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"


class ProgressHandle:
    """Stop handle returned by ProgressNotifier.start."""

    def __init__(self, stop_event: threading.Event) -> None:
        self.stop_event = stop_event
        self.started_at = time.monotonic()
        self.thread: Optional[threading.Thread] = None
        self.message_id: Optional[int] = None
        self._status_lock = threading.Lock()
        self._latest_status = ""

    @property
    def latest_status(self) -> str:
        with self._status_lock:
            return self._latest_status

    def update_from_event(self, event: dict) -> None:
        status = ProgressNotifier.event_status(event)
        if not status:
            return
        with self._status_lock:
            self._latest_status = status

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=2)
