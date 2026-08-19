import json
import socket
import struct
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Optional

from conexgram.chatgpt_ipc import ChatGPTIPCClient, ChatGPTIPCUnavailable
from conexgram.codex_runner import CodexRunner


class FakeChatGPTIPCServer:
    def __init__(self, root: Path, owner: bool = True) -> None:
        self.path = root / "ipc.sock"
        self.owner = owner
        self.requests: list[dict] = []
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        self.assert_ready()

    def assert_ready(self) -> None:
        if not self._ready.wait(2):
            raise AssertionError("fake IPC server did not start")

    def close(self) -> None:
        self._thread.join(timeout=2)

    def _run(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.path))
        server.listen(1)
        self._ready.set()
        try:
            connection, _ = server.accept()
            with connection:
                while True:
                    message = self._read(connection)
                    if message is None:
                        return
                    self.requests.append(message)
                    method = message.get("method")
                    if method == "initialize":
                        self._send(connection, {
                            "type": "response",
                            "requestId": message["requestId"],
                            "resultType": "success",
                            "method": "initialize",
                            "result": {"clientId": "client-1"},
                        })
                    elif message.get("type") == "broadcast":
                        continue
                    elif method == "thread-owner-discovery":
                        response = {
                            "type": "response",
                            "requestId": message["requestId"],
                            "resultType": "success",
                            "method": method,
                            "result": {},
                        }
                        if self.owner:
                            response["handledByClientId"] = "owner-1"
                        else:
                            response["resultType"] = "error"
                            response["error"] = "no-client-found"
                        self._send(connection, response)
                    elif method == "thread-follower-start-turn":
                        self._send(connection, {
                            "type": "response",
                            "requestId": message["requestId"],
                            "resultType": "success",
                            "method": method,
                            "result": {"turn": {"id": "turn-1"}},
                        })
                    elif method == "thread-follower-interrupt-turn":
                        self._send(connection, {
                            "type": "response",
                            "requestId": message["requestId"],
                            "resultType": "success",
                            "method": method,
                            "result": {"ok": True},
                        })
                    elif method == "thread-follower-steer-turn":
                        self._send(connection, {
                            "type": "response",
                            "requestId": message["requestId"],
                            "resultType": "success",
                            "method": method,
                            "result": {"ok": True},
                        })
        finally:
            server.close()

    @staticmethod
    def _read(connection: socket.socket) -> Optional[dict]:
        header = connection.recv(4)
        if not header:
            return None
        length = struct.unpack("<I", header)[0]
        body = b""
        while len(body) < length:
            chunk = connection.recv(length - len(body))
            if not chunk:
                return None
            body += chunk
        return json.loads(body.decode("utf-8"))

    @staticmethod
    def _send(connection: socket.socket, message: dict) -> None:
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        connection.sendall(struct.pack("<I", len(body)) + body)


class ChatGPTIPCClientTests(unittest.TestCase):
    def test_discovers_owner_and_forwards_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = FakeChatGPTIPCServer(Path(tmp))
            server.start()
            client = ChatGPTIPCClient(
                socket_path=server.path,
                host_id="local",
                client_type="CONEXGRAM",
                timeout_seconds=2,
            )
            turn = client.start_turn(
                "thread-1",
                {"input": [{"type": "text", "text": "hello", "text_elements": []}]},
            )
            self.assertEqual(turn.owner_client_id, "owner-1")
            self.assertEqual(turn.turn_id, "turn-1")
            client.interrupt_turn(turn)
            client.close()
            server.close()

            methods = [request["method"] for request in server.requests]
            self.assertEqual(
                methods,
                [
                    "initialize",
                    "thread-owner-discovery",
                    "thread-stream-following-changed",
                    "thread-follower-start-turn",
                    "thread-follower-interrupt-turn",
                ],
            )
            start = server.requests[3]
            self.assertEqual(start["targetClientId"], "owner-1")
            self.assertEqual(start["params"]["conversationId"], "thread-1")

    def test_no_owner_is_safe_fallback_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = FakeChatGPTIPCServer(Path(tmp), owner=False)
            server.start()
            client = ChatGPTIPCClient(socket_path=server.path, timeout_seconds=2)
            with self.assertRaises(ChatGPTIPCUnavailable):
                client.start_turn("thread-1", {"input": []})
            client.close()
            server.close()

    def test_command_approval_mapping_matches_desktop_protocol(self):
        method, params = CodexRunner._build_ipc_interaction(
            {
                "id": "request-1",
                "method": "item/commandExecution/requestApproval",
                "params": {},
            },
            "thread-1",
            "yes",
        )
        self.assertEqual(method, "thread-follower-command-approval-decision")
        self.assertEqual(
            params,
            {
                "conversationId": "thread-1",
                "requestId": "request-1",
                "decision": "accept",
            },
        )

    def test_user_input_mapping_preserves_question_ids(self):
        method, params = CodexRunner._build_ipc_interaction(
            {
                "id": "request-2",
                "method": "item/tool/requestUserInput",
                "params": {"questions": [{"id": "choice"}]},
            },
            "thread-1",
            "answer from Telegram",
        )
        self.assertEqual(method, "thread-follower-submit-user-input")
        self.assertEqual(
            params["response"],
            {"answers": {"choice": {"answers": ["answer from Telegram"]}}},
        )

    def test_steer_turn_forwards_plain_input_to_running_owner(self):
        with tempfile.TemporaryDirectory() as tmp:
            server = FakeChatGPTIPCServer(Path(tmp))
            server.start()
            client = ChatGPTIPCClient(socket_path=server.path, timeout_seconds=2)
            owner = client.discover_thread_owner("thread-1")

            client.steer_turn(
                "thread-1",
                owner,
                "second Telegram message",
                "/tmp/conexgram-test-workspace",
            )
            client.close()
            server.close()

            steer = next(
                item for item in server.requests
                if item.get("method") == "thread-follower-steer-turn"
            )
            self.assertEqual(steer["targetClientId"], "owner-1")
            self.assertEqual(steer["params"]["conversationId"], "thread-1")
            self.assertEqual(
                steer["params"]["input"][0]["text"],
                "second Telegram message",
            )
            restore_message = steer["params"]["restoreMessage"]
            resolved_cwd = str(Path("/tmp/conexgram-test-workspace").resolve())
            self.assertEqual(restore_message["cwd"], resolved_cwd)
            self.assertEqual(
                restore_message["context"]["workspaceRoots"],
                [resolved_cwd],
            )
            self.assertEqual(restore_message["context"]["commentAttachments"], [])


if __name__ == "__main__":
    unittest.main()
