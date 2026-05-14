# Security

Conexgram connects Telegram to Codex CLI on your own computer. Treat it like remote access software.

## Access modes

### Safe

Safe mode keeps Conexgram conservative. It does not add unrestricted Codex execution flags.

Use this for first-time setup and daily casual use.

### Workspace

Workspace mode is intended for project work. Configure `codex.workspace_roots` so `/workspace`, `/cwd`, and `/sendfile` stay inside known folders.

Recommended default:

```json
{
  "codex": {
    "default_working_dir": "~/ConexgramWorkspace",
    "workspace_roots": ["~/ConexgramWorkspace"]
  }
}
```

### Computer Access

Computer Access maps to Codex full-access execution. It can give Codex broad local machine access.

It is disabled by default. To allow Telegram users to request it:

```json
{
  "codex": {
    "allow_runtime_full_access": true
  }
}
```

Even then, users must confirm it from Telegram:

```text
/computer on
/confirm computer
```

## Telegram allowlist

Always set at least one of:

```json
{
  "telegram": {
    "allowed_user_ids": [123456789],
    "allowed_chat_ids": []
  }
}
```

Do not expose your bot token. Do not commit `~/.conexgram/config.json`.

## File sending

`/sendfile` can send local files to Telegram. Keep `workspace_roots` narrow so files outside your intended workspace cannot be sent.

## Logging

Conexgram writes logs under `~/.conexgram`. Logs may include command output from Codex. Avoid sending secrets to Codex if you do not want them in local logs.

## Recommended public defaults

```json
{
  "codex": {
    "mode": "workspace",
    "full_access": false,
    "allow_runtime_full_access": false,
    "default_working_dir": "~/ConexgramWorkspace",
    "workspace_roots": ["~/ConexgramWorkspace"]
  }
}
```
