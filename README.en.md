<div align="center">
  <h1>AKBridge</h1>
  <p><strong>Automatically connect AKShare public interfaces to MCP.</strong></p>
  <p>
    <a href="https://github.com/kevynf/akbridge/blob/master/README.md">简体中文</a> |
    <a href="https://github.com/kevynf/akbridge/blob/master/README.en.md">English</a>
  </p>
  <p>
    <a href="https://github.com/kevynf/akbridge/actions/workflows/akbridge-maintenance.yml"><img alt="Continuous integration" src="https://github.com/kevynf/akbridge/actions/workflows/akbridge-maintenance.yml/badge.svg"></a>
    <img alt="Python 3.11+" src="https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white">
    <a href="https://github.com/akfamily/akshare"><img alt="Data: AKShare" src="https://img.shields.io/badge/Data%20Science-AKShare-green"></a>
    <a href="https://modelcontextprotocol.io/"><img alt="MCP stdio and SSE" src="https://img.shields.io/badge/MCP-stdio%20%7C%20SSE-6f42c1"></a>
    <a href="LICENSE"><img alt="MIT License" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  </p>
</div>

Automatically exposes AKShare public interfaces as MCP tools with routed
discovery, structured output, per-interface acceptance, and automated
validation and maintenance. The current AKShare baseline, interface count, and
validation results are recorded in the [generated report](artifacts/maintenance/latest.json).

## Why choose AKBridge

AKShare provides broad access to financial data, but connecting it directly to an AI assistant still
requires Python invocation, function selection, argument construction, DataFrame conversion, and
ongoing adaptation to upstream releases. AKBridge turns that work into an installable, searchable,
and validated MCP service, so MCP-enabled LLM clients, agents, and tools can use AKShare without a
custom integration for every application.

AKBridge is intended for developers building financial research assistants, market-analysis agents,
data retrieval tools, and other LLM-based financial applications. It is particularly useful when you:

- need broad coverage of AKShare public interfaces instead of a small, manually maintained tool set;
- want users to express requests in natural language while an MCP-enabled LLM client, agent, or tool
  performs discovery and structured invocation;
- want to expose a small set of stable router tools instead of placing every interface in the LLM
  context;
- need consistent JSON output, pagination, summaries, and error classification across varied Python
  return types;
- want AKShare updates to be discovered automatically and checked through per-interface acceptance.

AKBridge does not replace AKShare: AKShare retrieves the data, while AKBridge makes those capabilities
reliably available to MCP-enabled LLM clients, agents, and tools. Provider authentication, CAPTCHAs,
anti-scraping controls, rate limits, and network restrictions still apply and are reported separately.

## Automated maintenance

GitHub Actions and Dependabot monitor AKShare updates. Updates within the current major version are
merged only after the complete validation gate passes. See the
[automated validation and maintenance guide](docs/automated-validation-and-maintenance.zh-CN.md)
for schedules, gates, and repository settings.

## Installation

Use [`uv`](https://docs.astral.sh/uv/getting-started/installation/) to install AKBridge as an
isolated command-line tool. `uv` manages the required Python 3.11+ environment.

Install the current GitHub version:

```powershell
uv tool install "git+https://github.com/kevynf/akbridge.git"
uv tool update-shell
```

Open a new terminal and verify the installation:

```powershell
akbridge --help
akbridge --mode router
```

The second command starts the stdio MCP server and waits for a client. No additional terminal output
is expected; press `Ctrl+C` to stop it.

After the package is published to PyPI, install it with:

```powershell
uv tool install akbridge
```

Upgrade or uninstall:

```powershell
# GitHub installation
uv tool install --force "git+https://github.com/kevynf/akbridge.git"

# PyPI installation
uv tool upgrade akbridge

uv tool uninstall akbridge
```

MCP client configuration:

```json
{
  "mcpServers": {
    "akbridge": {
      "command": "akbridge",
      "args": ["--mode", "router"]
    }
  }
}
```

## Development

```powershell
git clone https://github.com/kevynf/akbridge.git
cd akbridge
uv sync --extra dev
uv run --no-sync python -m pytest -q
uv run akbridge-accept manifest
uv run --no-sync akbridge-accept run --offline --workers 4
uv run --no-sync akbridge-maintain ci --strict --check-latest
```

Run the stdio MCP server:

```powershell
uv run akbridge
```

Router mode:

```powershell
uv run akbridge --mode router
```

## Acceptance model

`akbridge-accept manifest` writes a versioned inventory of all discovered
interfaces to `artifacts/acceptance/manifest.json`. Each interface is checked
for discovery and a valid input schema. Calls run in isolated subprocesses so
one stalled provider does not block the run. Results are atomically saved after
each interface and can be resumed:

```powershell
uv run akbridge-accept run --limit 20 --timeout 30
uv run akbridge-accept run --resume --limit 100 --timeout 30 --workers 4
uv run akbridge-accept run --resume --retry-status timeout --timeout 60
uv run akbridge-accept report
```

Parameterized interfaces are marked `fixture_required` until their acceptance
inputs are added to `artifacts/acceptance/fixtures.json`.

External data sources can be unavailable, rate-limited, or require credentials.
Each call result is therefore recorded independently instead of aborting the
entire acceptance run.

An interface passes MCP adapter acceptance when its input Schema, metadata, and
single-tool MCP construction are valid. Data-provider availability is reported
separately as `passed`, `passed_empty`, `failed`, or `timeout`; provider failures
are never hidden.

For a fully offline contract check, use adapter-only acceptance. It never calls
a third-party provider and needs no human or LLM decision:

```powershell
uv run akbridge-accept run --offline --workers 4
uv run akbridge-maintain ci --strict
```

## Current limitations

- The current Skill is a general usage guide, not an installable set of client- and domain-specific
  modules.
- Current RAG support provides an interface catalog and lexical search, without financial knowledge,
  terminology mapping, vector recall, or reranking.

## Future directions

- Provide installable, composable financial Skills for major LLM clients and agent frameworks.
- Add curated tool descriptions, parameter semantics, examples, and result summaries for frequently
  used interfaces to improve LLM tool selection, argument generation, and result interpretation.
- Build a source-backed, versioned financial knowledge base with Chinese and English terminology
  mapping.
- Add hybrid RAG retrieval and automated acceptance for knowledge recall, tool selection, and
  parameter completeness.

## Project documentation

- [Automated validation and maintenance](docs/automated-validation-and-maintenance.zh-CN.md)
- [Security policy](SECURITY.md)

## Contributing

Issues and pull requests are welcome. Read the [contribution guide](CONTRIBUTING.md)
before submitting changes.

## License

AKBridge is open source under the [MIT License](LICENSE).
