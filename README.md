# Conexgram

[![CI](https://github.com/aanoval/conexgram/actions/workflows/ci.yml/badge.svg)](https://github.com/aanoval/conexgram/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/conexgram.svg)](https://pypi.org/project/conexgram/)
[![Python](https://img.shields.io/pypi/pyversions/conexgram.svg)](https://pypi.org/project/conexgram/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

Conexgram lets you **run Codex CLI from Telegram**.

Keep coding sessions running on your own computer and manage them from your phone.

> Your code, credentials, and compute stay local.

```text
Telegram -> Conexgram -> Codex CLI -> your local workspace -> Telegram
```

## Why Conexgram?

Conexgram lets you keep Codex running on your own computer while you manage coding sessions from Telegram. It is useful when you want to check builds, ask Codex to inspect a project, continue a coding session, or run supervised automation without opening your laptop.

Good fits:

- remote coding assistant for a personal workstation
- lightweight DevOps helper for trusted private machines
- Telegram-controlled Codex sessions for long-running work
- simple bridge for experimenting with future multi-agent workflows

## Features

- Telegram bot -> Codex CLI bridge
- Persistent Codex sessions per chat or per user
- `/new`, `/status`, `/sessions`, `/switch`, `/cwd`, `/workspace`
- `/model`, `/models`, `/reasoning`, `/mode`, `/preset`, `/fast`, `/fullaccess`, `/computer`
- `/settings`, `/permissions`, `/rename`, `/summary`, `/reset`, `/logs`, `/doctor`, `/version`
- `/typing`, `/progress`, `/silent`
- Optional local file send-back with `/sendfile`
- Telegram file upload into the active workspace
- Optional Telegram typing indicator while Codex is running
- Optional random progress messages for long-running Codex turns
- No third-party Python dependencies
- Works in foreground or as an auto-start service on macOS, Linux, or Windows
- Modular Python internals with a small agent-profile foundation for future multi-agent routing

## How it works

```text
Telegram message
  -> Telegram Bot API
  -> Conexgram on your machine
  -> Codex CLI
  -> final response
  -> Telegram reply
```

## What you can do from Telegram

- Start and resume Codex sessions
- Switch between safe, work, fast, power, and Computer Access presets
- Change model and reasoning effort per session
- Upload files into the active workspace
- Send local files back to Telegram
- Watch long-running tasks with typing and progress indicators
- Stop a running Codex turn from your phone

Conexgram keeps two layers of state:

- **Gateway session**: local session record managed by Conexgram
- **Codex thread**: actual Codex CLI thread id used for resume/continuation

## Requirements

- Python 3.11+
- `codex` CLI installed and authenticated
- A Telegram bot token from BotFather
- Your Telegram user id or allowed chat id

Quick check:

```bash
codex --version
codex exec --help
python3 --version
```

## 3-minute quickstart

Recommended install:

```bash
curl -fsSL https://conexgram.com/install.sh | bash
```

Or with pipx:

```bash
pipx install conexgram
conexgram setup
conexgram doctor --fix
conexgram install-service
```

Run in the foreground instead of installing a service:

```bash
conexgram run
```

Source checkout:

```bash
git clone https://github.com/aanoval/conexgram.git
cd conexgram
python3 -m conexgram setup
python3 -m conexgram doctor --fix
python3 -m conexgram run
```

Then send `/start` or `/settings` to your Telegram bot.

If the bot says you are unauthorized, it will show your Telegram user ID and chat ID. Add one of those IDs to `~/.conexgram/config.json`.

## Install from source

```bash
git clone https://github.com/aanoval/conexgram.git
cd conexgram
python3 -m conexgram init-config
```

For a guided setup:

```bash
python3 -m conexgram setup
```

Or edit config manually:

```bash
nano ~/.conexgram/config.json
```

Then validate:

```bash
python3 -m conexgram doctor
```

Run:

```bash
python3 -m conexgram run
```

You can also use the entry script:

```bash
python3 gateway.py run
```

## One-tap auto-start install

Create and edit `~/.conexgram/config.json` first, then run the installer for your platform.
If the config does not exist yet, `./scripts/install.sh` starts the guided setup first.

macOS or Linux:

```bash
./scripts/install.sh
```

macOS direct installer:

```bash
./scripts/install_launch_agent.sh
```

Linux user systemd installer:

```bash
./scripts/install_linux_systemd.sh
```

Windows PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1
```

The installers register Conexgram to launch automatically at login and start it immediately.

If Conexgram was installed with `pipx`, you can also use:

```bash
conexgram install-service
conexgram uninstall-service
```

## Example config

Key fields:

- `telegram.bot_token`
- `telegram.allowed_user_ids`
- `telegram.allowed_chat_ids`
- `codex.binary`
- `codex.default_working_dir`
- `codex.additional_writable_dirs`
- `codex.workspace_roots`
- `codex.model`
- `codex.reasoning_effort`
- `codex.mode`
- `codex.full_access`
- `codex.allow_runtime_full_access`
- `progress.typing_indicator`
- `progress.progress_messages`

Security details: see `docs/security.md`.
Troubleshooting: see `docs/troubleshooting.md`.

Generate a fresh config:

```bash
python3 -m conexgram example-config
```

## Commands

- `/new [working_dir]` — start a fresh Codex session
- `/status` — show the active session
- `/sessions` — list recent sessions
- `/switch <number_or_id>` — switch active session
- `/cwd [path]` — show or set working directory before Codex thread starts
- `/workspace [list|switch <path_or_number>|<path>]` — show or set allowed workspace
- `/model [name|default]` — show or set model for this session
- `/models` — list configured model presets
- `/reasoning low|medium|high|xhigh` — set reasoning effort
- `/mode safe|workspace|full|<preset>` — set execution mode or preset
- `/preset list|safe|work|fast|power|computer` — apply a common setup
- `/fast on|off` — toggle fast mode
- `/fullaccess status|on|off` — inspect or toggle full access if config allows it
- `/computer status|on|off` — user-friendly alias for full access
- `/settings` — show a friendly settings panel
- `/permissions` — show effective local access settings
- `/typing status|on|off|default` — control typing indicator for this session
- `/progress status|on|off|default` — control long-running progress messages
- `/silent status|on|off|default` — quickly silence or restore progress UX
- `/rename <title>` — rename active session
- `/summary` — show active session summary
- `/reset` — start a fresh default session
- `/logs [gateway|launchd]` — send a local log file
- `/tail` — show the latest Codex output for this session
- `/doctor` — run setup checks from Telegram
- `/version` — show Conexgram, Python, and Codex details
- `/config` — show config summary
- `/sendfile <path> [caption]` — send a local file to Telegram
- `/stop` — stop the running Codex process
- `/help` — show help

Any non-command text is forwarded to the active Codex session.

## Progress UX

Conexgram can show Telegram's `typing...` indicator while Codex is running. Telegram only displays each typing action briefly, so Conexgram refreshes it every few seconds.

Default:

```json
{
  "progress": {
    "typing_indicator": true,
    "typing_interval_seconds": 4,
    "progress_messages": true,
    "progress_interval_seconds": 60
  }
}
```

Set both values to `false` to keep the bot silent until Codex returns the final answer:

```json
{
  "progress": {
    "typing_indicator": false,
    "progress_messages": false
  }
}
```

Allowed Telegram users can also change progress UX per session:

```text
/typing off
/progress off
/silent on
/silent default
```

## Security notes

Conexgram can expose meaningful access to your local machine through Codex CLI.

Recommended defaults:

- keep the bot private
- use `allowed_user_ids` and/or `allowed_chat_ids`
- leave `full_access` as `false` unless you explicitly want unrestricted Codex execution
- leave `allow_runtime_full_access` as `false` unless you want Telegram users to toggle full access
- set `workspace_roots` so `/workspace`, `/cwd`, and `/sendfile` stay inside known folders
- run it on a dedicated machine or workspace if possible

When `codex.full_access=true`, Conexgram adds:

```bash
--dangerously-bypass-approvals-and-sandbox
```

Only enable that if you understand the risk.

### Runtime full access from Telegram

Telegram users cannot enable full access by default. The machine owner must opt in locally first:

```json
{
  "codex": {
    "full_access": false,
    "allow_runtime_full_access": true
  }
}
```

After restarting Conexgram, allowed Telegram users can toggle it:

```text
/fullaccess on
/fullaccess off
/fullaccess status
/mode full
/mode safe
```

Keep `allow_runtime_full_access=false` if the Telegram bot should never be able to switch Codex into unrestricted local execution.

For non-technical users, `/computer` is the friendly alias:

```text
/computer status
/computer on
/computer off
/confirm computer
```

Common presets:

```text
/preset safe
/preset work
/preset fast
/preset power
/preset computer
```

`/preset computer` also requires `allow_runtime_full_access=true`.

Conexgram is unofficial and is not affiliated with OpenAI.

## Auto-start services

macOS uses a LaunchAgent:

```bash
./scripts/install_launch_agent.sh
```

Stop macOS:

```bash
launchctl bootout "gui/$(id -u)/com.conexgram.agent"
```

Linux uses a user systemd service:

```bash
systemctl --user status conexgram.service
systemctl --user disable --now conexgram.service
```

Windows uses a Scheduled Task:

```powershell
Get-ScheduledTask -TaskName Conexgram
Stop-ScheduledTask -TaskName Conexgram
```

Logs:

```text
~/.conexgram/gateway.log
~/.conexgram/logs/
```

## Internal structure

Key modules:

- `conexgram/app.py` — gateway loop and Telegram update processing
- `conexgram/commands.py` — Telegram slash commands
- `conexgram/codex_runner.py` — Codex CLI execution and JSON event parsing
- `conexgram/progress.py` — typing indicator and long-running progress messages
- `conexgram/session_store.py` — local session persistence
- `conexgram/agents.py` — future multi-agent profile primitives

## Packaging

For users:

```bash
pipx install conexgram
```

For local editable development:

```bash
pip install -e .
conexgram --help
```

Release details: see `docs/pypi-release.md`.
Curl installer details: see `docs/curl-install.md`.

## Project status

Conexgram is intentionally small and focused. The goal is a clean, understandable bridge for remote Codex usage over Telegram — not a full remote agent platform.
