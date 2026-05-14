"""Install and uninstall Conexgram as a user auto-start service."""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH
from .paths import ensure_dir, expand_path


SERVICE_LABEL = "com.conexgram.agent"
SERVICE_NAME = "conexgram.service"
WINDOWS_TASK_NAME = "Conexgram"


def install_service(config_path: Path = DEFAULT_CONFIG_PATH, start: bool = True) -> str:
    system = platform.system()
    executable = shutil.which("conexgram")
    if executable is None:
        raise RuntimeError("The `conexgram` command was not found in PATH. Install the package first.")
    config = str(expand_path(config_path))
    if system == "Darwin":
        return _install_macos(executable, config, start)
    if system == "Linux":
        return _install_linux(executable, config, start)
    if system == "Windows":
        return _install_windows(executable, config, start)
    raise RuntimeError(f"Unsupported operating system: {system}")


def uninstall_service() -> str:
    system = platform.system()
    if system == "Darwin":
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{SERVICE_LABEL}"], check=False)
        plist = Path.home() / "Library" / "LaunchAgents" / f"{SERVICE_LABEL}.plist"
        plist.unlink(missing_ok=True)
        return f"Removed macOS LaunchAgent: {SERVICE_LABEL}"
    if system == "Linux":
        subprocess.run(["systemctl", "--user", "disable", "--now", SERVICE_NAME], check=False)
        service = Path.home() / ".config" / "systemd" / "user" / SERVICE_NAME
        service.unlink(missing_ok=True)
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
        return f"Removed Linux user service: {SERVICE_NAME}"
    if system == "Windows":
        subprocess.run(["schtasks", "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"], check=False)
        return f"Removed Windows Scheduled Task: {WINDOWS_TASK_NAME}"
    raise RuntimeError(f"Unsupported operating system: {system}")


def _install_macos(executable: str, config: str, start: bool) -> str:
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    ensure_dir(launch_agents)
    plist = launch_agents / f"{SERVICE_LABEL}.plist"
    state_dir = Path.home() / ".conexgram"
    ensure_dir(state_dir)
    plist.write_text(
        f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{SERVICE_LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>{executable}</string>
    <string>--config</string>
    <string>{config}</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{state_dir}/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>{state_dir}/launchd.err.log</string>
</dict>
</plist>
""",
        encoding="utf-8",
    )
    if start:
        subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{SERVICE_LABEL}"], check=False)
        subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], check=True)
        subprocess.run(["launchctl", "enable", f"gui/{os.getuid()}/{SERVICE_LABEL}"], check=True)
        subprocess.run(["launchctl", "kickstart", "-k", f"gui/{os.getuid()}/{SERVICE_LABEL}"], check=True)
    return f"Installed macOS LaunchAgent: {plist}"


def _install_linux(executable: str, config: str, start: bool) -> str:
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    ensure_dir(systemd_dir)
    service = systemd_dir / SERVICE_NAME
    service.write_text(
        f"""[Unit]
Description=Conexgram Telegram connector for Codex CLI
After=network-online.target

[Service]
Type=simple
ExecStart={executable} --config {config} run
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""",
        encoding="utf-8",
    )
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
    if start:
        subprocess.run(["systemctl", "--user", "enable", "--now", SERVICE_NAME], check=True)
    return f"Installed Linux user service: {service}"


def _install_windows(executable: str, config: str, start: bool) -> str:
    command = f'"{executable}" --config "{config}" run'
    subprocess.run(
        ["schtasks", "/Create", "/SC", "ONLOGON", "/TN", WINDOWS_TASK_NAME, "/TR", command, "/F"],
        check=True,
    )
    if start:
        subprocess.run(["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME], check=True)
    return f"Installed Windows Scheduled Task: {WINDOWS_TASK_NAME}"
