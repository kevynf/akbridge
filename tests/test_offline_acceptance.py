from __future__ import annotations

import sys
from pathlib import Path

import pytest

import akbridge.acceptance as acceptance
from akbridge.acceptance import run_acceptance
from akbridge.catalog import ApiFunction, signature_to_schema


def test_adapter_only_acceptance_never_invokes_provider(tmp_path: Path) -> None:
    called = {"value": False}

    def provider(required: str) -> str:
        called["value"] = True
        raise AssertionError("provider must not run in adapter-only mode")

    api = ApiFunction(
        name="sample",
        function=provider,
        description="sample",
        input_schema=signature_to_schema(provider),
        signature="(required: str) -> str",
    )

    report = run_acceptance(
        {"sample": api},
        tmp_path / "run.json",
        {},
        adapter_only=True,
    )

    assert report["results"][0]["status"] == "adapter_passed"
    assert not called["value"]


def test_adapter_only_acceptance_reports_invalid_mcp_contract(tmp_path: Path) -> None:
    def provider() -> str:
        return "unused"

    api = ApiFunction(
        name="sample",
        function=provider,
        description="sample",
        input_schema={"type": "array"},
        signature="() -> str",
    )

    report = run_acceptance(
        {"sample": api},
        tmp_path / "run.json",
        {},
        adapter_only=True,
    )

    assert report["results"][0]["status"] == "adapter_failed"
    assert "root must be an object" in report["results"][0]["error"]


def test_offline_cli_fails_for_an_invalid_adapter_contract(tmp_path: Path, monkeypatch) -> None:
    def provider() -> str:
        return "unused"

    api = ApiFunction(
        name="sample",
        function=provider,
        description="sample",
        input_schema={"type": "array"},
        signature="() -> str",
    )
    monkeypatch.setattr(acceptance, "discover_functions", lambda: {"sample": api})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "akbridge-accept",
            "run",
            "--offline",
            "--output",
            str(tmp_path / "run.json"),
        ],
    )

    with pytest.raises(SystemExit) as error:
        acceptance.main()

    assert error.value.code == 1
