from __future__ import annotations

import json
from pathlib import Path

import pytest

from akbridge.acceptance import run_acceptance
from akbridge.catalog import ApiFunction


def _api(name: str, required: list[str] | None = None) -> ApiFunction:
    return ApiFunction(
        name=name,
        function=lambda: None,
        description=name,
        input_schema={"type": "object", "properties": {}, "required": required or []},
        signature="()",
    )


def test_run_acceptance_records_missing_fixture(tmp_path: Path) -> None:
    output = tmp_path / "report.json"

    report = run_acceptance({"sample": _api("sample", ["symbol"])}, output, {})

    assert report["results"][0]["status"] == "fixture_required"
    assert json.loads(output.read_text(encoding="utf-8"))["results"] == report["results"]


def test_run_acceptance_rejects_unknown_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unknown AKShare APIs"):
        run_acceptance({}, tmp_path / "report.json", {}, names=["missing"])


def test_run_acceptance_supports_concurrent_workers(tmp_path: Path) -> None:
    catalog = {
        "first": _api("first", ["symbol"]),
        "second": _api("second", ["symbol"]),
    }

    report = run_acceptance(catalog, tmp_path / "report.json", {}, workers=2)

    assert {item["name"] for item in report["results"]} == {"first", "second"}
    assert {item["status"] for item in report["results"]} == {"fixture_required"}
