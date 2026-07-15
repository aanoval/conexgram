# Curl Installer

Recommended public install command:

```bash
curl -fsSL https://conexgram.com/install.sh | bash
```

The hosted script should serve `public/install.sh` directly.

For direct pip installs, run:

```bash
python3 -m pip install conexgram
python3 -m conexgram install-service
```

Or if you need setup + install in one pass:

```bash
bash scripts/pip_install_and_service.sh
```

## What it does

1. Creates a private virtual environment under `~/.conexgram/venv`
2. Installs Conexgram
3. Creates a `~/.local/bin/conexgram-gateway` symlink
4. Runs the guided setup if config does not exist
5. Runs `doctor --fix`
6. Installs and starts the user auto-start service

## Why this path matters

This is the cleanest public install surface for Conexgram:

- short enough to remember
- obvious from the landing page
- easy to share in posts, README copy, and demos

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
