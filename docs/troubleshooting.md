# Troubleshooting

## `conexgram-gateway` command not found

Install with pipx:

```bash
pipx install conexgram
```

Or run from a source checkout:

```bash
python3 -m conexgram --help
```

## Config error: Telegram token is not configured

Run:

```bash
conexgram-gateway setup --force
```

Then paste a real BotFather token.

## I do not know my Telegram user ID

Send `/start` to your bot. If you are not authorized, Conexgram replies with:

- your Telegram user ID
- the current chat ID

Add one of those IDs to `~/.conexgram/config.json`.

## Conexgram Agent runtime not found

Install and authenticate Conexgram Agent first, then run:

```bash
conexgram --version
conexgram exec --help
conexgram-gateway doctor --fix
```

## The service starts but Telegram does not respond

Check logs:

```bash
cat ~/.conexgram/gateway.log
cat ~/.conexgram/launchd.err.log
```

On Linux:

```bash
systemctl --user status conexgram.service
journalctl --user -u conexgram.service -n 100
```

## macOS service cannot find Codex

GUI services may have a different `PATH` from your terminal. Prefer installing Conexgram with `pipx`, then run:

```bash
conexgram-gateway install-service
```

## Computer Access does not turn on

Computer Access is disabled by default. Enable local opt-in first:

```json
{
  "codex": {
    "allow_runtime_full_access": true
  }
}
```

Restart Conexgram, then use:

```text
/computer on
/confirm computer
```

## Telegram inline buttons do nothing

Restart Conexgram after upgrading. The bot must receive `callback_query` updates.

## File upload does not appear in the workspace

Uploaded Telegram documents are saved under:

```text
<active-workspace>/telegram_uploads/
```

Use `/workspace` to see the active workspace.
