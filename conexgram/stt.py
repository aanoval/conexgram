"""Local speech-to-text integration for Telegram audio."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import SttConfig


@dataclass(frozen=True)
class SttResult:
    text: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error


class LocalSttTranscriber:
    def __init__(self, config: SttConfig) -> None:
        self.config = config

    def should_transcribe(self, media_type: str) -> bool:
        return self.config.enabled and media_type in set(self.config.media_types)

    def transcribe(self, path: Path, media_type: str) -> SttResult:
        if not self.should_transcribe(media_type):
            return SttResult()
        if not self.config.python:
            return SttResult(error="stt.python is not configured.")
        python = Path(self.config.python).expanduser()
        if not python.exists():
            return SttResult(error=f"STT python does not exist: {python}")
        worker = Path(__file__).with_name("stt_worker.py")
        command = [
            str(python),
            str(worker),
            "--audio",
            str(path),
            "--model",
            self.config.model,
            "--language",
            self.config.language,
            "--device",
            self.config.device,
            "--compute-type",
            self.config.compute_type,
        ]
        try:
            proc = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.config.timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return SttResult(error=f"STT timed out after {self.config.timeout_seconds}s.")
        except OSError as exc:
            return SttResult(error=f"STT failed to start: {exc}")
        if proc.returncode != 0:
            detail = _last_nonempty_line(proc.stderr) or _last_nonempty_line(proc.stdout)
            return SttResult(error=f"STT exited with code {proc.returncode}: {detail or 'unknown error'}")
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            detail = _last_nonempty_line(proc.stdout)
            return SttResult(error=f"STT returned invalid JSON: {detail or 'empty output'}")
        if not isinstance(payload, dict) or not payload.get("ok"):
            error = str(payload.get("error") if isinstance(payload, dict) else "unknown error")
            return SttResult(error=error)
        text = str(payload.get("text") or "").strip()
        if not text:
            return SttResult(error="STT returned empty transcript.")
        return SttResult(text=text)


def _last_nonempty_line(value: Optional[str]) -> str:
    if not value:
        return ""
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    return lines[-1] if lines else ""
