# AKBridge Automated Validation and Maintenance

[简体中文](automated-validation-and-maintenance.zh-CN.md)

This document defines AKBridge's automated maintenance boundaries. Default commands are deterministic:
they do not call an LLM, wait for human input, or treat temporary provider outages as MCP adapter regressions.

## Validation layers

| Layer | Command | Third-party data source | Failure meaning |
| --- | --- | ---: | --- |
| Local contract | `akbridge-maintain ci --strict` | No | Discovery, schema, catalog, or routing regression. |
| Offline per-interface | `akbridge-accept run --offline` | No | A local adapter contract failed. |
| Provider probe | `akbridge-maintain ci --provider` | Yes | Upstream network, anti-scraping, credentials, data format, or runtime changes. |

The first two layers should be stable commit and scheduled CI gates. Run provider probes separately and retain their reports; transient network failures must not replace the contract baseline.

## Upgrade workflow

1. Upgrade `akshare` and its lock file on an isolated branch.
2. Run the strict offline gate and generate a new manifest, semantic catalog, and report.
3. Inspect `artifacts/maintenance/latest.json`: added interfaces are informational; removed interfaces, signature changes, and schema changes are regressions in strict mode.
4. Run the full offline per-interface acceptance to confirm discovery and schema generation.
5. Run `--provider` only when upstream availability needs assessment; classify failures using the ledger scope.
6. Dependabot updates within the same major version may merge automatically after the complete gate passes; major upgrades or unusual changes require review.
7. Update `artifacts/acceptance/manifest.json` only after explicitly accepting the new compatibility boundary.

## Commands

Generate an auditable baseline:

```powershell
.venv\Scripts\python.exe -m akbridge.maintenance manifest `
  --output artifacts\acceptance\manifest.json
.venv\Scripts\python.exe -m akbridge.maintenance catalog `
  --output artifacts\catalog.json
```

Run the strict offline check:

```powershell
.venv\Scripts\python.exe -m akbridge.maintenance ci --strict `
  --baseline artifacts\acceptance\manifest.json `
  --current artifacts\maintenance\manifest.json `
  --catalog artifacts\catalog.json `
  --report artifacts\maintenance\latest.json
```

Append `--check-latest` in scheduled jobs to query the latest AKShare release on PyPI. Network unavailability is reported as `unavailable`, not as an adapter regression. Add `--fail-on-update` only when a newly available release should fail the gate.

Run offline per-interface adapter acceptance:

```powershell
.venv\Scripts\python.exe -m akbridge.acceptance run --offline --workers 4 `
  --output artifacts\acceptance\runs\offline.json
.venv\Scripts\python.exe -m akbridge.acceptance report `
  --run artifacts\acceptance\runs\offline.json
```

The report writes `SUMMARY.md` and `SUMMARY.en.md`, the machine-readable `summary.json`, the status image, and the per-interface `ledger.csv`. The documentation section records document-chunk count, required-field completeness, public-interface coverage, and unlinked interfaces.

## Exit codes and reports

`akbridge-maintain ci --strict` exits nonzero when the discovered interface count falls below its minimum, interfaces are removed beyond `--max-removed`, existing signatures or input-schema hashes change, or catalog, schema, or routing validation fails.

Reports contain a stable `current_fingerprint`. The same AKShare version and local code should produce the same fingerprint; generation time is excluded. Metadata-hash changes are recorded, while only signature and schema changes are strict compatibility regressions.

## GitHub Actions

Scheduled tasks run in GitHub-hosted runners and do not create local scheduled jobs or resident processes. The maintenance workflow runs offline tests, the strict gate, and the version-matched AKShare documentation build; it uploads the maintenance and acceptance reports, including both language summaries. The provider workflow runs a monthly isolated upstream probe and retains the complete ledger. Dependabot opens daily AKShare update PRs, and the auto-merge workflow verifies that only the pinned dependency files changed before merging a same-major upgrade.

Repository settings should enable read and write workflow permissions when workflows need to update generated status artifacts. Branch protection should require the maintenance `offline-contract` check. No workflow uses an LLM or interactive human input.
