"""Audio transcription helpers for Telegram voice/audio uploads."""

from __future__ import annotations

import json
import mimetypes
import os
import shutil
import subprocess
import uuid
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import AudioTranscriptionConfig
from .paths import ensure_dir


@dataclass(frozen=True)
class AudioTranscriptionResult:
    text: str = ""
    error: str = ""
    uploaded_path: Optional[Path] = None

    @property
    def ok(self) -> bool:
        return bool(self.text.strip()) and not self.error


class AudioTranscriber:
    _AUDIO_MEDIA_TYPES = {"voice", "audio"}
    _OPENAI_SUPPORTED_SUFFIXES = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}

    def __init__(self, config: AudioTranscriptionConfig) -> None:
        self.config = config

    def should_transcribe(self, media_type: str) -> bool:
        return (
            self.config.enabled
            and self.config.provider == "openai"
            and media_type in self._AUDIO_MEDIA_TYPES
        )

    def transcribe(self, path: Path, media_type: str) -> AudioTranscriptionResult:
        if not self.should_transcribe(media_type):
            return AudioTranscriptionResult()
        api_key = os.environ.get(self.config.api_key_env, "").strip()
        if not api_key:
            return AudioTranscriptionResult(
                error=f"{self.config.api_key_env} is not set for audio transcription."
            )
        try:
            upload_path = self._prepare_audio(path)
            text = self._openai_transcribe(upload_path, api_key)
        except AudioTranscriptionError as exc:
            return AudioTranscriptionResult(error=str(exc))
        return AudioTranscriptionResult(text=text.strip(), uploaded_path=upload_path)

    def _prepare_audio(self, path: Path) -> Path:
        if not path.exists():
            raise AudioTranscriptionError(f"Audio file does not exist: {path}")
        upload_path = path
        if path.suffix.lower() not in self._OPENAI_SUPPORTED_SUFFIXES:
            if not self.config.convert_unsupported:
                raise AudioTranscriptionError(
                    f"Unsupported audio format for OpenAI transcription: {path.suffix or 'unknown'}"
                )
            upload_path = self._convert_to_mp3(path)
        size = upload_path.stat().st_size
        if size > self.config.max_audio_bytes:
            raise AudioTranscriptionError(
                f"Audio file is too large for transcription: {size} bytes "
                f"(limit {self.config.max_audio_bytes} bytes)."
            )
        return upload_path

    def _convert_to_mp3(self, path: Path) -> Path:
        ffmpeg = shutil.which(self.config.ffmpeg_binary)
        if not ffmpeg:
            raise AudioTranscriptionError(
                f"{self.config.ffmpeg_binary} is required to convert Telegram voice notes."
            )
        output_dir = ensure_dir(path.parent / "_transcoded")
        output = output_dir / f"{path.stem}-{uuid.uuid4().hex[:8]}.mp3"
        command = [
            ffmpeg,
            "-y",
            "-i",
            str(path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-b:a",
            "64k",
            str(output),
        ]
        proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:]
            raise AudioTranscriptionError(
                "Failed to convert audio with ffmpeg"
                + (f": {detail[0]}" if detail else ".")
            )
        return output

    def _openai_transcribe(self, path: Path, api_key: str) -> str:
        fields = {
            "model": self.config.model,
            "response_format": "json",
        }
        if self.config.language:
            fields["language"] = self.config.language
        if self.config.prompt:
            fields["prompt"] = self.config.prompt
        body, content_type = _multipart_body(fields, "file", path)
        request = urllib.request.Request(
            "https://api.openai.com/v1/audio/transcriptions",
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": content_type,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_seconds) as response:
                payload = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise AudioTranscriptionError(f"OpenAI transcription HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise AudioTranscriptionError(f"OpenAI transcription network error: {exc.reason}") from exc
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise AudioTranscriptionError("OpenAI transcription returned invalid JSON.") from exc
        text = data.get("text") if isinstance(data, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise AudioTranscriptionError("OpenAI transcription did not return text.")
        return text


class AudioTranscriptionError(RuntimeError):
    pass


def _multipart_body(fields: dict[str, str], file_field: str, file_path: Path) -> tuple[bytes, str]:
    boundary = f"----conexgram-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.extend([
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode("utf-8"),
            str(value).encode("utf-8"),
            b"\r\n",
        ])
    mime = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
    chunks.extend([
        f"--{boundary}\r\n".encode("utf-8"),
        (
            f'Content-Disposition: form-data; name="{file_field}"; '
            f'filename="{file_path.name}"\r\n'
        ).encode("utf-8"),
        f"Content-Type: {mime}\r\n\r\n".encode("utf-8"),
        file_path.read_bytes(),
        b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ])
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
