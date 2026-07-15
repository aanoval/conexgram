"""Interactive first-run setup for non-technical users."""

from __future__ import annotations

import json
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, example_config_text
from .paths import ensure_dir, expand_path


def run_setup(path: Path = DEFAULT_CONFIG_PATH, force: bool = False) -> Path:
    """Run an interactive setup wizard and write config JSON."""
    path = expand_path(path)
    ensure_dir(path.parent)
    if path.exists() and not force:
        raise FileExistsError(f"Config already exists: {path}. Use --force to overwrite.")

    base = json.loads(example_config_text())

    print("Conexgram setup")
    print("Press Enter to keep defaults shown in brackets.")
    print()

    token = _ask_token(base["telegram"]["bot_token"])
    parsed_user_ids, parsed_chat_ids = _ask_allowlist()
    workspace = _ask("Main workspace folder", str(Path.home() / "ConexgramWorkspace"))
    mode = _choice("Access mode", ["safe", "workspace", "computer"], "workspace")
    model = _ask("Default Codex model, empty for Codex default", "")
    reasoning = _ask("Default reasoning effort, empty for Codex default", "")
    typing = _yes_no("Show Telegram typing indicator while Codex runs", True)
    progress = _yes_no("Send progress text every minute during long runs", True)
    runtime_computer = False
    full_access = False
    if mode == "computer":
        runtime_computer = _yes_no("Allow Telegram to toggle Computer Access later", False)
        full_access = _yes_no("Start with Computer Access enabled", False)

    workspace_dir = expand_path(workspace)
    ensure_dir(workspace_dir)
    workspace_path = str(workspace_dir)
    base["telegram"]["bot_token"] = token
    base["telegram"]["allowed_user_ids"] = parsed_user_ids
    base["telegram"]["allowed_chat_ids"] = parsed_chat_ids
    base["codex"]["default_working_dir"] = workspace_path
    base["codex"]["workspace_roots"] = [workspace_path]
    base["codex"]["model"] = model
    base["codex"]["reasoning_effort"] = reasoning.strip().lower()
    base["codex"]["mode"] = "full" if mode == "computer" else mode
    base["codex"]["full_access"] = full_access
    base["codex"]["allow_runtime_full_access"] = runtime_computer
    base["progress"]["typing_indicator"] = typing
    base["progress"]["progress_messages"] = progress

    path.write_text(json.dumps(base, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)
    print()
    print(f"Created config: {path}")
    print("Next: run `conexgram-gateway doctor`, then start the gateway.")
    return path


def _ask(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def _ask_token(default: str) -> str:
    while True:
        token = _ask("Telegram bot token from BotFather", default)
        if token and token != default:
            return token
        print("A real Telegram bot token is required.")


def _ask_allowlist() -> tuple[list[int], list[int]]:
    while True:
        user_ids = _ask("Allowed Telegram user IDs, comma-separated", "123456789")
        chat_ids = _ask("Allowed Telegram chat IDs, optional comma-separated", "")
        parsed_user_ids = _int_list(user_ids)
        parsed_chat_ids = _int_list(chat_ids)
        if parsed_user_ids or parsed_chat_ids:
            return parsed_user_ids, parsed_chat_ids
        print("At least one Telegram user ID or chat ID is required.")


def _choice(label: str, choices: list[str], default: str) -> str:
    choices_text = "/".join(choices)
    while True:
        value = _ask(f"{label} ({choices_text})", default).lower()
        if value in choices:
            return value
        print(f"Choose one of: {choices_text}")


def _yes_no(label: str, default: bool) -> bool:
    default_text = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{default_text}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Answer yes or no.")


def _int_list(value: str) -> list[int]:
    if not value.strip():
        return []
    items: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if item:
            items.append(int(item))
    return items
