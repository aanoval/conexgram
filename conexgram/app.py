"""Application loop for the Conexgram."""

from __future__ import annotations

import logging
import os
import queue
import threading
import time
from dataclasses import dataclass, replace
from pathlib import Path

from .codex_runner import CodexRunner
from .commands import CommandHandler, FileCommandResponse, MessageCommandResponse, ProfileCommandResponse
from .config import AppConfig
from .message_format import split_message
from .paths import ensure_dir
from .progress import ProgressNotifier
from .session_store import SessionStore, now_iso
from .stt import LocalSttTranscriber
from .telegram_api import TelegramClient, TelegramMessage, TelegramApiError
from typing import Optional, Union

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class WorkItem:
    message: TelegramMessage


@dataclass(frozen=True)
class AttachmentDirective:
    path_text: str
    caption: Optional[str] = None


@dataclass(frozen=True)
class UploadedTelegramMedia:
    path: Path
    media_type: str
    file_name: str
    transcript: str = ""
    transcript_error: str = ""


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
        self.commands.set_notify_callback(self._notify_for_commands)
        self.progress = ProgressNotifier(self.telegram, config.progress, self._send)
        self.stt_transcriber = LocalSttTranscriber(config.stt)
        self.queue: queue.Queue[WorkItem] = queue.Queue()
        self.stop_event = threading.Event()

    def _notify_for_commands(self, chat_id: int, text: str) -> None:
        self._send(chat_id, text)

    def _sync_bot_menu(self) -> None:
        commands = [
            {"command": "menu", "description": "Open the Conexgram command menu"},
            {"command": "help", "description": "Open help and command categories"},
            {"command": "status", "description": "Show the active Codex session"},
            {"command": "sessions", "description": "Browse workspaces and sessions"},
            {"command": "settings", "description": "Open model and safety settings"},
            {"command": "quota", "description": "Show Codex usage and limits"},
            {"command": "profile", "description": "Manage Codex auth profiles"},
            {"command": "workspace", "description": "Show or set workspace"},
            {"command": "sandbox", "description": "Choose Codex sandbox"},
            {"command": "approval", "description": "Choose approval policy"},
            {"command": "users", "description": "List authorized users"},
            {"command": "invite", "description": "Create a one-time user invite"},
            {"command": "codexlogin", "description": "Add a Codex auth profile"},
            {"command": "sendfile", "description": "Send a local file to Telegram"},
            {"command": "stop", "description": "Stop the running Codex process"},
        ]
        try:
            self.telegram.set_my_commands(commands)
            self.telegram.set_chat_menu_button()
            LOG.info("Synced Telegram bot command menu")
        except TelegramApiError as exc:
            LOG.warning("Failed to sync Telegram bot command menu: %s", exc)

    def run(self) -> None:
        LOG.info("Starting Conexgram")
        self._sync_bot_menu()
        cleanup_worker = threading.Thread(target=self._cleanup_loop, name="upload-cleanup", daemon=True)
        cleanup_worker.start()
        for index in range(self.config.gateway.worker_count):
            worker = threading.Thread(target=self._worker_loop, name=f"codex-worker-{index + 1}", daemon=True)
            worker.start()
        self._poll_loop()

    def _cleanup_loop(self) -> None:
        self._cleanup_uploads_once()
        interval = max(300, self.config.uploads.cleanup_interval_minutes * 60)
        while not self.stop_event.wait(interval):
            self._cleanup_uploads_once()

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
        self.store.record_user_identity(
            user_id=message.user_id,
            chat_id=message.chat_id,
            username=message.username,
            first_name=message.first_name,
            last_name=message.last_name,
        )

        if not self.commands.is_allowed(message.chat_id, message.user_id):
            if self.commands.claim_invite_if_valid(message.text, message.user_id, message.chat_id):
                self._send(
                    message.chat_id,
                    "Authorization complete. You can now use Conexgram.",
                    message.message_id,
                )
            else:
                LOG.warning("Rejected message from user=%s chat=%s", message.user_id, message.chat_id)
            return

        if message.document_file_id:
            upload = self._handle_upload(message)
            if upload is None:
                return
            if not self.commands.active_profile_has_auth(message.chat_id, message.user_id):
                self._send(
                    message.chat_id,
                    self.commands.codex_not_ready_message(message.chat_id, message.user_id),
                    message.message_id,
                )
                return
            media_message = replace(
                message,
                text=self._media_context_prompt(message, upload),
                document_file_id=None,
                document_file_name=None,
                media_type=None,
            )
            self.queue.put(WorkItem(message=media_message))
            if self.config.gateway.send_ack:
                session = self.commands.ensure_session(message.chat_id, message.user_id)
                self._send(
                    message.chat_id,
                    f"Queued media for Codex session {session.id[:8]}.",
                    message.message_id,
                )
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
            self._respond_command(message, command_response.text, command_response.reply_markup)
            return
        if isinstance(command_response, MessageCommandResponse):
            self._respond_command(message, command_response.text, command_response.reply_markup)
            return
        if command_response is not None:
            self._send(message.chat_id, command_response, message.message_id)
            return

        if not self.commands.active_profile_has_auth(message.chat_id, message.user_id):
            self._send(
                message.chat_id,
                self.commands.codex_not_ready_message(message.chat_id, message.user_id),
                message.message_id,
            )
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

    def _handle_upload(self, message: TelegramMessage) -> Optional[UploadedTelegramMedia]:
        session = self.commands.ensure_session(message.chat_id, message.user_id)
        upload_dir = ensure_dir(Path(session.working_dir) / "telegram_uploads")
        filename = message.document_file_name or f"telegram-{message.document_file_id}"
        safe_name = Path(filename).name
        destination = upload_dir / safe_name
        try:
            self.telegram.download_file(message.document_file_id or "", destination)
        except TelegramApiError as exc:
            self._send(message.chat_id, f"Upload failed: {exc}", message.message_id)
            return None
        media_type = message.media_type or "file"
        transcription = self.stt_transcriber.transcribe(destination, media_type)
        return UploadedTelegramMedia(
            path=destination,
            media_type=media_type,
            file_name=safe_name,
            transcript=transcription.text,
            transcript_error=transcription.error,
        )

    @staticmethod
    def _media_context_prompt(message: TelegramMessage, upload: UploadedTelegramMedia) -> str:
        caption = message.text.strip()
        has_caption = bool(caption and caption != "/upload")
        lines = [
            "Telegram media received.",
            f"- Type: {upload.media_type}",
            f"- Saved path: {upload.path}",
            f"- File name: {upload.file_name}",
        ]
        if upload.transcript:
            lines.extend([
                "",
                "Audio transcript:",
                upload.transcript,
                "",
                "Use the transcript as the user's voice instruction/context. Do not run local audio transcription tools or download transcription models.",
            ])
        elif upload.media_type in {"voice", "audio"}:
            lines.extend([
                "",
                "Audio transcript is not available.",
                f"Transcription status: {upload.transcript_error or 'STT is disabled.'}",
                "Do not run other local audio transcription tools or download transcription models. If the user needs transcription, explain that STT must be configured first.",
            ])
        else:
            lines.extend([
                "",
                "The file is available locally in the current workspace. Use this path if you need to inspect or operate on it.",
            ])
        if has_caption:
            lines.extend(["", "User caption/instruction:", caption])
        else:
            if upload.transcript:
                lines.extend([
                    "",
                    "No caption was provided. Treat the audio transcript as the latest user message and respond naturally based on the previous conversation.",
                ])
            else:
                lines.extend([
                    "",
                    "No caption was provided. Treat this media as context for the current session and respond naturally based on the previous conversation.",
                ])
        return "\n".join(lines)

    def _cleanup_uploads_once(self) -> None:
        cutoff = time.time() - (self.config.uploads.retention_hours * 3600)
        for upload_dir in self._known_upload_dirs():
            self._cleanup_upload_dir(upload_dir, cutoff)

    def _known_upload_dirs(self) -> set[Path]:
        roots = {Path(self.config.codex.default_working_dir)}
        roots.update(Path(session.working_dir) for session in self.store.list_all_sessions())
        return {root / "telegram_uploads" for root in roots}

    def _cleanup_upload_dir(self, upload_dir: Path, cutoff: float) -> None:
        try:
            if not upload_dir.exists() or not upload_dir.is_dir():
                return
            for item in upload_dir.rglob("*"):
                if not item.exists() or item.is_dir():
                    continue
                try:
                    stat = item.stat()
                except OSError:
                    continue
                if stat.st_mtime <= cutoff:
                    try:
                        item.unlink()
                        LOG.info("Deleted expired Telegram upload: %s", item)
                    except OSError as exc:
                        LOG.warning("Failed to delete expired Telegram upload %s: %s", item, exc)
            self._remove_empty_dirs(upload_dir)
        except OSError as exc:
            LOG.warning("Upload cleanup failed for %s: %s", upload_dir, exc)

    def _remove_empty_dirs(self, root: Path) -> None:
        for current, dirs, _files in os.walk(root, topdown=False):
            for dirname in dirs:
                path = Path(current) / dirname
                try:
                    path.rmdir()
                except OSError:
                    pass

    def _send(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[dict] = None,
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

    def _respond_command(
        self,
        message: TelegramMessage,
        text: str,
        reply_markup: Optional[dict] = None,
    ) -> None:
        if (
            message.callback_query_id
            and reply_markup is not None
            and len(text) <= self.config.gateway.max_telegram_message_chars
            and self._edit_callback_message(message.chat_id, message.message_id, text, reply_markup)
        ):
            return
        self._send(
            message.chat_id,
            text,
            message.message_id,
            reply_markup=reply_markup,
        )

    def _edit_callback_message(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[dict] = None,
    ) -> bool:
        try:
            self.telegram.edit_message_text(
                chat_id,
                message_id,
                text,
                reply_markup=reply_markup,
            )
            return True
        except TelegramApiError as exc:
            if "message is not modified" in str(exc).lower():
                return True
            LOG.warning("Failed to edit Telegram callback message: %s", exc)
            return False

    def _send_file(
        self,
        chat_id: int,
        path: Path,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
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
    ) -> Union[FileCommandResponse, str]:
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
