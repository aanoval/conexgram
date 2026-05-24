"""CLI for Conexgram."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

from .app import GatewayApp, configure_logging
from .config import DEFAULT_CONFIG_PATH, example_config_text, init_config, load_config
from .onboarding import OnboardingError, run_first_run_onboarding
from .paths import ensure_dir, expand_path
from .service import install_service, uninstall_service
from .setup_wizard import run_setup
from .terminal_shell import TerminalShell


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Codex CLI from Telegram or a Conexgram terminal shell."
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG_PATH),
        help="Path to config JSON. Default: ~/.conexgram/config.json",
    )
    subparsers = parser.add_subparsers(dest="command")

    shell_parser = subparsers.add_parser("shell", help="Run the interactive Conexgram terminal shell.")
    shell_parser.add_argument("--cwd", default="", help="Working directory for the new CLI session.")

    codex_parser = subparsers.add_parser(
        "codex",
        add_help=False,
        help="Run native Codex CLI args using the active Conexgram profile.",
    )
    codex_parser.add_argument("codex_args", nargs=argparse.REMAINDER)

    subparsers.add_parser("run", help="Run the Telegram polling gateway.")

    init_parser = subparsers.add_parser("init-config", help="Create a local config file.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite existing config.")

    setup_parser = subparsers.add_parser("setup", help="Run interactive first-run setup.")
    setup_parser.add_argument("--force", action="store_true", help="Overwrite existing config.")

    doctor_parser = subparsers.add_parser("doctor", help="Validate config and local prerequisites.")
    doctor_parser.add_argument("--fix", action="store_true", help="Create configured local directories before validation.")
    service_parser = subparsers.add_parser("install-service", help="Install and start Conexgram as a user service.")
    service_parser.add_argument("--no-start", action="store_true", help="Install service without starting it.")
    subparsers.add_parser("uninstall-service", help="Remove the Conexgram user service.")
    subparsers.add_parser("example-config", help="Print example config JSON.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args, unknown_args = parser.parse_known_args(argv)
    if unknown_args and args.command != "codex":
        parser.error(f"unrecognized arguments: {' '.join(unknown_args)}")
    if args.command == "codex":
        args.codex_args = list(getattr(args, "codex_args", []) or []) + unknown_args
    command = args.command or "shell"
    config_path = Path(args.config).expanduser()

    if command == "example-config":
        print(example_config_text(), end="")
        return 0

    if command == "init-config":
        try:
            created = init_config(config_path, force=bool(args.force))
        except FileExistsError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Created config: {created}")
        print("Edit telegram.bot_token and the allowlist before running Conexgram.")
        return 0

    if command == "setup":
        try:
            run_setup(config_path, force=bool(args.force))
        except Exception as exc:
            print(f"Setup error: {exc}", file=sys.stderr)
            return 1
        return 0

    if command in {"doctor", "install-service"} and bool(getattr(args, "fix", False)):
        _doctor_fix_dirs(config_path)

    if command in {"run", "shell", "codex"}:
        config_loaded = False
        attempted_onboarding = False
        while not config_loaded:
            try:
                config = load_config(config_path)
                config_loaded = True
            except Exception as exc:
                if attempted_onboarding or not _is_first_run_config_error(exc):
                    print(f"Config error: {exc}", file=sys.stderr)
                    return 1
                try:
                    run_first_run_onboarding(config_path)
                except OnboardingError as onboarding_exc:
                    print(f"Onboarding error: {onboarding_exc}", file=sys.stderr)
                    return 1
                attempted_onboarding = True

    else:
        try:
            config = load_config(config_path)
        except Exception as exc:
            print(f"Config error: {exc}", file=sys.stderr)
            if command == "doctor":
                print()
                print("Fix:")
                print(f"  python3 -m conexgram --config {config_path} setup --force")
                print("  Then edit the generated config if needed and rerun doctor.")
            return 1

    if command == "doctor":
        print("Config OK")
        print(f"Config path: {config.config_path}")
        print(f"State dir: {config.gateway.state_dir}")
        print(f"Codex binary: {config.codex.binary}")
        print(f"Default working dir: {config.codex.default_working_dir}")
        print(f"Full access: {config.codex.full_access}")
        print(f"Session scope: {config.gateway.session_scope}")
        print("Next: start Conexgram or send /settings in Telegram.")
        return 0

    if command == "install-service":
        _doctor_fix_dirs(config_path)
        try:
            message = install_service(config_path, start=not bool(getattr(args, "no_start", False)))
        except Exception as exc:
            print(f"Service install error: {exc}", file=sys.stderr)
            return 1
        print(message)
        return 0

    if command == "uninstall-service":
        try:
            message = uninstall_service()
        except Exception as exc:
            print(f"Service uninstall error: {exc}", file=sys.stderr)
            return 1
        print(message)
        return 0

    if command == "run":
        configure_logging(config.gateway.log_level, config.gateway.state_dir)
        GatewayApp(config).run()
        return 0

    if command == "shell":
        cwd_arg = str(getattr(args, "cwd", "") or "").strip()
        cwd = Path(cwd_arg).expanduser().resolve() if cwd_arg else None
        return TerminalShell(config).run(cwd=cwd)

    if command == "codex":
        return TerminalShell(config).run_codex_args(list(getattr(args, "codex_args", []) or []))

    parser.error(f"Unknown command: {command}")
    return 2


def _doctor_fix_dirs(config_path: Path) -> None:
    if not config_path.exists():
        return
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    gateway = raw.get("gateway", {})
    codex = raw.get("codex", {})
    state_dir = gateway.get("state_dir")
    if state_dir:
        ensure_dir(expand_path(state_dir))
    default_working_dir = codex.get("default_working_dir")
    if default_working_dir:
        ensure_dir(expand_path(default_working_dir))
    for item in codex.get("workspace_roots", []):
        ensure_dir(expand_path(item))


def _is_first_run_config_error(exc: Exception) -> bool:
    if isinstance(exc, FileNotFoundError):
        return True

    message = str(exc)
    return message in {
        "telegram.bot_token is not configured",
        "Configure telegram.allowed_user_ids or telegram.allowed_chat_ids",
    }
