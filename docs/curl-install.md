# Curl Installer

Recommended public install command:

```bash
curl -fsSL https://conexgram.com/install.sh | bash
```

The hosted script should serve `public/install.sh`.

## What it does

1. Creates a private virtual environment under `~/.conexgram/venv`
2. Installs Conexgram
3. Creates a `~/.local/bin/conexgram` symlink
4. Runs the guided setup if config does not exist
5. Runs `doctor --fix`
6. Installs and starts the user auto-start service

## Before PyPI is live

The script first tries:

```bash
pip install conexgram
```

If PyPI is not available yet, it falls back to:

```bash
pip install git+https://github.com/aanoval/conexgram.git
```

Override the package source:

```bash
CONEXGRAM_PACKAGE="git+https://github.com/aanoval/conexgram.git" \
  curl -fsSL https://conexgram.com/install.sh | bash
```

## Domain setup

Serve the script at:

```text
https://conexgram.com/install.sh
```

Recommended headers:

```text
Content-Type: text/x-shellscript; charset=utf-8
Cache-Control: no-cache
```
