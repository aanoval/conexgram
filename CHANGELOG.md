# Changelog

All notable changes to Conexgram will be documented in this file.

The format follows a simple versioned changelog, and this project uses semantic versioning before `1.0.0` with alpha releases for early public testing.

## 0.1.0-alpha

Initial public alpha.

### Added

- Telegram bridge for running Codex CLI from a private bot
- Persistent Codex sessions per chat or per user
- Guided setup wizard with safe workspace defaults
- PyPI-ready `conexgram` CLI entry point
- `pipx` and curl installer documentation
- `conexgram install-service` and `conexgram uninstall-service`
- Auto-start service support for macOS, Linux, and Windows
- Slash commands for sessions, model selection, reasoning effort, presets, workspace switching, and status
- Friendly `/settings` panel with inline keyboard buttons
- Typing indicator and long-running progress messages
- Per-session silent/progress controls
- Optional Computer Access mode with local opt-in and Telegram confirmation
- File upload from Telegram into the active workspace
- `/sendfile` and `/tail` helper commands
- Codex turn timeout via `max_turn_seconds`
- Log retention limits via `max_log_days` and `max_log_mb`
- Basic multi-worker queue support
- Security documentation and troubleshooting guide
- CI workflow and PyPI Trusted Publishing workflow

### Security

- Telegram allowlist is required
- Computer Access is disabled by default
- Runtime Computer Access requires local config opt-in
- Computer Access activation requires `/confirm computer`
- Workspace defaults avoid using the full home directory

### Notes

- This is an alpha release for early adopters.
- Windows support is included but should be treated as experimental until tested on real Windows machines.
- A live Telegram demo and README GIF should be added before a broader launch.
