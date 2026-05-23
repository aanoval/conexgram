"""Application loop for the Conexgram."""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .codex_runner import CodexRunner
from .commands import CommandHandler, FileCommandResponse, MessageCommandResponse, ProfileCommandResponse
from .config import AppConfig
from .message_format import split_message
from .paths import ensure_dir
from .progress import ProgressNotifier
from .session_store import SessionStore, now_iso
from .telegram_api import TelegramClient, TelegramMessage, TelegramApiError

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkItem:
    message: TelegramMessage


@dataclass(frozen=True)
class AttachmentDirective:
    path_text: str
    caption: str | None = None


class GatewayApp:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        state_dir = config.gateway.state_dir
        self.store = SessionStore(state_dir / "sessions.json")
        self.telegram = TelegramClient(
            config.telegram.bot_token,
            timeout_seconds=config.telegram.poll_timeout_seconds,
        )
        self.codex = CodexRunner(
            config.codex,
            state_dir / "logs",
            max_log_days=config.gateway.max_log_days,
            max_log_mb=config.gateway.max_log_mb,
        )
        self.commands = CommandHandler(config, self.store)
        self.progress = ProgressNotifier(self.telegram, config.progress, self._send)
        self.queue: queue.Queue[WorkItem] = queue.Queue()
        self.stop_event = threading.Event()

    def run(self) -> None:
        LOG.info("Starting Conexgram")
        for index in range(self.config.gateway.worker_count):
            worker = threading.Thread(target=self._worker_loop, name=f"codex-worker-{index + 1}", daemon=True)
            worker.start()
        self._poll_loop()

    def _poll_loop(self) -> None:
        offset = self.store.update_offset
        while not self.stop_event.is_set():
            try:
                updates = self.telegram.get_updates(offset)
            except TelegramApiError as exc:
                LOG.warning("Telegram polling failed: %s", exc)
                time.sleep(5)
                continue
            for update in updates:
                offset = int(update["update_id"]) + 1
                self.store.set_update_offset(offset)
                message = self.telegram.parse_text_message(update)
                if message is None:
                    continue
                if message.callback_query_id:
                    self.telegram.answer_callback_query(message.callback_query_id)
                self._handle_message(message)

    def _handle_message(self, message: TelegramMessage) -> None:
        if not self.commands.is_allowed(message.chat_id, message.user_id):
            LOG.warning("Rejected message from user=%s chat=%s", message.user_id, message.chat_id)
            self._send(
                message.chat_id,
                (
                    "Unauthorized Telegram user or chat.\n"
                    f"Your user id: {message.user_id}\n"
                    f"This chat id: {message.chat_id}\n\n"
                    "Ask the machine owner to add one of these IDs to the Conexgram config."
                ),
                message.message_id,
                )
            return

        if message.document_file_id:
            self._handle_upload(message)
            return

        command_response = self.commands.handle_command(message.text, message.chat_id, message.user_id)
        if command_response == "__STOP_CODEX__":
            session = self.commands.ensure_session(message.chat_id, message.user_id)
            stopped = self.codex.stop_session(session.id)
            self._send(message.chat_id, "Stop signal sent." if stopped else "No Codex process is running.", message.message_id)
            return
        if isinstance(command_response, FileCommandResponse):
            self._send_file(
                message.chat_id,
                command_response.path,
                command_response.caption,
                reply_to_message_id=message.message_id,
            )
            return
        if isinstance(command_response, ProfileCommandResponse):
            for session_id in command_response.stop_session_ids:
                self.codex.stop_session(session_id)
            self._send(
                message.chat_id,
                command_response.text,
                message.message_id,
                reply_markup=command_response.reply_markup,
            )
            return
        if isinstance(command_response, MessageCommandResponse):
            self._send(
                message.chat_id,
                command_response.text,
                message.message_id,
                reply_markup=command_response.reply_markup,
            )
            return
        if command_response is not None:
            self._send(message.chat_id, command_response, message.message_id)
            return

        self.queue.put(WorkItem(message=message))
        if self.config.gateway.send_ack:
            session = self.commands.ensure_session(message.chat_id, message.user_id)
            self._send(
                message.chat_id,
                f"Queued for Codex session {session.id[:8]}.",
                message.message_id,
            )

    def _worker_loop(self) -> None:
        while not self.stop_event.is_set():
            item = self.queue.get()
            try:
                self._process_codex_message(item.message)
            except Exception:
                LOG.exception("Failed to process Codex message")
                self._send(item.message.chat_id, "Gateway error while processing this message.")
            finally:
                self.queue.task_done()

    def _process_codex_message(self, message: TelegramMessage) -> None:
        session = self.commands.ensure_session(message.chat_id, message.user_id)
        progress_handle = self.progress.start(session, message.chat_id, message.message_id)
        try:
            profile_home = self.commands.active_profile_home(message.chat_id, message.user_id)
            result = self.codex.run_turn(session, message.text, profile_home=profile_home)
        finally:
            progress_handle.stop()

        if result.thread_id and not session.codex_thread_id:
            session.codex_thread_id = result.thread_id
        session.turn_count += 1
        session.last_message_at = now_iso()
        if session.title == "Fresh Codex session":
            session.title = self._title_from_text(message.text)
        self.store.update(session)

        response_text, attachment_directives = self._extract_attachment_directives(result.text)

        prefix = ""
        if result.return_code != 0:
            prefix = f"Codex exited with code {result.return_code}.\n\n"
        text_to_send = (prefix + response_text).strip()
        if text_to_send:
            self._send(message.chat_id, text_to_send)

        for directive in attachment_directives:
            attachment = self._prepare_attachment(directive, session)
            if isinstance(attachment, str):
                self._send(message.chat_id, attachment, message.message_id)
                continue
            self._send_file(
                message.chat_id,
                attachment.path,
                attachment.caption,
                reply_to_message_id=message.message_id,
            )

    def _handle_upload(self, message: TelegramMessage) -> None:
        session = self.commands.ensure_session(message.chat_id, message.user_id)
        upload_dir = ensure_dir(Path(session.working_dir) / "telegram_uploads")
        filename = message.document_file_name or f"telegram-{message.document_file_id}"
        safe_name = Path(filename).name
        destination = upload_dir / safe_name
        try:
            self.telegram.download_file(message.document_file_id or "", destination)
        except TelegramApiError as exc:
            self._send(message.chat_id, f"Upload failed: {exc}", message.message_id)
            return
        self._send(
            message.chat_id,
            f"Uploaded file to workspace:\n{destination}",
            message.message_id,
        )

    def _send(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        reply_markup: dict | None = None,
    ) -> None:
        for chunk in split_message(text, self.config.gateway.max_telegram_message_chars):
            try:
                self.telegram.send_message(
                    chat_id,
                    chunk,
                    reply_to_message_id=reply_to_message_id,
                    reply_markup=reply_markup,
                )
            except TelegramApiError as exc:
                LOG.warning("Failed to send Telegram message: %s", exc)
            reply_markup = None

    def _send_file(
        self,
        chat_id: int,
        path: Path,
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> None:
        try:
            self.telegram.send_document(
                chat_id,
                path,
                caption=caption,
                reply_to_message_id=reply_to_message_id,
            )
        except TelegramApiError as exc:
            LOG.warning("Failed to send Telegram file: %s", exc)
            self._send(chat_id, f"Failed to send file: {exc}", reply_to_message_id)

    @staticmethod
    def _extract_attachment_directives(text: str) -> tuple[str, list[AttachmentDirective]]:
        clean_lines: list[str] = []
        directives: list[AttachmentDirective] = []
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("CONEXGRAM_SEND_FILE:"):
                path_text = stripped.split(":", 1)[1].strip().strip("\"'")
                if path_text:
                    directives.append(AttachmentDirective(path_text=path_text))
                continue
            if stripped.startswith("CONEXGRAM_SEND_FILE_CAPTION:"):
                caption = stripped.split(":", 1)[1].strip()
                if directives:
                    previous = directives[-1]
                    directives[-1] = AttachmentDirective(
                        path_text=previous.path_text,
                        caption=caption or None,
                    )
                continue
            clean_lines.append(line)
        return "\n".join(clean_lines).strip(), directives

    def _prepare_attachment(
        self,
        directive: AttachmentDirective,
        session,
    ) -> FileCommandResponse | str:
        raw_path = Path(directive.path_text).expanduser()
        if raw_path.is_absolute():
            requested = raw_path.resolve()
        else:
            requested = (Path(session.working_dir) / raw_path).resolve()
        if not requested.exists():
            return f"Attachment file not found: {requested}"
        if not requested.is_file():
            return f"Attachment path is not a file: {requested}"
        if not self.commands._path_allowed(requested):
            return f"Attachment file is outside configured workspace roots: {requested}"

        size = requested.stat().st_size
        max_bytes = self.config.gateway.max_upload_bytes
        if size > max_bytes:
            return (
                f"Attachment file too large: {CommandHandler._format_bytes(size)}. "
                f"Limit: {CommandHandler._format_bytes(max_bytes)}."
            )

        caption = directive.caption
        if caption and len(caption) > 1024:
            caption = caption[:1021] + "..."
        return FileCommandResponse(path=requested, caption=caption)

    @staticmethod
    def _title_from_text(text: str) -> str:
        title = " ".join(text.strip().split())
        if len(title) > 64:
            title = title[:61] + "..."
        return title or "Codex session"


def configure_logging(level: str, state_dir: Path) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    log_path = state_dir / "gateway.log"
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )
