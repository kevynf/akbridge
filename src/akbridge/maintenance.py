"""Unattended AKBridge catalog and regression maintenance.

The default ``ci`` command is offline: it imports AKShare, rebuilds the
manifest, compares it with a baseline, and validates the MCP/router contract.
Provider calls are an explicit opt-in because third-party availability is not
an appropriate CI gate for schema compatibility.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import akshare

from .acceptance import build_manifest, run_acceptance, write_acceptance_artifacts, write_manifest
from .catalog import ApiFunction, discover_functions
from .documents import documentation_coverage, load_builtin_document_chunks
from .router import CatalogIndex


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def manifest_fingerprint(manifest: dict[str, Any]) -> str:
    """Hash a manifest while ignoring generation time and ordering noise."""
    stable = {
        "akshare_version": manifest.get("akshare_version"),
        "interfaces": sorted(
            (
                {
                    key: item.get(key)
                    for key in (
                        "name",
                        "signature",
                        "schema_hash",
                        "metadata_hash",
                        "category",
                        "source_module",
                        "side_effect",
                    )
                }
                for item in manifest.get("interfaces", [])
            ),
            key=lambda item: item.get("name", ""),
        ),
    }
    return hashlib.sha256(
        json.dumps(stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()


def _interface_map(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(item.get("name")): item
        for item in manifest.get("interfaces", [])
        if isinstance(item, dict) and item.get("name")
    }


def diff_manifests(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic added/removed/changed interface information."""
    if not previous:
        return {
            "baseline_present": False,
            "added": sorted(_interface_map(current)),
            "removed": [],
            "changed": [],
            "version_changed": False,
            "previous_fingerprint": None,
            "current_fingerprint": manifest_fingerprint(current),
        }
    old = _interface_map(previous)
    new = _interface_map(current)
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed: list[dict[str, Any]] = []
    fields = (
        "signature",
        "schema_hash",
        "metadata_hash",
        "category",
        "source_module",
        "side_effect",
    )
    for name in sorted(set(old) & set(new)):
        differences = {
            field: {"previous": old[name].get(field), "current": new[name].get(field)}
            for field in fields
            if old[name].get(field) != new[name].get(field)
        }
        if differences:
            changed.append({"name": name, "fields": differences})
    return {
        "baseline_present": True,
        "added": added,
        "removed": removed,
        "changed": changed,
        "version_changed": previous.get("akshare_version") != current.get("akshare_version"),
        "previous_fingerprint": manifest_fingerprint(previous),
        "current_fingerprint": manifest_fingerprint(current),
    }


def compare_manifests(previous: dict[str, Any] | None, current: dict[str, Any]) -> dict[str, Any]:
    """Backward-friendly alias for callers that prefer ``compare`` wording."""
    return diff_manifests(previous, current)


def has_regression(diff: dict[str, Any], *, max_removed: int = 0) -> bool:
    """Return whether a diff should fail a strict automated gate."""
    schema_changes = [
        item
        for item in diff.get("changed", [])
        if any(field in item.get("fields", {}) for field in ("signature", "schema_hash"))
    ]
    return len(diff.get("removed", [])) > max_removed or bool(schema_changes)


def validate_catalog(catalog: dict[str, ApiFunction]) -> dict[str, Any]:
    """Validate all local adapter contracts without invoking providers."""
    errors: list[dict[str, str]] = []
    names = list(catalog)
    if len(names) != len(set(names)):
        errors.append({"code": "duplicate_name", "detail": "catalog contains duplicate names"})
    for name, api in catalog.items():
        schema = api.input_schema
        if schema.get("type") != "object":
            errors.append(
                {"code": "invalid_schema", "name": name, "detail": "root schema is not object"}
            )
        if not api.description:
            errors.append(
                {"code": "missing_description", "name": name, "detail": "empty description"}
            )
        if not api.category:
            errors.append({"code": "missing_category", "name": name, "detail": "empty category"})
        required = set(schema.get("required", []))
        properties = set(schema.get("properties", {}))
        if not required <= properties:
            errors.append(
                {
                    "code": "invalid_required",
                    "name": name,
                    "detail": "required parameter absent from properties",
                }
            )
    return {
        "interface_count": len(catalog),
        "error_count": len(errors),
        "errors": errors[:200],
        "ok": not errors,
    }


def validate_mcp_contract(catalog: dict[str, ApiFunction]) -> dict[str, Any]:
    """Construct both MCP surfaces and report schema/model validation errors."""
    try:
        from .server import build_mcp_tools

        all_tools = build_mcp_tools(catalog, mode="all")
        router_tools = build_mcp_tools(catalog, mode="router")
        names = [tool.name for tool in all_tools]
        errors: list[str] = []
        if len(names) != len(set(names)):
            errors.append("duplicate tool names in all mode")
        if len(router_tools) != 3:
            errors.append(f"router mode exposes {len(router_tools)} tools, expected 3")
    except Exception as exc:  # Pydantic/MCP schema errors are contract failures.
        all_tools = []
        router_tools = []
        errors = [f"{type(exc).__name__}: {exc}"]
    return {
        "all_tool_count": len(all_tools),
        "router_tool_count": len(router_tools),
        "error_count": len(errors),
        "errors": errors,
        "ok": not errors,
    }


def semantic_catalog(catalog: dict[str, ApiFunction]) -> dict[str, Any]:
    """Build a compact JSON catalog intended for local RAG/indexing."""
    index = CatalogIndex(catalog)
    return {
        "schema_version": 2,
        "akshare_version": getattr(akshare, "__version__", "unknown"),
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "interface_count": len(catalog),
        "interfaces": [api.as_metadata(include_schema=False) for api in catalog.values()],
        "routes": index.route_table(),
    }


def build_semantic_catalog(catalog: dict[str, ApiFunction]) -> dict[str, Any]:
    """Public naming alias for integrations that call this a catalog builder."""
    return semantic_catalog(catalog)


def check_latest_akshare(
    *,
    installed: str | None = None,
    fetcher: Callable[[str], bytes] | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Check PyPI for a newer AKShare release without requiring a package tool."""
    installed = installed or getattr(akshare, "__version__", "unknown")
    if fetcher is None:

        def fetcher(url: str) -> bytes:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                return response.read()

    try:
        payload = json.loads(fetcher("https://pypi.org/pypi/akshare/json"))
        latest = str(payload["info"]["version"])
    except Exception as exc:  # Network availability is not an adapter failure.
        return {
            "status": "unavailable",
            "installed": installed,
            "latest": None,
            "update_available": None,
            "error": f"{type(exc).__name__}: {exc}"[:500],
        }
    return {
        "status": "ok",
        "installed": installed,
        "latest": latest,
        "update_available": latest != installed,
        "error": None,
    }


def run_maintenance(
    *,
    baseline_path: Path | None = None,
    current_path: Path | None = None,
    report_path: Path | None = None,
    catalog_path: Path | None = None,
    strict: bool = False,
    max_removed: int = 0,
    min_interfaces: int = 1,
    check_latest: bool = False,
    fail_on_update: bool = False,
    catalog: dict[str, ApiFunction] | None = None,
) -> dict[str, Any]:
    """Run all offline checks and optionally persist machine-readable reports."""
    catalog = discover_functions() if catalog is None else catalog
    current = build_manifest(catalog)
    previous = _read_json(baseline_path) if baseline_path else None
    diff = diff_manifests(previous, current)
    validation = validate_catalog(catalog)
    mcp_validation = validate_mcp_contract(catalog)
    try:
        documentation = documentation_coverage(
            catalog,
            load_builtin_document_chunks(
                expected_version=str(getattr(akshare, "__version__", "unknown"))
            ),
        )
    except ValueError as exc:
        documentation = {
            "status": "invalid",
            "error": str(exc),
            "chunk_count": 0,
            "interface_count": len(catalog),
        }
    # Constructing the index is itself a useful RAG contract check.
    index = CatalogIndex(catalog)
    categories: dict[str, int] = {}
    for api in catalog.values():
        categories[api.category] = categories.get(api.category, 0) + 1
    alerts: list[dict[str, Any]] = []
    if len(catalog) < min_interfaces:
        alerts.append(
            {
                "code": "interface_count_below_minimum",
                "actual": len(catalog),
                "minimum": min_interfaces,
            }
        )
    if len(diff["removed"]) > max_removed:
        alerts.append(
            {"code": "interfaces_removed", "count": len(diff["removed"]), "allowed": max_removed}
        )
    schema_changes = [
        item
        for item in diff["changed"]
        if any(field in item.get("fields", {}) for field in ("signature", "schema_hash"))
    ]
    if schema_changes:
        alerts.append({"code": "schema_regression", "count": len(schema_changes)})
    if not validation["ok"]:
        alerts.append({"code": "catalog_validation_failed", "count": validation["error_count"]})
    if not mcp_validation["ok"]:
        alerts.append({"code": "mcp_contract_failed", "count": mcp_validation["error_count"]})
    if not index.search("", limit=1):
        alerts.append({"code": "empty_router_index"})
    version_check = check_latest_akshare() if check_latest else None
    if fail_on_update and version_check and version_check.get("update_available"):
        alerts.append(
            {
                "code": "akshare_update_available",
                "installed": version_check.get("installed"),
                "latest": version_check.get("latest"),
            }
        )
    report = {
        "ok": not alerts,
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "akshare_version": current.get("akshare_version"),
        "current_fingerprint": manifest_fingerprint(current),
        "validation": validation,
        "mcp_validation": mcp_validation,
        "documentation": documentation,
        "diff": diff,
        "categories": dict(sorted(categories.items())),
        "version_check": version_check,
        "alerts": alerts,
        "strict": strict,
    }
    # In non-strict mode the report remains informative; callers can still
    # inspect ``ok`` while a first run without a baseline is allowed.
    report["exit_code"] = 1 if strict and alerts else 0
    if current_path:
        _write_json(current_path, current)
    if catalog_path:
        _write_json(catalog_path, semantic_catalog(catalog))
    if report_path:
        _write_json(report_path, report)
    return report


def _load_fixtures(path: Path) -> dict[str, dict[str, Any]]:
    payload = _read_json(path)
    return payload if isinstance(payload, dict) else {}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run automated AKBridge validation and maintenance checks"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    manifest_parser = sub.add_parser("manifest", help="write the full acceptance manifest")
    manifest_parser.add_argument(
        "--output", type=Path, default=Path("artifacts/acceptance/manifest.json")
    )

    catalog_parser = sub.add_parser("catalog", help="write the compact semantic/RAG catalog")
    catalog_parser.add_argument("--output", type=Path, default=Path("artifacts/catalog.json"))

    version_parser = sub.add_parser(
        "version", help="check the installed AKShare version against PyPI"
    )
    version_parser.add_argument("--timeout", type=float, default=5.0)

    verify_parser = sub.add_parser("verify", help="run offline adapter and router checks")
    verify_parser.add_argument("--strict", action="store_true")
    verify_parser.add_argument("--check-latest", action="store_true")
    verify_parser.add_argument("--fail-on-update", action="store_true")
    verify_parser.add_argument(
        "--report", type=Path, default=Path("artifacts/maintenance/latest.json")
    )

    diff_parser = sub.add_parser("diff", help="compare a baseline manifest with a new one")
    diff_parser.add_argument("--baseline", type=Path, required=True)
    diff_parser.add_argument("--current", type=Path)
    diff_parser.add_argument("--strict", action="store_true")
    diff_parser.add_argument("--max-removed", type=int, default=0)

    ci_parser = sub.add_parser(
        "ci", help="run the complete automated validation and maintenance gate"
    )
    ci_parser.add_argument(
        "--baseline", type=Path, default=Path("artifacts/acceptance/manifest.json")
    )
    ci_parser.add_argument(
        "--current", type=Path, default=Path("artifacts/maintenance/manifest.json")
    )
    ci_parser.add_argument("--catalog", type=Path, default=Path("artifacts/catalog.json"))
    ci_parser.add_argument("--report", type=Path, default=Path("artifacts/maintenance/latest.json"))
    ci_parser.add_argument("--strict", action="store_true")
    ci_parser.add_argument("--max-removed", type=int, default=0)
    ci_parser.add_argument("--check-latest", action="store_true")
    ci_parser.add_argument("--fail-on-update", action="store_true")
    ci_parser.add_argument(
        "--provider", action="store_true", help="also run network-backed interface acceptance"
    )
    ci_parser.add_argument("--timeout", type=float, default=30)
    ci_parser.add_argument("--workers", type=int, default=4)

    args = parser.parse_args()
    if args.command == "manifest":
        result = write_manifest(args.output)
        print(
            json.dumps(
                {"interface_count": result["interface_count"], "output": str(args.output)},
                ensure_ascii=False,
            )
        )
        return
    if args.command == "catalog":
        catalog = discover_functions()
        _write_json(args.output, semantic_catalog(catalog))
        print(
            json.dumps(
                {"interface_count": len(catalog), "output": str(args.output)}, ensure_ascii=False
            )
        )
        return
    if args.command == "version":
        print(
            json.dumps(
                check_latest_akshare(timeout=args.timeout), ensure_ascii=False, sort_keys=True
            )
        )
        return
    if args.command == "verify":
        report = run_maintenance(
            report_path=args.report,
            strict=args.strict,
            check_latest=args.check_latest,
            fail_on_update=args.fail_on_update,
        )
    elif args.command == "diff":
        current = _read_json(args.current) if args.current else build_manifest(discover_functions())
        previous = _read_json(args.baseline)
        report = diff_manifests(previous, current)
        report["ok"] = not has_regression(report, max_removed=args.max_removed)
        report["exit_code"] = 1 if args.strict and not report["ok"] else 0
    else:
        report = run_maintenance(
            baseline_path=args.baseline,
            current_path=args.current,
            report_path=args.report,
            catalog_path=args.catalog,
            strict=args.strict,
            max_removed=args.max_removed,
            check_latest=args.check_latest,
            fail_on_update=args.fail_on_update,
        )
        if args.provider:
            run_path = args.report.with_name("provider-run.json")
            provider_report = run_acceptance(
                discover_functions(),
                run_path,
                _load_fixtures(Path("artifacts/acceptance/fixtures.json")),
                timeout=args.timeout,
                workers=args.workers,
                resume=True,
            )
            report["provider"] = write_acceptance_artifacts(run_path, args.report.parent)
            report["provider_status_counts"] = {
                status: sum(
                    1 for item in provider_report["results"] if item.get("status") == status
                )
                for status in sorted({item.get("status") for item in provider_report["results"]})
            }
            if args.strict and any(
                item.get("status") == "worker_failed" for item in provider_report["results"]
            ):
                report["exit_code"] = 1
            _write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    if report.get("exit_code"):
        raise SystemExit(int(report["exit_code"]))


if __name__ == "__main__":
    main()
