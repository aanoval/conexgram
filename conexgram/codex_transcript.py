"""Read-only snapshots of local Codex rollout transcripts.

The gateway uses this module when a Codex thread was started outside the
gateway (for example in the Codex app or a separate CLI process).  It never
executes or resumes a thread; it only tails the rollout JSONL that Codex
already writes to the active profile.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass(frozen=True)
class CodexThreadSnapshot:
    thread_id: str
    content: str
    processing: bool
    final: bool
    source_path: Optional[Path] = None
    source_mtime: float = 0.0
    complete: bool = False


class CodexTranscriptReader:
    """Return the latest user-visible assistant content for a Codex thread."""

    def __init__(self, profile_home: Path, stale_after_seconds: float = 180.0) -> None:
        self.profile_home = profile_home.expanduser()
        self.stale_after_seconds = max(30.0, stale_after_seconds)

    def snapshot(self, thread_id: str) -> CodexThreadSnapshot:
        path = self._latest_rollout(thread_id)
        if path is None:
            return CodexThreadSnapshot(thread_id, "", False, False, None, 0.0, False)

        latest_content = ""
        latest_task_started = False
        latest_task_completed = False
        saw_task_complete = False

        try:
            with path.open("r", encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        # Codex may be in the middle of appending a JSONL line.
                        continue
                    if not isinstance(record, dict):
                        continue

                    event_type = str(record.get("type") or "")
                    payload = record.get("payload")
                    payload = payload if isinstance(payload, dict) else {}
                    payload_type = str(payload.get("type") or "")

                    if event_type == "event_msg" and payload_type == "task_started":
                        latest_task_started = True
                        latest_task_completed = False
                        saw_task_complete = False
                        continue

                    if event_type == "event_msg" and payload_type == "task_complete":
                        final_text = payload.get("last_agent_message")
                        if isinstance(final_text, str) and final_text.strip():
                            latest_content = final_text.strip()
                        latest_task_completed = True
                        latest_task_started = False
                        saw_task_complete = True
                        continue

                    text = self._assistant_text(record, payload, payload_type)
                    if text:
                        latest_content = text

        except OSError:
            return CodexThreadSnapshot(thread_id, "", False, False, path, 0.0, False)

        try:
            source_mtime = path.stat().st_mtime
            stale = time.time() - source_mtime > self.stale_after_seconds
        except OSError:
            source_mtime = 0.0
            stale = True

        processing = latest_task_started and not latest_task_completed and not stale
        final = bool(latest_content) and not processing and saw_task_complete
        return CodexThreadSnapshot(
            thread_id,
            latest_content,
            processing,
            final,
            path,
            source_mtime,
            saw_task_complete,
        )

    def _latest_rollout(self, thread_id: str) -> Optional[Path]:
        roots = [self.profile_home / ".codex" / "sessions", Path.home() / ".codex" / "sessions"]
        candidates: list[Path] = []
        needle = f"-{thread_id}.jsonl"
        for root in roots:
            if not root.exists():
                continue
            try:
                candidates.extend(path for path in root.rglob(f"*{needle}") if path.is_file())
            except OSError:
                continue
        if not candidates:
            return None
        return max(candidates, key=self._mtime)

    @staticmethod
    def _mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @classmethod
    def _assistant_text(cls, record: dict[str, Any], payload: dict[str, Any], payload_type: str) -> str:
        if record.get("type") == "event_msg" and payload_type == "agent_message":
            value = payload.get("message")
            return value.strip() if isinstance(value, str) and value.strip() else ""

        if record.get("type") != "response_item" or payload_type != "message":
            return ""
        if payload.get("role") != "assistant":
            return ""
        content = payload.get("content")
        if not isinstance(content, list):
            return ""
        parts: list[str] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") not in {"output_text", "text"}:
                continue
            value = item.get("text")
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
        return "\n".join(parts).strip()
