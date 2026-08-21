# Repository Guidelines

## Project Structure & Module Organization

AKBridge is a Python 3.11+ MCP adapter for AKShare. Runtime code lives in `src/akbridge/`: `server.py` defines transports and tools, `catalog.py` discovers APIs, `router.py` handles search and routing, and `serialization.py` normalizes provider results. Reliability, acceptance, maintenance, document indexing, and credential handling are kept in focused sibling modules. Tests live in `tests/` and follow the runtime areas (`test_router.py`, `test_reliability.py`, and similar). Generated catalogs and acceptance reports belong under `artifacts/`; operational documentation is in `docs/`. GitHub automation is defined in `.github/workflows/`.

## Build, Test, and Development Commands

- `uv sync --group dev` installs the pinned runtime and development dependencies.
- `uv run akbridge --mode router` starts the recommended stdio MCP server locally.
- `uv run --no-sync python -m pytest -q` runs the complete test suite.
- `uv run --no-sync ruff check src tests` checks imports and lint rules.
- `uv run --no-sync ruff format --check src tests` verifies formatting.
- `uv run --no-sync akbridge-accept run --offline --workers 4` validates every adapter contract without contacting providers.
- `uv run --no-sync akbridge-maintain ci --strict --check-latest` runs the strict maintenance gate.
- `uv build` builds wheel and source distributions.

Run focused tests during development, for example `uv run pytest tests/test_router.py -q`, then run the full pre-commit sequence documented in `CONTRIBUTING.en.md`.

## Coding Style & Naming Conventions

Use four-space indentation, type annotations, and concise docstrings. Ruff targets Python 3.11 with a 100-character line limit and enforces `E`, `F`, `I`, `UP`, `B`, and `SIM` rules. Use `snake_case` for modules, functions, fixtures, and variables; `PascalCase` for classes; and `UPPER_SNAKE_CASE` for constants. Keep modules narrowly scoped and preserve existing public CLI and MCP contracts.

## Testing Guidelines

Tests use pytest and are named `tests/test_<area>.py`; test functions begin with `test_`. Add regression coverage for behavioral changes, including async, serialization, or router edge cases where relevant. Provider-independent tests are preferred. Changes to interface contracts must update `artifacts/acceptance/manifest.json` and `artifacts/catalog.json`, while preserving `--mode all` compatibility.

## Commit & Pull Request Guidelines

History uses concise conventional prefixes such as `chore:`, `docs:`, and `release:`; use an imperative subject and keep each commit focused. Pull requests should explain the behavior and motivation, link related issues, and list validation commands run. Keep Chinese and English documentation synchronized. Never commit credentials, cookies, API keys, real data snapshots, virtual environments, or local configuration. Report vulnerabilities through a private security advisory, not a public issue.
