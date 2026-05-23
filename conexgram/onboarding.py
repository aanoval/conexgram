"""Interactive onboarding flow for first-run setup."""

from __future__ import annotations

import json
import secrets
import string
import sys
import time
from pathlib import Path
from typing import Any, Optional

from .config import DEFAULT_STATE_DIR, example_config_text
from .paths import ensure_dir, expand_path
from .telegram_api import TelegramApiError, TelegramClient


class OnboardingError(RuntimeError):
    """Raised when interactive onboarding cannot complete."""


def run_first_run_onboarding(config_path: Path) -> None:
    """Run first-run onboarding and write a usable production config.

    Flow:
      1. Ask for Telegram bot token.
      2. Validate token with Telegram getMe.
      3. Ask user to send one Telegram message to the bot.
      4. Send welcome + verification code to that first sender.
      5. Ask owner to paste the code in the terminal.
      6. Persist allowed user/chat IDs and finish.
    """

    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise OnboardingError(
            "Onboarding needs an interactive terminal."
            " Run `conexgram setup --force` and then `conexgram run`."
        )

    config_path = expand_path(config_path)
    base = _load_or_seed_config(config_path)

    token = _ask_bot_token()
    bot_token = token.strip()

    client = TelegramClient(bot_token)
    bot_username = _fetch_bot_username(client)
    code = _random_code(6)

    base.setdefault("telegram", {})["bot_token"] = bot_token
    base.setdefault("telegram", {})["allowed_user_ids"] = []
    base.setdefault("telegram", {})["allowed_chat_ids"] = []

    # Keep explicit, safe defaults even when config doesn't exist yet.
    workspace = Path.home() / "ConexgramWorkspace"
    workspace.mkdir(parents=True, exist_ok=True)
    base["codex"]["default_working_dir"] = str(workspace)
    base["codex"]["workspace_roots"] = [str(workspace)]

    state_dir = expand_path(base.get("gateway", {}).get("state_dir", DEFAULT_STATE_DIR))
    ensure_dir(state_dir)
    base["gateway"]["state_dir"] = str(state_dir)

    _write_config(config_path, base)

    print("Conexgram onboarding")
    print("I need to verify the first Telegram sender as the machine owner.")
    if bot_username:
        print(f"Open Telegram and send any message to @{bot_username}.")
    else:
        print("Open Telegram and send any message to your new bot.")
    print("I will reply with a verification code, then ask you to paste it here.")

    owner = _wait_owner_candidate(client)
    if owner is None:
        raise OnboardingError("No incoming Telegram message received. Onboarding stopped.")

    client.send_message(
        owner["chat_id"],
        (
            "Welcome to Conexgram.\n"
            "Your onboarding code is: "
            f"{code}\n"
            "Paste this code in the terminal where conexgram was started to complete setup."
        ),
    )

    entered = _prompt_verification_code(code)
    if entered is None:
        raise OnboardingError("Verification was not completed in this session.")

    base["telegram"]["allowed_user_ids"] = [owner["user_id"]]
    base["telegram"]["allowed_chat_ids"] = [owner["chat_id"]]
    _write_config(config_path, base)

    print("Configuration saved. Starting Conexgram...")


def _load_or_seed_config(config_path: Path) -> dict[str, Any]:
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(existing, dict):
                base = json.loads(example_config_text())
                for section, values in existing.items():
                    if isinstance(values, dict) and isinstance(base.get(section), dict):
                        base[section].update(values)
                    else:
                        base[section] = values
                return base
        except Exception:
            # Fall back to default template on malformed file.
            pass

    return json.loads(example_config_text())


def _write_config(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o600)


def _ask_bot_token() -> str:
    while True:
        token = input("Telegram bot token from BotFather: ").strip()
        if token:
            return token
        print("A valid Telegram bot token is required.")


def _fetch_bot_username(client: TelegramClient) -> Optional[str]:
    try:
        bot_info = client.get_me()
    except TelegramApiError as exc:
        raise OnboardingError(f"Invalid token or Telegram API error: {exc}") from exc

    username = bot_info.get("username") if isinstance(bot_info, dict) else None
    return str(username) if isinstance(username, str) else None


def _wait_owner_candidate(client: TelegramClient) -> Optional[dict[str, int]]:
    last_update_id = _latest_update_offset(client)

    while True:
        try:
            updates = client.get_updates(last_update_id)
        except TelegramApiError:
            time.sleep(3)
            continue

        for update in updates:
            message = client.parse_text_message(update)
            if message is None:
                continue

            candidate = {"chat_id": message.chat_id, "user_id": message.user_id}
            last_update_id = message.update_id + 1
            return candidate


def _latest_update_offset(client: TelegramClient) -> Optional[int]:
    try:
        current = client.get_updates(None)
    except TelegramApiError:
        return None

    if not current:
        return None
    return max(int(update.get("update_id", 0)) for update in current) + 1


def _prompt_verification_code(expected: str) -> Optional[str]:
    for _ in range(5):
        value = input("Paste verification code from Telegram: ").strip()
        if value == expected:
            return value
        print("Verification code mismatch. Try again.")
    return None


def _random_code(length: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))
