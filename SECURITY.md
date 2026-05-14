# Security Policy

Conexgram is remote-control software for Codex CLI. Please report security issues privately.

## Reporting a vulnerability

Open a private security advisory on GitHub, or contact the maintainer through the repository owner profile.

Do not open public issues for vulnerabilities involving:

- Telegram bot token exposure
- bypassing the Telegram allowlist
- unauthorized Computer Access
- arbitrary file read through `/sendfile`
- arbitrary file write through upload handling
- command execution outside configured expectations

## Supported versions

Until `1.0.0`, only the latest release is supported.

## Security defaults

- Computer Access is disabled by default.
- Runtime Computer Access requires local config opt-in.
- Telegram allowlist is required.
- Workspace roots should be kept narrow.
