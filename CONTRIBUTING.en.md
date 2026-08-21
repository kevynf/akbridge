# Contributing Guide

[简体中文](CONTRIBUTING.md)

Thank you for contributing to AKBridge. Code, documentation, and test changes must remain reproducible and must not require human or LLM intervention for validation.

## Development Environment

```powershell
uv sync --group dev
```

## Pre-commit Checks

```powershell
uv run --no-sync python -m pytest -q
uv run --no-sync ruff check src tests
uv run --no-sync ruff format --check src tests
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

Releases are version-driven. Update `pyproject.toml`, `src/akbridge/__init__.py`, and both version fields in `server.json`, then merge the change into the default branch. After the full CI succeeds, `auto-release.yml` creates the matching tag and GitHub Release (for example, `v0.1.3`) only if the default branch still points to the validated commit. It then reuses `publish-pypi.yml` to publish to PyPI and the MCP Registry. Existing releases are a no-op; a tag without a release, or a tag pointing elsewhere, fails for maintainer review. Manually published GitHub Releases still trigger the same publishing workflow.
