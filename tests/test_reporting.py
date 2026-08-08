from __future__ import annotations

import csv
import json
from pathlib import Path

from akbridge.acceptance import classify_result, write_acceptance_artifacts


def test_classify_result_separates_adapter_and_upstream_failures() -> None:
    assert classify_result({"status": "worker_failed"})[0] == "mcp_adapter"
    assert classify_result({"status": "timeout"})[0] == "upstream_timeout"
    assert classify_result({"status": "failed", "error": "ConnectionError: reset"}) == (
        "upstream_transport",
        "ConnectionError",
    )
    assert classify_result({"status": "failed", "error": "KeyError: field"}) == (
        "akshare_runtime",
        "KeyError",
    )


def test_write_acceptance_artifacts_creates_full_ledger(tmp_path: Path) -> None:
    run_path = tmp_path / "run.json"
    run_path.write_text(
        json.dumps(
            {
                "akshare_version": "1.0",
                "generated_at": "2026-01-01T00:00:00Z",
                "results": [
                    {"name": "good", "status": "passed"},
                    {"name": "bad", "status": "failed", "error": "KeyError: field"},
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = write_acceptance_artifacts(run_path, tmp_path / "out")

    assert summary["interface_count"] == 2
    assert summary["mcp_adapter_accepted"] == 2
    assert summary["mcp_adapter_acceptance_rate"] == 1.0
    assert summary["status_rates"] == {"passed": 0.5, "failed": 0.5}
    markdown = (tmp_path / "out" / "SUMMARY.md").read_text(encoding="utf-8")
    english_markdown = (tmp_path / "out" / "SUMMARY.en.md").read_text(encoding="utf-8")
    assert "**2 / 2（100.00%）**" in markdown
    assert "| `passed` | 1 / 2 | 50.00% |" in markdown
    assert "# AKBridge Acceptance Summary" in english_markdown
    assert "## Documentation Index Coverage" in english_markdown
    status_svg = (tmp_path / "out" / "status.svg").read_text(encoding="utf-8")
    assert "MCP 适配通过率 100.00%" in status_svg
    assert "数据源可用性 50.00%" in status_svg
    assert "成功返回 1（50.00%）" in status_svg
    assert 'class="availability-line"' in status_svg
    assert status_svg.count('class="availability-line"') == 64
    assert status_svg.count('class="mcp-line"') == 64
    assert '.background { fill: #ffffff; }' in status_svg
    assert '.background { fill: #0d1117; }' in status_svg
    assert 'class="background" width="860"' in status_svg
    with (tmp_path / "out" / "ledger.csv").open(encoding="utf-8-sig", newline="") as handle:
        assert len(list(csv.DictReader(handle))) == 2
