# Contributing Guide

[简体中文](CONTRIBUTING.md)

Thank you for contributing to AKBridge. Code, documentation, and test changes must remain reproducible and must not require human or LLM intervention for validation.

## Development Environment

```powershell
uv sync --extra dev
```

## Pre-commit Checks

```powershell
uv run --no-sync python -m pytest -q
uvx --from ruff ruff check src tests
uvx --from ruff ruff format --check src tests
uv run --no-sync akbridge-accept run --offline --workers 4
uv run --no-sync akbridge-maintain ci --strict --check-latest
uv build
uv sync --extra release
uv run --no-sync twine check dist/*
```

Offline acceptance does not contact third-party providers or call an LLM. A separate scheduled workflow performs real provider probes.

## Change Requirements

- Preserve `--mode all` compatibility and update router mode and the semantic catalog together.
- Regenerate `artifacts/acceptance/manifest.json` and `artifacts/catalog.json` when changing an interface contract.
- Do not commit credentials, cookies, API keys, real data snapshots, or local environment directories.
- Keep Chinese and English documentation in sync. Use English topic names and language suffixes for filenames, for example `automated-validation-and-maintenance.zh-CN.md`.

## Publishing to PyPI

The release workflow uses PyPI Trusted Publishing and stores no API token in the repository. Before the first release, configure the GitHub Publisher for project `akbridge` on PyPI with repository `kevynf/akbridge`, workflow `publish-pypi.yml`, and environment `pypi`.

After synchronizing the version in `pyproject.toml` and `src/akbridge/__init__.py`, create the matching tag (for example, `v0.1.0`) and publish a GitHub Release. The workflow validates the tag, runs tests and offline acceptance, builds the version-matched AKShare documentation index, verifies wheel/sdist metadata, and publishes through OIDC.
