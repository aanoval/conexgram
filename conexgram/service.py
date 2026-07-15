"""Install and uninstall Conexgram as a user auto-start service."""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from xml.sax.saxutils import escape

from .config import DEFAULT_CONFIG_PATH
from .paths import ensure_dir, expand_path


SERVICE_LABEL = "com.conexgram.agent"
SERVICE_NAME = "conexgram.service"
WINDOWS_TASK_NAME = "Conexgram"


def install_service(
    config_path: Path = DEFAULT_CONFIG_PATH,
    start: bool = True,
    runtime_binary: str = "conexgram",
) -> str:
    system = platform.system()
    python_executable = sys.executable
    resolved_runtime = shutil.which(runtime_binary) or runtime_binary
    config = str(expand_path(config_path))
    if system == "Darwin":
        return _install_macos(python_executable, config, resolved_runtime, start)
    if system == "Linux":
        return _install_linux(python_executable, config, resolved_runtime, start)
    if system == "Windows":
        return _install_windows(python_executable, config, resolved_runtime, start)
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


def _install_macos(
    python_executable: str,
    config: str,
    runtime_binary: str,
    start: bool,
) -> str:
    launch_agents = Path.home() / "Library" / "LaunchAgents"
    ensure_dir(launch_agents)
    plist = launch_agents / f"{SERVICE_LABEL}.plist"
    state_dir = Path.home() / ".conexgram"
    ensure_dir(state_dir)
    escaped_python = escape(python_executable)
    escaped_config = escape(config)
    escaped_runtime = escape(runtime_binary)
    escaped_state_dir = escape(str(state_dir))
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
    <string>{escaped_python}</string>
    <string>-m</string>
    <string>conexgram</string>
    <string>--runtime-bin</string>
    <string>{escaped_runtime}</string>
    <string>--config</string>
    <string>{escaped_config}</string>
    <string>run</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{escaped_state_dir}/launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>{escaped_state_dir}/launchd.err.log</string>
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


def _install_linux(
    python_executable: str,
    config: str,
    runtime_binary: str,
    start: bool,
) -> str:
    systemd_dir = Path.home() / ".config" / "systemd" / "user"
    ensure_dir(systemd_dir)
    service = systemd_dir / SERVICE_NAME
    service.write_text(
        f"""[Unit]
Description=Conexgram Telegram connector for Codex CLI
After=network-online.target

[Service]
Type=simple
ExecStart={shlex.quote(python_executable)} -m conexgram --runtime-bin {shlex.quote(runtime_binary)} --config {shlex.quote(config)} run
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


def _install_windows(
    python_executable: str,
    config: str,
    runtime_binary: str,
    start: bool,
) -> str:
    command = (
        f'"{python_executable}" -m conexgram --runtime-bin "{runtime_binary}" '
        f'--config "{config}" run'
    )
    subprocess.run(
        ["schtasks", "/Create", "/SC", "ONLOGON", "/TN", WINDOWS_TASK_NAME, "/TR", command, "/F"],
        check=True,
    )
    if start:
        subprocess.run(["schtasks", "/Run", "/TN", WINDOWS_TASK_NAME], check=True)
    return f"Installed Windows Scheduled Task: {WINDOWS_TASK_NAME}"
