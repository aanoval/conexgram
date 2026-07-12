"""Minimal Telegram Bot API client using only the Python standard library."""

from __future__ import annotations

import json
import math
import logging
import mimetypes
import shutil
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramMessage:
    update_id: int
    message_id: int
    chat_id: int
    user_id: int
    text: str
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    callback_query_id: Optional[str] = None
    document_file_id: Optional[str] = None
    document_file_name: Optional[str] = None
    media_type: Optional[str] = None


@dataclass(frozen=True)
class TelegramDownloadableMedia:
    file_id: str
    file_name: str
    media_type: str


class TelegramApiError(RuntimeError):
    """Raised when Telegram API returns an error."""


class TelegramClient:
    def __init__(
        self,
        bot_token: str,
        timeout_seconds: int = 30,
        api_base_url: str = "https://api.telegram.org",
        local_bot_api: bool = False,
    ) -> None:
        self.bot_token = bot_token
        self.timeout_seconds = timeout_seconds
        self.api_base_url = api_base_url.rstrip("/")
        self.local_bot_api = local_bot_api
        self.base_url = f"{self.api_base_url}/bot{bot_token}"

    def get_updates(self, offset: Optional[int]) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": self.timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._request("getUpdates", payload, timeout=self.timeout_seconds + 10)
        return list(result)

    def get_me(self) -> dict[str, Any]:
        result = self._request("getMe", {}, timeout=15)
        if not isinstance(result, dict):
            raise TelegramApiError("Telegram API getMe returned unexpected payload")
        return result

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: Optional[int] = None,
        reply_markup: Optional[dict[str, Any]] = None,
    ) -> Optional[int]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        result = self._request("sendMessage", payload, timeout=30)
        if isinstance(result, dict):
            message_id = result.get("message_id")
            if isinstance(message_id, int):
                return message_id
        return None

    def edit_message_text(
        self,
        chat_id: int,
        message_id: int,
        text: str,
        reply_markup: Optional[dict[str, Any]] = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._request("editMessageText", payload, timeout=30)

    def set_my_commands(self, commands: list[dict[str, str]]) -> None:
        self._request("setMyCommands", {"commands": commands}, timeout=30)

    def set_chat_menu_button(self) -> None:
        self._request("setChatMenuButton", {"menu_button": {"type": "commands"}}, timeout=30)

    def answer_callback_query(self, callback_query_id: str) -> None:
        self._request("answerCallbackQuery", {"callback_query_id": callback_query_id}, timeout=15)

    def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "action": action,
        }
        self._request("sendChatAction", payload, timeout=15)

    def send_document(
        self,
        chat_id: int,
        file_path: Path,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[int] = None,
    ) -> None:
        upload_timeout = self._document_upload_timeout(file_path)
        if self.local_bot_api:
            payload: dict[str, Any] = {
                "chat_id": chat_id,
                "document": file_path.expanduser().resolve().as_uri(),
            }
            if caption:
                payload["caption"] = caption
            if reply_to_message_id is not None:
                payload["reply_parameters"] = {"message_id": reply_to_message_id}
            self._request("sendDocument", payload, timeout=upload_timeout)
            return
        payload: dict[str, str] = {
            "chat_id": str(chat_id),
        }
        if caption:
            payload["caption"] = caption
        if reply_to_message_id is not None:
            payload["reply_parameters"] = json.dumps({"message_id": reply_to_message_id})
        self._multipart_request(
            "sendDocument",
            payload,
            "document",
            file_path,
            timeout=upload_timeout,
        )

    @staticmethod
    def _document_upload_timeout(file_path: Path) -> int:
        # Large uploads can spend several minutes transferring from the Bot API
        # server to Telegram even when local mode passes the file by path.
        size = file_path.stat().st_size
        transfer_seconds = math.ceil(size / (256 * 1024))
        return min(7200, max(600, 120 + transfer_seconds))

    def parse_text_message(self, update: dict[str, Any]) -> Optional[TelegramMessage]:
        message = update.get("message")
        if not isinstance(message, dict):
            callback = update.get("callback_query")
            if isinstance(callback, dict):
                return self._parse_callback_query(update, callback)
        if not isinstance(message, dict):
            return None
        text = message.get("text") or message.get("caption") or ""
        media = self._extract_downloadable_media(message)
        if not isinstance(text, str):
            text = ""
        if not text.strip() and media is None:
            return None
        chat = message.get("chat", {})
        user = message.get("from", {})
        if not isinstance(chat, dict) or not isinstance(user, dict):
            return None
        chat_id = chat.get("id")
        user_id = user.get("id")
        message_id = message.get("message_id")
        if chat_id is None or user_id is None or message_id is None:
            return None
        username = user.get("username")
        first_name = user.get("first_name")
        last_name = user.get("last_name")
        return TelegramMessage(
            update_id=int(update["update_id"]),
            message_id=int(message_id),
            chat_id=int(chat_id),
            user_id=int(user_id),
            text=text.strip() or "/upload",
            username=str(username) if username else None,
            first_name=str(first_name) if isinstance(first_name, str) and first_name.strip() else None,
            last_name=str(last_name) if isinstance(last_name, str) and last_name.strip() else None,
            document_file_id=media.file_id if media else None,
            document_file_name=media.file_name if media else None,
            media_type=media.media_type if media else None,
        )

    def _extract_downloadable_media(self, message: dict[str, Any]) -> Optional[TelegramDownloadableMedia]:
        document = message.get("document")
        if isinstance(document, dict) and document.get("file_id"):
            file_id = str(document["file_id"])
            file_name = self._media_file_name(
                document.get("file_name"),
                "document",
                int(message.get("message_id", 0) or 0),
                document.get("mime_type"),
            )
            return TelegramDownloadableMedia(file_id=file_id, file_name=file_name, media_type="document")

        photo = message.get("photo")
        if isinstance(photo, list):
            best_photo = self._largest_photo_size(photo)
            if best_photo is not None:
                return TelegramDownloadableMedia(
                    file_id=str(best_photo["file_id"]),
                    file_name=self._media_file_name(
                        None,
                        "photo",
                        int(message.get("message_id", 0) or 0),
                        "image/jpeg",
                    ),
                    media_type="photo",
                )

        for media_type in ("voice", "audio", "video", "video_note", "animation"):
            media = message.get(media_type)
            if isinstance(media, dict) and media.get("file_id"):
                return TelegramDownloadableMedia(
                    file_id=str(media["file_id"]),
                    file_name=self._media_file_name(
                        media.get("file_name"),
                        media_type,
                        int(message.get("message_id", 0) or 0),
                        media.get("mime_type"),
                    ),
                    media_type=media_type,
                )
        return None

    @staticmethod
    def _largest_photo_size(items: list[Any]) -> Optional[dict[str, Any]]:
        photos = [item for item in items if isinstance(item, dict) and item.get("file_id")]
        if not photos:
            return None
        return max(
            photos,
            key=lambda item: (
                int(item.get("file_size") or 0),
                int(item.get("width") or 0) * int(item.get("height") or 0),
            ),
        )

    @staticmethod
    def _media_file_name(raw_name: Any, media_type: str, message_id: int, mime_type: Any = None) -> str:
        if isinstance(raw_name, str) and raw_name.strip():
            return Path(raw_name.strip()).name
        preferred_extension = {
            "photo": ".jpg",
            "voice": ".ogg",
            "audio": ".mp3",
            "video": ".mp4",
            "video_note": ".mp4",
            "animation": ".mp4",
        }.get(media_type)
        extension = preferred_extension or ""
        if not extension and isinstance(mime_type, str) and mime_type.strip():
            extension = mimetypes.guess_extension(mime_type.split(";", 1)[0].strip()) or ""
        if not extension:
            extension = ".bin"
        safe_message_id = message_id if message_id > 0 else uuid.uuid4().hex
        return f"telegram-{media_type}-{safe_message_id}{extension}"

    def download_file(self, file_id: str, destination: Path) -> Path:
        file_info = self._request("getFile", {"file_id": file_id}, timeout=30)
        file_path = file_info.get("file_path") if isinstance(file_info, dict) else None
        if not isinstance(file_path, str):
            raise TelegramApiError("Telegram getFile did not return file_path")
        local_path = Path(file_path)
        if self.local_bot_api and local_path.is_absolute():
            if not local_path.exists() or not local_path.is_file():
                raise TelegramApiError(f"Telegram local file path is unavailable: {local_path}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local_path, destination)
            return destination
        url = f"{self.api_base_url}/file/bot{self.bot_token}/{file_path}"
        with urllib.request.urlopen(url, timeout=120) as response:
            destination.write_bytes(response.read())
        return destination

    def _parse_callback_query(
        self,
        update: dict[str, Any],
        callback: dict[str, Any],
    ) -> Optional[TelegramMessage]:
        data = callback.get("data")
        message = callback.get("message")
        user = callback.get("from", {})
        if not isinstance(data, str) or not isinstance(message, dict) or not isinstance(user, dict):
            return None
        chat = message.get("chat", {})
        if not isinstance(chat, dict):
            return None
        chat_id = chat.get("id")
        user_id = user.get("id")
        message_id = message.get("message_id")
        callback_query_id = callback.get("id")
        if chat_id is None or user_id is None or message_id is None or callback_query_id is None:
            return None
        username = user.get("username")
        first_name = user.get("first_name")
        last_name = user.get("last_name")
        return TelegramMessage(
            update_id=int(update["update_id"]),
            message_id=int(message_id),
            chat_id=int(chat_id),
            user_id=int(user_id),
            text=data.strip(),
            username=str(username) if username else None,
            first_name=str(first_name) if isinstance(first_name, str) and first_name.strip() else None,
            last_name=str(last_name) if isinstance(last_name, str) and last_name.strip() else None,
            callback_query_id=str(callback_query_id),
        )

    def _request(self, method: str, payload: dict[str, Any], timeout: int) -> Any:
        url = f"{self.base_url}/{method}"
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._execute(request, timeout)

    def _multipart_request(
        self,
        method: str,
        fields: dict[str, str],
        file_field: str,
        file_path: Path,
        timeout: int,
    ) -> Any:
        boundary = f"----TelegramCodexGateway{uuid.uuid4().hex}"
        body = bytearray()

        for name, value in fields.items():
            body.extend(f"--{boundary}\r\n".encode("utf-8"))
            body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"))
            body.extend(value.encode("utf-8"))
            body.extend(b"\r\n")

        filename = file_path.name
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        body.extend(f"--{boundary}\r\n".encode("utf-8"))
        body.extend(
            (
                f'Content-Disposition: form-data; name="{file_field}"; '
                f'filename="{filename}"\r\n'
            ).encode("utf-8")
        )
        body.extend(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
        body.extend(file_path.read_bytes())
        body.extend(b"\r\n")
        body.extend(f"--{boundary}--\r\n".encode("utf-8"))

        request = urllib.request.Request(
            f"{self.base_url}/{method}",
            data=bytes(body),
            headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Content-Length": str(len(body)),
            },
            method="POST",
        )
        return self._execute(request, timeout)

    def _execute(self, request: urllib.request.Request, timeout: int) -> Any:
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                parsed = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise TelegramApiError(f"Telegram HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise TelegramApiError(f"Telegram network error: {exc}") from exc
        except TimeoutError as exc:
            raise TelegramApiError(f"Telegram network timeout: {exc}") from exc

        if not parsed.get("ok"):
            raise TelegramApiError(f"Telegram API error: {parsed}")
        return parsed.get("result")
