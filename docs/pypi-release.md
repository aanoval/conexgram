# PyPI Release

Conexgram is designed to be installed as a PyPI package.

## User install path

Recommended:

```bash
pipx install conexgram
conexgram setup
conexgram doctor --fix
conexgram install-service
```

Upgrade:

```bash
pipx upgrade conexgram
```

Source install:

```bash
pipx install git+https://github.com/aanoval/conexgram.git
```

## Maintainer release checklist

1. Update `pyproject.toml` version.
2. Update `conexgram/__init__.py` version.
3. Run local checks:

```bash
python -m compileall conexgram
python -m unittest discover -s tests
python -m build
twine check dist/*
```

4. Commit changes.
5. Create a GitHub release tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

6. GitHub Actions publishes to PyPI through Trusted Publishing.

## PyPI Trusted Publishing

Configure this project on PyPI:

- Owner: `aanoval`
- Repository: `conexgram`
- Workflow: `publish.yml`
- Environment: `pypi`

Do not store PyPI API tokens in the repository.
