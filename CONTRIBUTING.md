# Contributing

Thanks for helping improve Conexgram.

## Local setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
python -m unittest discover -s tests
```

## Checks before opening a PR

```bash
python -m compileall conexgram
python -m unittest discover -s tests
bash -n scripts/install.sh scripts/install_launch_agent.sh scripts/install_linux_systemd.sh
```

## Design principles

- Keep setup simple for non-technical users.
- Keep security defaults conservative.
- Avoid adding dependencies unless they clearly improve reliability or UX.
- Prefer small, understandable modules.
- Document any feature that changes local machine access.

## Pull requests

Please include:

- what changed
- why it matters
- how you tested it
- any security implications
