"""Generate and execute the per-interface AKShare acceptance ledger."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import akshare

from .catalog import ApiFunction, discover_functions

UPSTREAM_TRANSPORT_ERRORS = {
    "ConnectionError",
    "HTTPError",
    "SSLError",
    "TimeoutError",
}
UPSTREAM_RESPONSE_ERRORS = {
    "APIError",
    "ArrowInvalid",
    "BadZipFile",
    "JSONDecodeError",
}

SVG_NAMESPACE = "http://www.w3.org/2000/svg"
STATUS_ORDER = (
    "passed",
    "passed_empty",
    "adapter_passed",
    "failed",
    "timeout",
    "fixture_required",
    "worker_failed",
    "adapter_failed",
)
STATUS_LABELS = {
    "passed": "成功返回",
    "passed_empty": "正常空结果",
    "adapter_passed": "适配通过",
    "failed": "运行失败",
    "timeout": "超时",
    "fixture_required": "缺少验收参数",
    "worker_failed": "隔离进程失败",
    "adapter_failed": "适配失败",
}
STATUS_COLORS = {
    "passed": "#2da44e",
    "passed_empty": "#0969da",
    "adapter_passed": "#2da44e",
    "failed": "#cf222e",
    "timeout": "#bf8700",
    "fixture_required": "#8250df",
    "worker_failed": "#cf222e",
    "adapter_failed": "#a40e26",
}


def build_manifest(catalog: dict[str, ApiFunction]) -> dict[str, Any]:
    interfaces = []
    for api in catalog.values():
        required = api.input_schema.get("required", [])
        schema_hash = hashlib.sha256(
            json.dumps(
                api.input_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        metadata = api.as_metadata(include_schema=False)
        interfaces.append(
            {
                "name": api.name,
                "signature": api.signature,
                "display_name": api.display_name or api.name,
                "category": api.category,
                "aliases": list(api.aliases),
                "use_cases": list(api.use_cases),
                "examples": list(api.examples),
                "return": api.return_metadata,
                "parameters": api.parameter_metadata,
                "side_effect": api.side_effect,
                "source_url": api.source_url,
                "source_module": api.source_module,
                "description": api.description,
                "schema_hash": schema_hash,
                "metadata_hash": hashlib.sha256(
                    json.dumps(
                        metadata,
                        ensure_ascii=False,
                        sort_keys=True,
                        default=str,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "required_parameters": required,
                "schema_status": "passed",
                "call_status": "pending" if not required else "fixture_required",
            }
        )
    return {
        "akshare_version": getattr(akshare, "__version__", "unknown"),
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "interface_count": len(interfaces),
        "interfaces": interfaces,
    }


def write_manifest(path: Path) -> dict[str, Any]:
    manifest = build_manifest(discover_functions())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _adapter_contract_result(api: ApiFunction) -> dict[str, Any]:
    """Validate one generated MCP adapter without invoking its provider."""
    try:
        schema = api.input_schema
        if schema.get("type") != "object":
            raise ValueError("input schema root must be an object")
        properties = schema.get("properties", {})
        if not isinstance(properties, dict):
            raise ValueError("input schema properties must be an object")
        required = schema.get("required", [])
        if not isinstance(required, list) or not set(required) <= set(properties):
            raise ValueError("required parameters must be declared in properties")
        if not api.description or not api.category:
            raise ValueError("adapter metadata is incomplete")

        # Import lazily so acceptance helpers remain usable during maintenance
        # module startup, while still exercising the exact MCP model boundary.
        from .server import build_mcp_tools

        tools = build_mcp_tools({api.name: api}, mode="all")
        if len(tools) != 1 or tools[0].name != api.name:
            raise ValueError("MCP tool construction returned an unexpected tool")
    except Exception as exc:  # Contract failures must be machine-readable.
        return {
            "name": api.name,
            "status": "adapter_failed",
            "duration_seconds": 0.0,
            "arguments": {},
            "error": f"{type(exc).__name__}: {exc}",
            "worker_logs": None,
            "preview": None,
        }
    return {
        "name": api.name,
        "status": "adapter_passed",
        "duration_seconds": 0.0,
        "arguments": {},
        "error": None,
        "worker_logs": None,
        "preview": None,
    }


def classify_result(result: dict[str, Any]) -> tuple[str, str | None]:
    """Return the failure scope and exception type for a call result."""
    status = result.get("status")
    if status in {"passed", "passed_empty"}:
        return "provider_success", None
    if status == "adapter_passed":
        return "adapter_contract", None
    if status == "adapter_failed":
        return "mcp_adapter", "AdapterContractError"
    if status == "timeout":
        return "upstream_timeout", "Timeout"
    if status == "fixture_required":
        return "fixture", None
    if status == "worker_failed":
        return "mcp_adapter", None
    error_type = (result.get("error") or "UnknownError").split(":", 1)[0]
    if error_type in UPSTREAM_TRANSPORT_ERRORS:
        return "upstream_transport", error_type
    if error_type in UPSTREAM_RESPONSE_ERRORS:
        return "upstream_response", error_type
    return "akshare_runtime", error_type


def _write_status_svg(summary: dict[str, Any], output_path: Path) -> None:
    """Render a theme-aware availability-stripe chart for repository READMEs."""
    ET.register_namespace("", SVG_NAMESPACE)
    status_counts = summary["status_counts"]
    interface_count = summary["interface_count"]
    ordered_statuses = [key for key in STATUS_ORDER if key in status_counts]
    ordered_statuses.extend(sorted(set(status_counts) - set(ordered_statuses)))
    scope_counts = summary["scope_counts"]
    provider_report = any(
        key in scope_counts
        for key in (
            "provider_success",
            "upstream_transport",
            "upstream_response",
            "upstream_timeout",
            "akshare_runtime",
        )
    )
    documentation = summary.get("documentation", {})
    documentation_available = documentation.get("status") == "ok"
    legend_rows = max(1, (len(ordered_statuses) + 3) // 4)
    documentation_y = 162 if provider_report else 94
    legend_y = documentation_y + 70 if documentation_available else (164 if provider_report else 96)
    height = legend_y + 26 + (legend_rows - 1) * 34

    root = ET.Element(
        f"{{{SVG_NAMESPACE}}}svg",
        {
            "width": "860",
            "height": str(height),
            "viewBox": f"0 0 860 {height}",
            "role": "img",
            "aria-labelledby": "title description",
        },
    )
    ET.SubElement(root, f"{{{SVG_NAMESPACE}}}title", {"id": "title"}).text = "AKBridge 最新验收状态"
    ET.SubElement(
        root, f"{{{SVG_NAMESPACE}}}desc", {"id": "description"}
    ).text = "使用短竖线表示 MCP 适配通过率和数据源动态验收组分。"
    style = ET.SubElement(root, f"{{{SVG_NAMESPACE}}}style")
    style.text = """
      .background { fill: #ffffff; }
      .primary { fill: #1f2328; }
      .muted { fill: #656d76; }
      text { font-family: "Segoe UI", "Microsoft YaHei", sans-serif; }
      @media (prefers-color-scheme: dark) {
        .background { fill: #0d1117; }
        .primary { fill: #f0f6fc; }
        .muted { fill: #8b949e; }
      }
    """
    ET.SubElement(
        root,
        f"{{{SVG_NAMESPACE}}}rect",
        {
            "class": "background",
            "width": "860",
            "height": str(height),
            "rx": "6",
        },
    )

    def add_text(
        value: str,
        x: int,
        y: int,
        *,
        css_class: str = "primary",
        size: int = 13,
        weight: int = 400,
        anchor: str | None = None,
    ) -> None:
        attributes = {
            "class": css_class,
            "x": str(x),
            "y": str(y),
            "font-size": str(size),
            "font-weight": str(weight),
        }
        if anchor:
            attributes["text-anchor"] = anchor
        ET.SubElement(root, f"{{{SVG_NAMESPACE}}}text", attributes).text = value

    def ratio(count: int) -> float:
        return count / interface_count if interface_count else 0.0

    stripe_count = 64

    adapter_accepted = summary["mcp_adapter_accepted"]
    add_text(
        f"MCP 适配通过率 {ratio(adapter_accepted):.2%}",
        16,
        26,
        size=22,
        weight=600,
    )
    add_text(
        f"{adapter_accepted} / {interface_count}",
        844,
        26,
        css_class="muted",
        size=18,
        anchor="end",
    )
    for index in range(stripe_count):
        target = ((index + 0.5) / stripe_count) * interface_count
        color = "#2da44e" if target <= adapter_accepted else "#cf222e"
        x = 20 + index * (820 / (stripe_count - 1))
        ET.SubElement(
            root,
            f"{{{SVG_NAMESPACE}}}line",
            {
                "class": "mcp-line",
                "x1": f"{x:.2f}",
                "y1": "36",
                "x2": f"{x:.2f}",
                "y2": "58",
                "stroke": color,
                "stroke-width": "4",
                "stroke-linecap": "round",
            },
        )

    availability_label = "数据源可用性" if provider_report else "离线契约通过率"
    available_count = (
        scope_counts.get("provider_success", 0)
        if provider_report
        else scope_counts.get("adapter_contract", summary["mcp_adapter_accepted"])
    )
    if provider_report:
        add_text(
            f"{availability_label} {ratio(available_count):.2%}",
            16,
            94,
            size=22,
            weight=600,
        )
        add_text(
            f"{available_count} / {interface_count}",
            844,
            94,
            css_class="muted",
            size=18,
            anchor="end",
        )

    cumulative: list[tuple[int, str]] = []
    running_total = 0
    for status in ordered_statuses:
        running_total += status_counts[status]
        cumulative.append((running_total, status))
    if provider_report:
        for index in range(stripe_count):
            target = ((index + 0.5) / stripe_count) * interface_count
            status = next(
                (key for boundary, key in cumulative if target <= boundary),
                ordered_statuses[-1] if ordered_statuses else "unknown",
            )
            x = 20 + index * (820 / (stripe_count - 1))
            ET.SubElement(
                root,
                f"{{{SVG_NAMESPACE}}}line",
                {
                    "class": "availability-line",
                    "x1": f"{x:.2f}",
                    "y1": "108",
                    "x2": f"{x:.2f}",
                    "y2": "130",
                    "stroke": STATUS_COLORS.get(status, "#6e7781"),
                    "stroke-width": "4",
                    "stroke-linecap": "round",
                },
            )

    if documentation_available:
        documented_count = int(documentation["documented_interface_count"])
        add_text(
            f"文档接口覆盖率 {ratio(documented_count):.2%}",
            16,
            documentation_y,
            size=22,
            weight=600,
        )
        add_text(
            f"{documented_count} / {interface_count}",
            844,
            documentation_y,
            css_class="muted",
            size=18,
            anchor="end",
        )
        for index in range(stripe_count):
            target = ((index + 0.5) / stripe_count) * interface_count
            color = "#2da44e" if target <= documented_count else "#bf8700"
            x = 20 + index * (820 / (stripe_count - 1))
            ET.SubElement(
                root,
                f"{{{SVG_NAMESPACE}}}line",
                {
                    "class": "documentation-line",
                    "x1": f"{x:.2f}",
                    "y1": str(documentation_y + 10),
                    "x2": f"{x:.2f}",
                    "y2": str(documentation_y + 32),
                    "stroke": color,
                    "stroke-width": "4",
                    "stroke-linecap": "round",
                },
            )

    for index, status in enumerate(ordered_statuses):
        column = index % 4
        row = index // 4
        x = 17 + column * 207
        y = legend_y + row * 34
        ET.SubElement(
            root,
            f"{{{SVG_NAMESPACE}}}line",
            {
                "class": "legend-line",
                "x1": str(x),
                "y1": str(y - 17),
                "x2": str(x),
                "y2": str(y + 2),
                "stroke": STATUS_COLORS.get(status, "#6e7781"),
                "stroke-width": "5",
                "stroke-linecap": "round",
            },
        )
        count = status_counts[status]
        label = STATUS_LABELS.get(status, status)
        add_text(f"{label} {count}（{ratio(count):.2%}）", x + 14, y, size=17)

    tree = ET.ElementTree(root)
    ET.indent(tree, space="  ")
    output_path.write_text(ET.tostring(root, encoding="unicode") + "\n", encoding="utf-8")


def write_acceptance_artifacts(run_path: Path, output_dir: Path) -> dict[str, Any]:
    """Write a compact per-interface ledger and aggregate Markdown summary."""
    report = _load_json(run_path, {})
    results = report.get("results", [])
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "ledger.csv"
    with ledger_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "name",
                "status",
                "scope",
                "error_type",
                "duration_seconds",
                "error",
            ],
        )
        writer.writeheader()
        for result in sorted(results, key=lambda item: item["name"]):
            scope, error_type = classify_result(result)
            writer.writerow(
                {
                    "name": result["name"],
                    "status": result["status"],
                    "scope": scope,
                    "error_type": error_type or "",
                    "duration_seconds": result.get("duration_seconds", ""),
                    "error": (result.get("error") or "")[:4000]
                    .replace("\r", " ")
                    .replace("\n", " "),
                }
            )

    status_counts: dict[str, int] = {}
    scope_counts: dict[str, int] = {}
    for result in results:
        status_counts[result["status"]] = status_counts.get(result["status"], 0) + 1
        scope, _ = classify_result(result)
        scope_counts[scope] = scope_counts.get(scope, 0) + 1
    adapter_accepted = sum(
        count
        for status, count in status_counts.items()
        if status not in {"fixture_required", "worker_failed", "adapter_failed"}
    )
    interface_count = len(results)

    def ratio(count: int) -> float:
        return count / interface_count if interface_count else 0.0

    def percentage(count: int) -> str:
        return f"{ratio(count):.2%}"

    summary = {
        "akshare_version": report.get("akshare_version", "unknown"),
        "generated_at": report.get("generated_at"),
        "interface_count": interface_count,
        "mcp_adapter_accepted": adapter_accepted,
        "mcp_adapter_acceptance_rate": ratio(adapter_accepted),
        "status_counts": status_counts,
        "status_rates": {key: ratio(value) for key, value in status_counts.items()},
        "scope_counts": scope_counts,
        "scope_rates": {key: ratio(value) for key, value in scope_counts.items()},
    }
    try:
        from .catalog import discover_functions
        from .documents import documentation_coverage, load_builtin_document_chunks

        catalog = discover_functions()
        summary["documentation"] = documentation_coverage(
            catalog,
            load_builtin_document_chunks(expected_version=str(summary["akshare_version"])),
        )
    except ValueError as exc:
        summary["documentation"] = {"status": "invalid", "error": str(exc)}
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "# AKBridge 验收汇总",
        "",
        f"- AKShare 版本：`{summary['akshare_version']}`",
        f"- 生成时间：`{summary['generated_at']}`",
        f"- 已发现接口：**{summary['interface_count']}**",
        (
            f"- MCP 适配通过：**{adapter_accepted} / {interface_count}"
            f"（{percentage(adapter_accepted)}）**"
        ),
        "",
        "`MCP 适配通过`表示接口已被发现、已生成输入 Schema，并且没有因缺少验收参数",
        "或隔离进程故障而中断。数据源成功情况单独统计，上游故障和 AKShare 运行错误不会被隐藏。",
        "",
        "## 调用状态",
        "",
        "| 状态 | 数量 | 占比 |",
        "| --- | ---: | ---: |",
        *[
            f"| `{key}` | {value} / {interface_count} | {percentage(value)} |"
            for key, value in sorted(status_counts.items())
        ],
        "",
        "## 结果范围",
        "",
        "| 范围 | 数量 | 占比 |",
        "| --- | ---: | ---: |",
        *[
            f"| `{key}` | {value} / {interface_count} | {percentage(value)} |"
            for key, value in sorted(scope_counts.items())
        ],
        "",
        "每个接口的精确结果见 `ledger.csv`。",
    ]
    documentation = summary["documentation"]
    lines.extend(
        [
            "",
            "## 文档索引覆盖",
            "",
            f"- 状态：`{documentation['status']}`",
            f"- 文档块：**{documentation.get('chunk_count', 0)}**",
            (
                "- 公开接口关联：**"
                f"{documentation.get('documented_interface_count', 0)} / "
                f"{documentation.get('interface_count', 0)}"
                f"（{documentation.get('interface_coverage_percent', 0):.2f}%）**"
            ),
            (
                "- 文档字段完整：**"
                f"{documentation.get('complete_chunk_count', 0)} / "
                f"{documentation.get('chunk_count', 0)}"
                f"（{documentation.get('chunk_field_coverage_percent', 0):.2f}%）**"
            ),
        ]
    )
    (output_dir / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    english_lines = [
        "# AKBridge Acceptance Summary",
        "",
        f"- AKShare version: `{summary['akshare_version']}`",
        f"- Generated at: `{summary['generated_at']}`",
        f"- Discovered interfaces: **{summary['interface_count']}**",
        (
            f"- MCP adapter acceptance: **{adapter_accepted} / {interface_count}"
            f" ({percentage(adapter_accepted)})**"
        ),
        "",
        "`MCP adapter acceptance` means that interfaces were discovered, input schemas were",
        "generated, and MCP tool construction completed without an adapter failure. Provider",
        "availability is reported separately.",
        "",
        "## Call Status",
        "",
        "| Status | Count | Rate |",
        "| --- | ---: | ---: |",
    ]
    for status in sorted(status_counts):
        english_lines.append(
            f"| `{status}` | {status_counts[status]} / {interface_count} | "
            f"{percentage(status_counts[status])} |"
        )
    english_lines.extend(
        [
            "",
            "## Result Scope",
            "",
            "| Scope | Count | Rate |",
            "| --- | ---: | ---: |",
        ]
    )
    for scope in sorted(scope_counts):
        english_lines.append(
            f"| `{scope}` | {scope_counts[scope]} / {interface_count} | "
            f"{percentage(scope_counts[scope])} |"
        )
    english_lines.extend(
        [
            "",
            "## Documentation Index Coverage",
            "",
            f"- Status: `{documentation['status']}`",
            f"- Document chunks: **{documentation.get('chunk_count', 0)}**",
            (
                "- Public interfaces linked: **"
                f"{documentation.get('documented_interface_count', 0)} / "
                f"{documentation.get('interface_count', 0)}"
                f" ({documentation.get('interface_coverage_percent', 0):.2f}%)**"
            ),
            (
                "- Complete document fields: **"
                f"{documentation.get('complete_chunk_count', 0)} / "
                f"{documentation.get('chunk_count', 0)}"
                f" ({documentation.get('chunk_field_coverage_percent', 0):.2f}%)**"
            ),
            "",
            "See `ledger.csv` for per-interface details.",
        ]
    )
    (output_dir / "SUMMARY.en.md").write_text("\n".join(english_lines) + "\n", encoding="utf-8")
    _write_status_svg(summary, output_dir / "status.svg")
    return summary


def _run_one(api: ApiFunction, arguments: dict[str, Any], timeout: float) -> dict[str, Any]:
    command = [
        sys.executable,
        "-m",
        "akbridge.acceptance_worker",
        api.name,
        json.dumps(arguments, ensure_ascii=False),
    ]
    environment = os.environ.copy()
    environment["PYTHONIOENCODING"] = "utf-8"
    environment["PYTHONUTF8"] = "1"
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=environment,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"name": api.name, "status": "timeout", "error": f"exceeded {timeout}s"}

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        return {"name": api.name, "status": "worker_failed", "error": detail[-4000:]}
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError:
        return {
            "name": api.name,
            "status": "worker_failed",
            "error": f"invalid worker output: {completed.stdout[-4000:]}",
        }


def run_acceptance(
    catalog: dict[str, ApiFunction],
    output: Path,
    fixtures: dict[str, dict[str, Any]],
    *,
    names: list[str] | None = None,
    limit: int | None = None,
    timeout: float = 30,
    resume: bool = False,
    retry_statuses: set[str] | None = None,
    workers: int = 1,
    adapter_only: bool = False,
) -> dict[str, Any]:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    existing = _load_json(output, {}) if resume else {}
    results_by_name = {item["name"]: item for item in existing.get("results", [])}
    if retry_statuses:
        results_by_name = {
            name: item
            for name, item in results_by_name.items()
            if item.get("status") not in retry_statuses
        }
    selected = names or list(catalog)
    unknown = sorted(set(selected) - set(catalog))
    if unknown:
        raise ValueError(f"Unknown AKShare APIs: {', '.join(unknown)}")
    pending = [name for name in selected if name not in results_by_name]
    if limit is not None:
        pending = pending[:limit]

    report = {
        "akshare_version": getattr(akshare, "__version__", "unknown"),
        "generated_at": dt.datetime.now(dt.UTC).isoformat(),
        "results": list(results_by_name.values()),
    }

    def accept(name: str) -> dict[str, Any]:
        api = catalog[name]
        arguments = fixtures.get(name, {})
        if adapter_only:
            return _adapter_contract_result(api)
        missing = sorted(set(api.input_schema.get("required", [])) - set(arguments))
        if missing:
            return {
                "name": name,
                "status": "fixture_required",
                "error": f"missing required parameters: {', '.join(missing)}",
            }
        return _run_one(api, arguments, timeout)

    def record(name: str, result: dict[str, Any]) -> None:
        results_by_name[name] = result
        report["generated_at"] = dt.datetime.now(dt.UTC).isoformat()
        report["results"] = list(results_by_name.values())
        _write_report(output, report)
        print(f"{name}: {result['status']}", flush=True)

    if workers == 1:
        for name in pending:
            record(name, accept(name))
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(accept, name): name for name in pending}
            for future in as_completed(futures):
                name = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    result = {
                        "name": name,
                        "status": "worker_failed",
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                record(name, result)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or run AKShare interface acceptance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest_parser = subparsers.add_parser("manifest")
    manifest_parser.add_argument(
        "--output", type=Path, default=Path("artifacts/acceptance/manifest.json")
    )
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument(
        "--output", type=Path, default=Path("artifacts/acceptance/runs/all.json")
    )
    run_parser.add_argument(
        "--fixtures", type=Path, default=Path("artifacts/acceptance/fixtures.json")
    )
    run_parser.add_argument("--name", action="append", dest="names")
    run_parser.add_argument("--limit", type=int)
    run_parser.add_argument("--timeout", type=float, default=30)
    run_parser.add_argument("--resume", action="store_true")
    run_parser.add_argument("--retry-status", action="append", default=[])
    run_parser.add_argument("--workers", type=int, default=1)
    run_parser.add_argument(
        "--offline",
        "--adapter-only",
        dest="adapter_only",
        action="store_true",
        help="validate discovery and schemas without calling third-party providers",
    )
    report_parser = subparsers.add_parser("report")
    report_parser.add_argument(
        "--run", type=Path, default=Path("artifacts/acceptance/runs/all.json")
    )
    report_parser.add_argument("--output-dir", type=Path, default=Path("artifacts/acceptance"))
    args = parser.parse_args()

    if args.command == "manifest":
        manifest = write_manifest(args.output)
        print(f"Wrote {manifest['interface_count']} interfaces to {args.output}")
    elif args.command == "run":
        fixtures = _load_json(args.fixtures, {})
        report = run_acceptance(
            discover_functions(),
            args.output,
            fixtures,
            names=args.names,
            limit=args.limit,
            timeout=args.timeout,
            resume=args.resume,
            retry_statuses=set(args.retry_status),
            workers=args.workers,
            adapter_only=args.adapter_only,
        )
        counts: dict[str, int] = {}
        for result in report["results"]:
            counts[result["status"]] = counts.get(result["status"], 0) + 1
        print(json.dumps(counts, ensure_ascii=False, sort_keys=True))
        if args.adapter_only and any(
            item.get("status") != "adapter_passed" for item in report["results"]
        ):
            raise SystemExit(1)
    elif args.command == "report":
        summary = write_acceptance_artifacts(args.run, args.output_dir)
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
