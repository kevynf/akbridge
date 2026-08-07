"""Convert AKShare results into MCP-compatible content."""

from __future__ import annotations

import datetime as dt
import math
from contextlib import suppress
from typing import Any

import pandas as pd

OUTPUT_MODES = {"raw", "compact", "summary"}
_UNIT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("percent", ("percent", "pct", "rate", "ratio", "收益率", "增长率", "占比", "%")),
    ("price", ("price", "open", "high", "low", "close", "价", "净值")),
    ("volume", ("volume", "vol", "amount", "数量", "成交量", "成交额")),
    ("date", ("date", "time", "datetime", "日期", "时间")),
    ("currency", ("currency", "cny", "usd", "rmb", "金额")),
)


def _clean_scalar(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dt.datetime, dt.date, dt.time, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        with suppress(TypeError, ValueError):
            value = value.item()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    return value


def _field_unit(name: str) -> str | None:
    lowered = str(name).casefold()
    for unit, terms in _UNIT_RULES:
        if any(term.casefold() in lowered for term in terms):
            return unit
    return None


def infer_field_metadata(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Infer small, non-authoritative field hints from a returned frame."""
    fields: list[dict[str, Any]] = []
    for column in frame.columns:
        series = frame[column]
        fields.append(
            {
                "name": str(column),
                "dtype": str(series.dtype),
                "nullable": bool(series.isna().any()),
                "unit": _field_unit(str(column)),
            }
        )
    return fields


def _page_values(
    total: int, *, row_limit: int, page: int, page_size: int | None
) -> tuple[int, int, int]:
    if page < 1:
        raise ValueError("page must be at least 1")
    if row_limit < 1:
        raise ValueError("row_limit must be at least 1")
    size = page_size if page_size is not None else row_limit
    if size < 1:
        raise ValueError("page_size must be at least 1")
    size = min(size, row_limit)
    page_count = max(1, math.ceil(total / size))
    return size, (page - 1) * size, page_count


def _numeric_summary(frame: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for column in frame.select_dtypes(include="number").columns:
        series = frame[column].dropna()
        if series.empty:
            continue
        summary[str(column)] = {
            "min": _clean_scalar(series.min()),
            "max": _clean_scalar(series.max()),
            "mean": _clean_scalar(series.mean()),
        }
    return summary


def _frame_records(frame: pd.DataFrame, *, row_limit: int) -> list[dict[str, Any]]:
    return [
        {str(key): to_jsonable(item, row_limit=row_limit) for key, item in row.items()}
        for row in frame.to_dict(orient="records")
    ]


def _dataframe_json(
    value: pd.DataFrame,
    *,
    row_limit: int,
    mode: str,
    page: int,
    page_size: int | None,
    include_metadata: bool,
    include_index: bool,
) -> dict[str, Any]:
    # Preserve the original wire shape for existing direct-tool clients.
    legacy = (
        mode == "raw"
        and page == 1
        and page_size is None
        and not include_metadata
        and not include_index
    )
    if legacy:
        frame = value.head(row_limit)
        return {
            "type": "dataframe",
            "columns": [str(column) for column in frame.columns],
            "rows": _frame_records(frame, row_limit=row_limit),
            "row_count": len(value),
            "truncated": len(value) > row_limit,
        }

    size, start, page_count = _page_values(
        len(value), row_limit=row_limit, page=page, page_size=page_size
    )
    frame = value.iloc[start : start + size]
    records = _frame_records(frame, row_limit=row_limit)
    if mode == "summary":
        preview = _frame_records(frame.head(min(3, row_limit)), row_limit=row_limit)
        payload: dict[str, Any] = {
            "type": "dataframe_summary",
            "row_count": len(value),
            "columns": [str(column) for column in value.columns],
            "dtypes": {str(column): str(dtype) for column, dtype in value.dtypes.items()},
            "null_counts": {
                str(column): int(count) for column, count in value.isna().sum().items()
            },
            "numeric_summary": _numeric_summary(value),
            "preview": preview,
            "page": page,
            "page_count": page_count,
        }
    else:
        payload = {
            "type": "dataframe",
            "columns": [str(column) for column in value.columns],
            "rows": records,
            "row_count": len(value),
            "truncated": start + len(frame) < len(value),
            "page": page,
            "page_size": len(frame),
            "page_count": page_count,
            "next_page": page + 1 if page < page_count else None,
        }
    if include_metadata or mode != "raw":
        payload["fields"] = infer_field_metadata(value)
    if include_index:
        payload["index"] = [_clean_scalar(item) for item in frame.index.tolist()]
    return payload


def _series_json(
    value: pd.Series,
    *,
    row_limit: int,
    mode: str,
    page: int,
    page_size: int | None,
    include_metadata: bool,
) -> dict[str, Any]:
    legacy = mode == "raw" and page == 1 and page_size is None and not include_metadata
    if legacy:
        series = value.head(row_limit)
        return {
            "type": "series",
            "name": str(value.name) if value.name is not None else None,
            "values": [to_jsonable(item, row_limit=row_limit) for item in series.tolist()],
            "row_count": len(value),
            "truncated": len(value) > row_limit,
        }
    size, start, page_count = _page_values(
        len(value), row_limit=row_limit, page=page, page_size=page_size
    )
    series = value.iloc[start : start + size]
    payload: dict[str, Any]
    if mode == "summary":
        payload = {
            "type": "series_summary",
            "name": str(value.name) if value.name is not None else None,
            "row_count": len(value),
            "dtype": str(value.dtype),
            "null_count": int(value.isna().sum()),
            "preview": [to_jsonable(item, row_limit=row_limit) for item in series.head(3).tolist()],
            "page": page,
            "page_count": page_count,
        }
    else:
        payload = {
            "type": "series",
            "name": str(value.name) if value.name is not None else None,
            "values": [to_jsonable(item, row_limit=row_limit) for item in series.tolist()],
            "row_count": len(value),
            "truncated": start + len(series) < len(value),
            "page": page,
            "page_size": len(series),
            "page_count": page_count,
            "next_page": page + 1 if page < page_count else None,
        }
    if include_metadata or mode != "raw":
        payload["field"] = {
            "name": str(value.name) if value.name is not None else None,
            "dtype": str(value.dtype),
            "unit": _field_unit(str(value.name or "")),
        }
    return payload


def to_jsonable(
    value: Any,
    *,
    row_limit: int = 5000,
    mode: str = "raw",
    page: int = 1,
    page_size: int | None = None,
    include_metadata: bool = False,
    include_index: bool = False,
) -> Any:
    """Convert AKShare values to JSON-safe values with optional compact output.

    ``raw`` with default options intentionally retains the original AKBridge
    response shape.  ``compact`` adds pagination and field hints, while
    ``summary`` avoids returning a complete table and is useful for discovery.
    """
    if mode not in OUTPUT_MODES:
        raise ValueError(f"unsupported output mode: {mode}; choose raw, compact, or summary")
    if isinstance(value, pd.DataFrame):
        return _dataframe_json(
            value,
            row_limit=row_limit,
            mode=mode,
            page=page,
            page_size=page_size,
            include_metadata=include_metadata,
            include_index=include_index,
        )
    if isinstance(value, pd.Series):
        return _series_json(
            value,
            row_limit=row_limit,
            mode=mode,
            page=page,
            page_size=page_size,
            include_metadata=include_metadata,
        )
    if isinstance(value, dict):
        return {
            str(key): to_jsonable(
                item,
                row_limit=row_limit,
                mode=mode,
                page=page,
                page_size=page_size,
                include_metadata=include_metadata,
                include_index=include_index,
            )
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        values = list(value)
        if mode == "summary":
            return {
                "type": "list_summary",
                "length": len(values),
                "preview": [
                    to_jsonable(item, row_limit=row_limit, mode="raw") for item in values[:3]
                ],
            }
        return [
            to_jsonable(
                item,
                row_limit=row_limit,
                mode=mode,
                page=page,
                page_size=page_size,
                include_metadata=include_metadata,
                include_index=include_index,
            )
            for item in values[:row_limit]
        ]
    scalar = _clean_scalar(value)
    if mode == "summary" and include_metadata:
        return {"type": "scalar", "value": scalar}
    return scalar


def serialize_result(value: Any, **options: Any) -> Any:
    """Explicit alias used by the router and downstream integrations."""
    return to_jsonable(value, **options)
