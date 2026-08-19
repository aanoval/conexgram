"""Client for the private local IPC router owned by ChatGPT for macOS.

ChatGPT Desktop exposes a user-owned, length-prefixed JSON IPC router at
``~/.codex/ipc/ipc.sock``.  This is not the public app-server transport.  The
router lets local Codex clients discover the owner of a thread and forward
follower operations to that owner, which keeps the Desktop app as the actual
thread/app-server owner.

The protocol is private and can change with ChatGPT Desktop updates.  All
callers must keep the current CLI path as a fallback.
"""

from __future__ import annotations

import json
import logging
import select
import socket
import struct
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Optional

LOG = logging.getLogger(__name__)

MAX_FRAME_BYTES = 268_435_456

IPC_VERSIONS = {
    "thread-owner-discovery": 1,
    "thread-follower-start-turn": 1,
    "thread-follower-load-complete-history": 1,
    "thread-follower-compact-thread": 1,
    "thread-follower-steer-turn": 1,
    "thread-follower-interrupt-turn": 4,
    "thread-follower-update-thread-settings": 1,
    "thread-follower-edit-last-user-turn": 2,
    "thread-follower-command-approval-decision": 1,
    "thread-follower-file-approval-decision": 1,
    "thread-follower-permissions-request-approval-response": 1,
    "thread-follower-submit-user-input": 1,
    "thread-follower-submit-mcp-server-elicitation-response": 1,
    "thread-follower-set-queued-follow-ups-state": 1,
}

BROADCAST_VERSIONS = {
    "thread-stream-following-changed": 1,
}


class ChatGPTIPCError(RuntimeError):
    """Base class for a failed internal ChatGPT IPC operation."""


class ChatGPTIPCUnavailable(ChatGPTIPCError):
    """The Desktop IPC route cannot be used and CLI fallback is safe."""


class ChatGPTIPCTransportError(ChatGPTIPCError):
    """The IPC transport failed after a request may have been sent."""


class ChatGPTIPCRequestError(ChatGPTIPCError):
    """ChatGPT rejected a request after the IPC route was selected."""


@dataclass(frozen=True)
class ChatGPTIPCTurn:
    conversation_id: str
    host_id: str
    owner_client_id: str
    turn_id: Optional[str] = None


class ChatGPTIPCClient:
    """Synchronous, single-flight client for ChatGPT's local IPC router."""

    def __init__(
        self,
        socket_path: Optional[Path] = None,
        host_id: str = "local",
        client_type: str = "CONEXGRAM",
        timeout_seconds: float = 20.0,
        broadcast_callback: Optional[Callable[[dict[str, Any]], None]] = None,
    ) -> None:
        self.socket_path = (
            socket_path.expanduser()
            if socket_path is not None
            else Path.home() / ".codex" / "ipc" / "ipc.sock"
        )
        self.host_id = host_id or "local"
        self.client_type = client_type or "CONEXGRAM"
        self.timeout_seconds = max(1.0, float(timeout_seconds))
        self.broadcast_callback = broadcast_callback
        self._socket: Optional[socket.socket] = None
        self.client_id = "initializing-client"
        self._request_lock = RLock()

    @property
    def connected(self) -> bool:
        return self._socket is not None

    def connect(self) -> None:
        if self._socket is not None:
            return
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.setblocking(True)
        try:
            sock.connect(str(self.socket_path))
        except OSError as exc:
            sock.close()
            raise ChatGPTIPCUnavailable(
                f"ChatGPT IPC socket is unavailable at {self.socket_path}: {exc}"
            ) from exc
        self._socket = sock
        try:
            response = self._request(
                "initialize",
                {"clientType": self.client_type},
                version=0,
                timeout_seconds=self.timeout_seconds,
                safe_transport_failure=True,
            )
            result = response.get("result")
            client_id = result.get("clientId") if isinstance(result, dict) else None
            if not isinstance(client_id, str) or not client_id:
                raise ChatGPTIPCUnavailable("ChatGPT IPC initialize returned no client id")
            self.client_id = client_id
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        sock = self._socket
        self._socket = None
        self.client_id = "initializing-client"
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def __enter__(self) -> "ChatGPTIPCClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def discover_thread_owner(self, conversation_id: str) -> str:
        self.connect()
        response = self._request(
            "thread-owner-discovery",
            {"hostId": self.host_id, "conversationId": conversation_id},
            version=IPC_VERSIONS["thread-owner-discovery"],
            safe_transport_failure=True,
        )
        owner = response.get("handledByClientId")
        if not isinstance(owner, str) or not owner:
            raise ChatGPTIPCUnavailable(
                f"ChatGPT app does not currently own thread {conversation_id} on host {self.host_id}"
            )
        return owner

    def start_turn(
        self,
        conversation_id: str,
        turn_start_params: dict[str, Any],
        local_turn_metadata: Optional[dict[str, Any]] = None,
        mcp_app_model_context_attachments: Optional[list[dict[str, Any]]] = None,
    ) -> ChatGPTIPCTurn:
        owner = self.discover_thread_owner(conversation_id)
        # Register Conexgram as a stream follower first.  The Desktop owner
        # then sends an initial conversation snapshot and subsequent patches
        # over this same IPC connection, including pending tool/user requests.
        self.follow_thread(conversation_id, owner, following=True)
        params: dict[str, Any] = {
            "conversationId": conversation_id,
            "turnStartParams": turn_start_params,
        }
        if local_turn_metadata is not None:
            params["localTurnMetadata"] = local_turn_metadata
        if mcp_app_model_context_attachments is not None:
            params["mcpAppModelContextAttachments"] = mcp_app_model_context_attachments
        response = self._request(
            "thread-follower-start-turn",
            params,
            version=IPC_VERSIONS["thread-follower-start-turn"],
            target_client_id=owner,
            timeout_seconds=self.timeout_seconds,
        )
        result = response.get("result")
        return ChatGPTIPCTurn(
            conversation_id=conversation_id,
            host_id=self.host_id,
            owner_client_id=owner,
            turn_id=_extract_turn_id(result),
        )

    def follow_thread(
        self,
        conversation_id: str,
        owner_client_id: str,
        following: bool = True,
    ) -> None:
        """Ask the Desktop owner to stream state snapshots/patches to us."""
        self._send_broadcast(
            "thread-stream-following-changed",
            {
                "conversationId": conversation_id,
                "hostId": self.host_id,
                "following": bool(following),
            },
            target_client_ids=[owner_client_id],
        )

    def poll(self, max_messages: int = 20) -> int:
        """Drain currently available broadcasts without blocking the caller."""
        sock = self._socket
        if sock is None:
            return 0
        handled = 0
        with self._request_lock:
            while handled < max(1, max_messages):
                ready, _, _ = select.select([sock], [], [], 0)
                if not ready:
                    break
                message = self._receive(_monotonic() + 0.5)
                handled += 1
                if message.get("type") == "client-discovery-request":
                    self._send({
                        "type": "client-discovery-response",
                        "requestId": message.get("requestId"),
                        "response": {"canHandle": False},
                    })
                elif message.get("type") == "broadcast":
                    self._handle_broadcast(message)
        return handled

    def interrupt_turn(
        self,
        turn: ChatGPTIPCTurn,
        mode: str = "user-stop",
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "conversationId": turn.conversation_id,
            "mode": mode,
        }
        version = IPC_VERSIONS["thread-follower-interrupt-turn"]
        if turn.turn_id:
            params["expectedTurnId"] = turn.turn_id
        else:
            # ChatGPT Desktop has a compatibility version for requests without
            # expectedTurnId; its private router explicitly accepts version 3.
            version = 3
        return self._request(
            "thread-follower-interrupt-turn",
            params,
            version=version,
            target_client_id=turn.owner_client_id,
        )

    def send_follower_request(
        self,
        method: str,
        params: dict[str, Any],
        owner_client_id: str,
    ) -> dict[str, Any]:
        """Forward an approval/input/follower operation to the thread owner."""
        if method not in IPC_VERSIONS:
            raise ValueError(f"Unsupported ChatGPT follower method: {method}")
        return self._request(
            method,
            params,
            version=IPC_VERSIONS[method],
            target_client_id=owner_client_id,
        )

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        version: int,
        target_client_id: Optional[str] = None,
        timeout_seconds: Optional[float] = None,
        safe_transport_failure: bool = False,
    ) -> dict[str, Any]:
        with self._request_lock:
            sock = self._socket
            if sock is None:
                raise ChatGPTIPCUnavailable("ChatGPT IPC client is not connected")
            request_id = str(uuid.uuid4())
            message: dict[str, Any] = {
                "type": "request",
                "requestId": request_id,
                "sourceClientId": self.client_id,
                "version": version,
                "method": method,
                "params": params,
            }
            if target_client_id:
                message["targetClientId"] = target_client_id
            try:
                self._send(message)
                response = self._receive_response(
                    request_id,
                    timeout_seconds if timeout_seconds is not None else self.timeout_seconds,
                )
            except ChatGPTIPCTransportError as exc:
                if safe_transport_failure:
                    raise ChatGPTIPCUnavailable(str(exc)) from exc
                raise ChatGPTIPCRequestError(str(exc)) from exc
            except ChatGPTIPCError:
                raise
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                error = f"ChatGPT IPC request {method} failed: {exc}"
                if safe_transport_failure:
                    raise ChatGPTIPCUnavailable(error) from exc
                raise ChatGPTIPCRequestError(error) from exc

            if response.get("resultType") == "error":
                error = str(response.get("error") or "unknown ChatGPT IPC error")
                if error in {"no-client-found", "client-disconnected", "request-timeout"}:
                    raise ChatGPTIPCUnavailable(f"ChatGPT IPC could not route {method}: {error}")
                raise ChatGPTIPCRequestError(f"ChatGPT IPC rejected {method}: {error}")
            return response

    def _receive_response(self, request_id: str, timeout_seconds: float) -> dict[str, Any]:
        deadline = _monotonic() + max(0.5, timeout_seconds)
        while True:
            message = self._receive(deadline)
            message_type = message.get("type")
            if message_type == "client-discovery-request":
                self._send({
                    "type": "client-discovery-response",
                    "requestId": message.get("requestId"),
                    "response": {"canHandle": False},
                })
                continue
            if message_type == "broadcast":
                self._handle_broadcast(message)
                continue
            if message_type == "response" and message.get("requestId") == request_id:
                return message

    def _receive(self, deadline: float) -> dict[str, Any]:
        sock = self._socket
        if sock is None:
            raise ChatGPTIPCTransportError("ChatGPT IPC client disconnected")
        header = self._read_exact(4, deadline)
        length = struct.unpack("<I", header)[0]
        if length <= 0 or length > MAX_FRAME_BYTES:
            raise ChatGPTIPCTransportError(f"Invalid ChatGPT IPC frame length: {length}")
        body = self._read_exact(length, deadline)
        try:
            message = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ChatGPTIPCTransportError(f"Invalid ChatGPT IPC JSON frame: {exc}") from exc
        if not isinstance(message, dict):
            raise ChatGPTIPCTransportError("ChatGPT IPC frame is not a JSON object")
        return message

    def _read_exact(self, length: int, deadline: float) -> bytes:
        sock = self._socket
        if sock is None:
            raise ChatGPTIPCTransportError("ChatGPT IPC client disconnected")
        chunks: list[bytes] = []
        received = 0
        while received < length:
            remaining = deadline - _monotonic()
            if remaining <= 0:
                raise ChatGPTIPCTransportError("Timed out waiting for ChatGPT IPC response")
            ready, _, _ = select.select([sock], [], [], remaining)
            if not ready:
                raise ChatGPTIPCTransportError("Timed out waiting for ChatGPT IPC response")
            chunk = sock.recv(length - received)
            if not chunk:
                raise ChatGPTIPCTransportError("ChatGPT IPC socket closed")
            chunks.append(chunk)
            received += len(chunk)
        return b"".join(chunks)

    def _send(self, message: dict[str, Any]) -> None:
        sock = self._socket
        if sock is None:
            raise ChatGPTIPCUnavailable("ChatGPT IPC client disconnected")
        body = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_FRAME_BYTES:
            raise ChatGPTIPCRequestError("ChatGPT IPC message is too large")
        sock.sendall(struct.pack("<I", len(body)) + body)

    def _send_broadcast(
        self,
        method: str,
        params: dict[str, Any],
        target_client_ids: Optional[list[str]] = None,
    ) -> None:
        if method not in BROADCAST_VERSIONS:
            raise ValueError(f"Unsupported ChatGPT IPC broadcast: {method}")
        with self._request_lock:
            message: dict[str, Any] = {
                "type": "broadcast",
                "method": method,
                "sourceClientId": self.client_id,
                "params": params,
                "version": BROADCAST_VERSIONS[method],
            }
            if target_client_ids:
                message["targetClientIds"] = target_client_ids
            self._send(message)

    def _handle_broadcast(self, message: dict[str, Any]) -> None:
        if self.broadcast_callback is None:
            return
        try:
            self.broadcast_callback(message)
        except Exception:
            LOG.exception("ChatGPT IPC broadcast callback failed")


def _extract_turn_id(value: Any) -> Optional[str]:
    if isinstance(value, dict):
        turn = value.get("turn")
        if isinstance(turn, dict):
            candidate = turn.get("id") or turn.get("turnId") or turn.get("turn_id")
            if isinstance(candidate, str) and candidate:
                return candidate
        for key in ("turnId", "turn_id"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate:
                return candidate
        for child in value.values():
            found = _extract_turn_id(child)
            if found:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _extract_turn_id(child)
            if found:
                return found
    return None


def _monotonic() -> float:
    # Kept as a helper so protocol tests can patch time without touching the
    # socket implementation.
    import time

    return time.monotonic()
