"""Configuration loading and validation."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .paths import DEFAULT_CONFIG_PATH, DEFAULT_STATE_DIR, ensure_dir, expand_path


@dataclass(frozen=True)
class TelegramConfig:
    bot_token: str
    allowed_user_ids: set[int] = field(default_factory=set)
    allowed_chat_ids: set[int] = field(default_factory=set)
    owner_user_id: Optional[int] = None
    owner_chat_id: Optional[int] = None
    poll_timeout_seconds: int = 30
    api_base_url: str = "https://api.telegram.org"
    local_bot_api: bool = False


@dataclass(frozen=True)
class CodexConfig:
    binary: str = "codex"
    default_working_dir: Path = Path.cwd()
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    mode: str = "safe"
    fast_mode: bool = False
    full_access: bool = False
    allow_runtime_full_access: bool = False
    max_turn_seconds: int = 1800
    skip_git_repo_check: bool = True
    additional_writable_dirs: list[Path] = field(default_factory=list)
    workspace_roots: list[Path] = field(default_factory=list)
    model_presets: dict[str, str] = field(default_factory=dict)
    presets: dict[str, dict[str, Any]] = field(default_factory=dict)
    base_prompt: str = ""


@dataclass(frozen=True)
class GatewayConfig:
    state_dir: Path = DEFAULT_STATE_DIR
    session_scope: str = "chat"
    send_ack: bool = False
    max_telegram_message_chars: int = 3900
    max_upload_bytes: int = 50 * 1024 * 1024
    worker_count: int = 1
    max_log_days: int = 14
    max_log_mb: int = 100
    log_level: str = "INFO"


@dataclass(frozen=True)
class ProgressConfig:
    typing_indicator: bool = True
    typing_interval_seconds: int = 4
    progress_messages: bool = True
    progress_interval_seconds: int = 60
    messages: list[str] = field(default_factory=lambda: [
        "Still working on it...",
        "Codex is still running. I will send the result when it finishes.",
        "Still active, waiting for Codex to finish.",
        "Processing is taking longer than usual, but the session is still running.",
    ])


@dataclass(frozen=True)
class SttConfig:
    enabled: bool = False
    python: str = ""
    model: str = "tiny"
    language: str = "id"
    device: str = "cpu"
    compute_type: str = "int8"
    media_types: list[str] = field(default_factory=lambda: ["voice", "audio"])
    timeout_seconds: int = 120


@dataclass(frozen=True)
class UploadsConfig:
    retention_hours: int = 6
    cleanup_interval_minutes: int = 60
    keep_transcripts: bool = True


@dataclass(frozen=True)
class AppConfig:
    telegram: TelegramConfig
    codex: CodexConfig
    gateway: GatewayConfig
    config_path: Path
    progress: ProgressConfig = field(default_factory=ProgressConfig)
    stt: SttConfig = field(default_factory=SttConfig)
    uploads: UploadsConfig = field(default_factory=UploadsConfig)


def example_config_text() -> str:
    """Return a portable example config."""
    example = {
        "telegram": {
            "bot_token": "REPLACE_WITH_BOTFATHER_TOKEN",
            "allowed_user_ids": [123456789],
            "allowed_chat_ids": [],
            "owner_user_id": None,
            "owner_chat_id": None,
            "poll_timeout_seconds": 30,
            "api_base_url": "https://api.telegram.org",
            "local_bot_api": False,
        },
        "codex": {
            "binary": "codex",
            "default_working_dir": str(Path.home() / "ConexgramWorkspace"),
            "model": "",
            "reasoning_effort": "",
            "mode": "safe",
            "fast_mode": False,
            "full_access": False,
            "allow_runtime_full_access": False,
            "max_turn_seconds": 1800,
            "skip_git_repo_check": True,
            "additional_writable_dirs": [],
            "workspace_roots": [str(Path.home() / "ConexgramWorkspace")],
            "model_presets": {
                "default": "",
                "fast": "gpt-5.3-codex-spark",
                "power": "gpt-5.2",
            },
            "presets": {
                "safe": {"mode": "safe", "full_access": False},
                "work": {"mode": "workspace", "full_access": False},
                "fast": {"mode": "safe", "fast_mode": True, "reasoning_effort": "low"},
                "power": {"mode": "workspace", "fast_mode": False, "reasoning_effort": "high"},
                "computer": {"mode": "full", "full_access": True, "reasoning_effort": "high"},
            },
            "base_prompt": (
                "You are Codex CLI running through Conexgram. Treat Telegram "
                "messages as user instructions. Keep the current Codex session "
                "context intact, be concise in final replies, and report verified "
                "command results."
            ),
        },
        "gateway": {
            "state_dir": str(DEFAULT_STATE_DIR),
            "session_scope": "chat",
            "send_ack": False,
            "max_telegram_message_chars": 3900,
            "max_upload_bytes": 52428800,
            "worker_count": 1,
            "max_log_days": 14,
            "max_log_mb": 100,
            "log_level": "INFO",
        },
        "progress": {
            "typing_indicator": True,
            "typing_interval_seconds": 4,
            "progress_messages": True,
            "progress_interval_seconds": 60,
            "messages": [
                "Still working on it...",
                "Codex is still running. I will send the result when it finishes.",
                "Still active, waiting for Codex to finish.",
                "Processing is taking longer than usual, but the session is still running.",
            ],
        },
        "stt": {
            "enabled": False,
            "python": str(Path.home() / "ConexgramWorkspace" / ".venv-stt" / "bin" / "python"),
            "model": "tiny",
            "language": "id",
            "device": "cpu",
            "compute_type": "int8",
            "media_types": ["voice", "audio"],
            "timeout_seconds": 120,
        },
        "uploads": {
            "retention_hours": 6,
            "cleanup_interval_minutes": 60,
            "keep_transcripts": True,
        },
    }
    return json.dumps(example, indent=2) + "\n"


def save_config(config: AppConfig) -> None:
    """Persist AppConfig back to disk."""
    config_path = expand_path(config.config_path)

    data = {
        "telegram": {
            "bot_token": config.telegram.bot_token,
            "allowed_user_ids": sorted(config.telegram.allowed_user_ids),
            "allowed_chat_ids": sorted(config.telegram.allowed_chat_ids),
            "owner_user_id": config.telegram.owner_user_id,
            "owner_chat_id": config.telegram.owner_chat_id,
            "poll_timeout_seconds": config.telegram.poll_timeout_seconds,
            "api_base_url": config.telegram.api_base_url,
            "local_bot_api": config.telegram.local_bot_api,
        },
        "codex": {
            "binary": config.codex.binary,
            "default_working_dir": str(config.codex.default_working_dir),
            "model": config.codex.model or "",
            "reasoning_effort": config.codex.reasoning_effort or "",
            "mode": config.codex.mode,
            "fast_mode": config.codex.fast_mode,
            "full_access": config.codex.full_access,
            "allow_runtime_full_access": config.codex.allow_runtime_full_access,
            "max_turn_seconds": config.codex.max_turn_seconds,
            "skip_git_repo_check": config.codex.skip_git_repo_check,
            "additional_writable_dirs": [str(item) for item in config.codex.additional_writable_dirs],
            "workspace_roots": [str(item) for item in config.codex.workspace_roots],
            "model_presets": dict(config.codex.model_presets),
            "presets": {name: dict(value) for name, value in config.codex.presets.items()},
            "base_prompt": config.codex.base_prompt,
        },
        "gateway": {
            "state_dir": str(config.gateway.state_dir),
            "session_scope": config.gateway.session_scope,
            "send_ack": config.gateway.send_ack,
            "max_telegram_message_chars": config.gateway.max_telegram_message_chars,
            "max_upload_bytes": config.gateway.max_upload_bytes,
            "worker_count": config.gateway.worker_count,
            "max_log_days": config.gateway.max_log_days,
            "max_log_mb": config.gateway.max_log_mb,
            "log_level": config.gateway.log_level,
        },
        "progress": {
            "typing_indicator": config.progress.typing_indicator,
            "typing_interval_seconds": config.progress.typing_interval_seconds,
            "progress_messages": config.progress.progress_messages,
            "progress_interval_seconds": config.progress.progress_interval_seconds,
            "messages": config.progress.messages,
        },
        "stt": {
            "enabled": config.stt.enabled,
            "python": config.stt.python,
            "model": config.stt.model,
            "language": config.stt.language,
            "device": config.stt.device,
            "compute_type": config.stt.compute_type,
            "media_types": config.stt.media_types,
            "timeout_seconds": config.stt.timeout_seconds,
        },
        "uploads": {
            "retention_hours": config.uploads.retention_hours,
            "cleanup_interval_minutes": config.uploads.cleanup_interval_minutes,
            "keep_transcripts": config.uploads.keep_transcripts,
        },
    }

    config_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    config_path.chmod(0o600)


def init_config(path: Path = DEFAULT_CONFIG_PATH, force: bool = False) -> Path:
    """Create a config file from the example."""
    path = expand_path(path)
    ensure_dir(path.parent)
    if path.exists() and not force:
        raise FileExistsError(f"Config already exists: {path}")
    path.write_text(example_config_text(), encoding="utf-8")
    path.chmod(0o600)
    return path


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load and validate config from JSON."""
    config_path = expand_path(path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    telegram_raw = raw.get("telegram", {})
    codex_raw = raw.get("codex", {})
    gateway_raw = raw.get("gateway", {})
    progress_raw = raw.get("progress", {})
    stt_raw = raw.get("stt", {})
    uploads_raw = raw.get("uploads", {})

    bot_token = str(telegram_raw.get("bot_token", "")).strip()
    if not bot_token or bot_token == "REPLACE_WITH_BOTFATHER_TOKEN":
        raise ValueError("telegram.bot_token is not configured")

    allowed_user_ids = _int_set(telegram_raw.get("allowed_user_ids", []))
    allowed_chat_ids = _int_set(telegram_raw.get("allowed_chat_ids", []))
    owner_user_id = telegram_raw.get("owner_user_id")
    owner_chat_id = telegram_raw.get("owner_chat_id")
    owner_user_id = int(owner_user_id) if owner_user_id not in (None, "") else None
    owner_chat_id = int(owner_chat_id) if owner_chat_id not in (None, "") else None
    api_base_url = str(telegram_raw.get("api_base_url", "https://api.telegram.org")).strip().rstrip("/")
    parsed_api_url = urlparse(api_base_url)
    if parsed_api_url.scheme not in {"http", "https"} or not parsed_api_url.netloc:
        raise ValueError("telegram.api_base_url must be an absolute HTTP(S) URL")

    codex_binary = str(codex_raw.get("binary", "codex")).strip() or "codex"
    if shutil.which(codex_binary) is None:
        raise ValueError(f"Codex binary not found in PATH: {codex_binary}")

    state_dir = expand_path(gateway_raw.get("state_dir", DEFAULT_STATE_DIR))
    working_dir = expand_path(codex_raw.get("default_working_dir", Path.cwd()))
    if not working_dir.exists():
        raise ValueError(f"codex.default_working_dir does not exist: {working_dir}")

    session_scope = str(gateway_raw.get("session_scope", "chat")).strip().lower()
    if session_scope not in {"chat", "user"}:
        raise ValueError("gateway.session_scope must be 'chat' or 'user'")

    model = str(codex_raw.get("model", "")).strip() or None
    reasoning_effort = str(codex_raw.get("reasoning_effort", "")).strip().lower() or None
    if reasoning_effort is not None and reasoning_effort not in {"low", "medium", "high", "xhigh"}:
        raise ValueError("codex.reasoning_effort must be empty, low, medium, high, or xhigh")

    mode = str(codex_raw.get("mode", "safe")).strip().lower()
    if mode not in {"safe", "workspace", "full"}:
        raise ValueError("codex.mode must be safe, workspace, or full")

    return AppConfig(
        telegram=TelegramConfig(
            bot_token=bot_token,
            allowed_user_ids=allowed_user_ids,
            allowed_chat_ids=allowed_chat_ids,
            owner_user_id=owner_user_id,
            owner_chat_id=owner_chat_id,
            poll_timeout_seconds=int(telegram_raw.get("poll_timeout_seconds", 30)),
            api_base_url=api_base_url,
            local_bot_api=bool(telegram_raw.get("local_bot_api", False)),
        ),
        codex=CodexConfig(
            binary=codex_binary,
            default_working_dir=working_dir,
            model=model,
            reasoning_effort=reasoning_effort,
            mode=mode,
            fast_mode=bool(codex_raw.get("fast_mode", False)),
            full_access=bool(codex_raw.get("full_access", False)),
            allow_runtime_full_access=bool(codex_raw.get("allow_runtime_full_access", False)),
            max_turn_seconds=max(60, int(codex_raw.get("max_turn_seconds", 1800))),
            skip_git_repo_check=bool(codex_raw.get("skip_git_repo_check", True)),
            additional_writable_dirs=[
                expand_path(item) for item in codex_raw.get("additional_writable_dirs", [])
            ],
            workspace_roots=_workspace_roots(codex_raw, working_dir),
            model_presets={
                str(key): str(value)
                for key, value in codex_raw.get("model_presets", {}).items()
            },
            presets={
                str(key): dict(value)
                for key, value in codex_raw.get("presets", {}).items()
                if isinstance(value, dict)
            },
            base_prompt=str(codex_raw.get("base_prompt", "")).strip(),
        ),
        gateway=GatewayConfig(
            state_dir=state_dir,
            session_scope=session_scope,
            send_ack=bool(gateway_raw.get("send_ack", False)),
            max_telegram_message_chars=int(gateway_raw.get("max_telegram_message_chars", 3900)),
            max_upload_bytes=int(gateway_raw.get("max_upload_bytes", 50 * 1024 * 1024)),
            worker_count=max(1, int(gateway_raw.get("worker_count", 1))),
            max_log_days=max(1, int(gateway_raw.get("max_log_days", 14))),
            max_log_mb=max(10, int(gateway_raw.get("max_log_mb", 100))),
            log_level=str(gateway_raw.get("log_level", "INFO")).upper(),
        ),
        config_path=config_path,
        progress=ProgressConfig(
            typing_indicator=bool(progress_raw.get("typing_indicator", True)),
            typing_interval_seconds=max(2, int(progress_raw.get("typing_interval_seconds", 4))),
            progress_messages=bool(progress_raw.get("progress_messages", True)),
            progress_interval_seconds=max(10, int(progress_raw.get("progress_interval_seconds", 60))),
            messages=_progress_messages(progress_raw.get("messages")),
        ),
        stt=_stt_config(stt_raw),
        uploads=_uploads_config(uploads_raw),
    )


def _int_set(value: Any) -> set[int]:
    if not isinstance(value, list):
        raise ValueError("allowlist values must be arrays")
    return {int(item) for item in value}


def _workspace_roots(codex_raw: dict[str, Any], default_working_dir: Path) -> list[Path]:
    raw_roots = codex_raw.get("workspace_roots", [])
    if not isinstance(raw_roots, list):
        raise ValueError("codex.workspace_roots must be an array")
    roots = [expand_path(item) for item in raw_roots]
    if not roots:
        roots = [default_working_dir]
    for root in roots:
        if not root.exists() or not root.is_dir():
            raise ValueError(f"codex.workspace_roots contains invalid directory: {root}")
    return roots


def _progress_messages(value: Any) -> list[str]:
    defaults = ProgressConfig().messages
    if not isinstance(value, list):
        return defaults
    messages = [str(item).strip() for item in value if str(item).strip()]
    return messages or defaults


def _stt_config(value: Any) -> SttConfig:
    if not isinstance(value, dict):
        value = {}
    media_types_raw = value.get("media_types", ["voice", "audio"])
    if not isinstance(media_types_raw, list):
        raise ValueError("stt.media_types must be an array")
    media_types = [str(item).strip().lower() for item in media_types_raw if str(item).strip()]
    return SttConfig(
        enabled=bool(value.get("enabled", False)),
        python=str(value.get("python", "")).strip(),
        model=str(value.get("model", "tiny")).strip() or "tiny",
        language=str(value.get("language", "id")).strip() or "id",
        device=str(value.get("device", "cpu")).strip() or "cpu",
        compute_type=str(value.get("compute_type", "int8")).strip() or "int8",
        media_types=media_types or ["voice", "audio"],
        timeout_seconds=max(10, int(value.get("timeout_seconds", 120))),
    )


def _uploads_config(value: Any) -> UploadsConfig:
    if not isinstance(value, dict):
        value = {}
    return UploadsConfig(
        retention_hours=max(1, int(value.get("retention_hours", 6))),
        cleanup_interval_minutes=max(5, int(value.get("cleanup_interval_minutes", 60))),
        keep_transcripts=bool(value.get("keep_transcripts", True)),
    )
