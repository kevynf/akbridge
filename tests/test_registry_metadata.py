from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MCP_NAME = "io.github.kevynf/akbridge"


def test_registry_metadata_matches_package() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    package = server["packages"][0]

    assert server["name"] == MCP_NAME
    assert server["version"] == project["version"]
    assert package["registryType"] == "pypi"
    assert package["identifier"] == project["name"]
    assert package["version"] == project["version"]
    assert package["transport"] == {"type": "stdio"}
    assert package["packageArguments"] == [{"type": "named", "name": "--mode", "value": "router"}]


def test_pypi_readme_contains_registry_ownership_marker() -> None:
    marker = f"mcp-name: {MCP_NAME}"

    assert marker in (ROOT / "README.md").read_text(encoding="utf-8")
    assert marker in (ROOT / "README.en.md").read_text(encoding="utf-8")
