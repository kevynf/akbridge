from __future__ import annotations

import json
from pathlib import Path

from akbridge.catalog import ApiFunction, signature_to_schema
from akbridge.maintenance import (
    check_latest_akshare,
    diff_manifests,
    has_regression,
    manifest_fingerprint,
    run_maintenance,
    validate_catalog,
    validate_mcp_contract,
)


def _catalog() -> dict[str, ApiFunction]:
    def sample(value: int = 1) -> int:
        return value

    return {
        "sample": ApiFunction(
            name="sample",
            function=sample,
            description="sample",
            input_schema=signature_to_schema(sample),
            signature="(value: int = 1) -> int",
            category="calculation",
        )
    }


def test_manifest_diff_and_fingerprint_are_stable() -> None:
    from akbridge.acceptance import build_manifest

    current = build_manifest(_catalog())
    previous = dict(current)
    previous["generated_at"] = "different"
    diff = diff_manifests(previous, current)

    assert diff["added"] == []
    assert diff["removed"] == []
    assert diff["changed"] == []
    assert manifest_fingerprint(previous) == manifest_fingerprint(current)
    assert not has_regression(diff)


def test_version_check_is_machine_readable_and_injectable() -> None:
    payload = check_latest_akshare(
        installed="1.0.0",
        fetcher=lambda _: b'{"info": {"version": "2.0.0"}}',
    )

    assert payload == {
        "status": "ok",
        "installed": "1.0.0",
        "latest": "2.0.0",
        "update_available": True,
        "error": None,
    }


def test_maintenance_writes_machine_reports_without_provider_calls(tmp_path: Path) -> None:
    report_path = tmp_path / "maintenance.json"
    manifest_path = tmp_path / "manifest.json"
    catalog_path = tmp_path / "catalog.json"

    report = run_maintenance(
        catalog=_catalog(),
        current_path=manifest_path,
        catalog_path=catalog_path,
        report_path=report_path,
        strict=True,
    )

    assert report["ok"]
    assert report["exit_code"] == 0
    assert report["mcp_validation"]["all_tool_count"] == 1
    assert report["mcp_validation"]["router_tool_count"] == 3
    assert manifest_path.exists() and catalog_path.exists() and report_path.exists()
    assert json.loads(report_path.read_text(encoding="utf-8"))["exit_code"] == 0
    assert validate_catalog(_catalog())["ok"]
    assert validate_mcp_contract(_catalog())["ok"]


def test_fail_on_update_turns_version_signal_into_gate(monkeypatch) -> None:
    import akbridge.maintenance as maintenance

    monkeypatch.setattr(
        maintenance,
        "check_latest_akshare",
        lambda: {
            "status": "ok",
            "installed": "1.0.0",
            "latest": "2.0.0",
            "update_available": True,
            "error": None,
        },
    )

    report = maintenance.run_maintenance(
        catalog=_catalog(), strict=True, check_latest=True, fail_on_update=True
    )

    assert report["exit_code"] == 1
    assert any(alert["code"] == "akshare_update_available" for alert in report["alerts"])
