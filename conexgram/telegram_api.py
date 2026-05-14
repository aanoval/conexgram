"""Minimal Telegram Bot API client using only the Python standard library."""

from __future__ import annotations

import json
import logging
import mimetypes
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class TelegramMessage:
    update_id: int
    message_id: int
    chat_id: int
    user_id: int
    text: str
    username: str | None = None
    callback_query_id: str | None = None
    document_file_id: str | None = None
    document_file_name: str | None = None


class TelegramApiError(RuntimeError):
    """Raised when Telegram API returns an error."""


class TelegramClient:
    def __init__(self, bot_token: str, timeout_seconds: int = 30) -> None:
        self.bot_token = bot_token
        self.timeout_seconds = timeout_seconds
        self.base_url = f"https://api.telegram.org/bot{bot_token}"

    def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        payload: dict[str, Any] = {
            "timeout": self.timeout_seconds,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = self._request("getUpdates", payload, timeout=self.timeout_seconds + 10)
        return list(result)

    def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to_message_id: int | None = None,
        reply_markup: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_to_message_id is not None:
            payload["reply_parameters"] = {"message_id": reply_to_message_id}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        self._request("sendMessage", payload, timeout=30)

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
        caption: str | None = None,
        reply_to_message_id: int | None = None,
    ) -> None:
        payload: dict[str, str] = {
            "chat_id": str(chat_id),
        }
        if caption:
            payload["caption"] = caption
        if reply_to_message_id is not None:
            payload["reply_parameters"] = json.dumps({"message_id": reply_to_message_id})
        self._multipart_request("sendDocument", payload, "document", file_path, timeout=120)

    def parse_text_message(self, update: dict[str, Any]) -> TelegramMessage | None:
        message = update.get("message")
        if not isinstance(message, dict):
            callback = update.get("callback_query")
            if isinstance(callback, dict):
                return self._parse_callback_query(update, callback)
        if not isinstance(message, dict):
            return None
        text = message.get("text") or message.get("caption") or ""
        document = message.get("document")
        if not isinstance(text, str):
            text = ""
        if not text.strip() and not isinstance(document, dict):
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
        return TelegramMessage(
            update_id=int(update["update_id"]),
            message_id=int(message_id),
            chat_id=int(chat_id),
            user_id=int(user_id),
            text=text.strip() or "/upload",
            username=str(username) if username else None,
            document_file_id=str(document.get("file_id")) if isinstance(document, dict) and document.get("file_id") else None,
            document_file_name=str(document.get("file_name")) if isinstance(document, dict) and document.get("file_name") else None,
        )

    def download_file(self, file_id: str, destination: Path) -> Path:
        file_info = self._request("getFile", {"file_id": file_id}, timeout=30)
        file_path = file_info.get("file_path") if isinstance(file_info, dict) else None
        if not isinstance(file_path, str):
            raise TelegramApiError("Telegram getFile did not return file_path")
        url = f"{self.base_url.replace('/bot', '/file/bot')}/{file_path}"
        with urllib.request.urlopen(url, timeout=120) as response:
            destination.write_bytes(response.read())
        return destination

    def _parse_callback_query(
        self,
        update: dict[str, Any],
        callback: dict[str, Any],
    ) -> TelegramMessage | None:
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
        return TelegramMessage(
            update_id=int(update["update_id"]),
            message_id=int(message_id),
            chat_id=int(chat_id),
            user_id=int(user_id),
            text=data.strip(),
            username=str(username) if username else None,
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

        if not parsed.get("ok"):
            raise TelegramApiError(f"Telegram API error: {parsed}")
        return parsed.get("result")
