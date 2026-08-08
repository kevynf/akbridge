"""Discover AKShare functions and translate their signatures to MCP schemas."""

from __future__ import annotations

import inspect
import json
import os
import re
import types
import typing
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

import akshare
import pandas as pd

JSON_PRIMITIVES: dict[Any, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


@dataclass(frozen=True, slots=True)
class ApiFunction:
    """A public AKShare function and its MCP metadata."""

    name: str
    function: Callable[..., Any]
    description: str
    input_schema: dict[str, Any]
    signature: str
    # The fields below are generated deterministically from the AKShare name,
    # signature and docstring.  Defaults keep the small public constructor
    # backwards compatible for acceptance fixtures and downstream users.
    display_name: str = ""
    category: str = "other"
    aliases: tuple[str, ...] = ()
    use_cases: tuple[str, ...] = ()
    examples: tuple[dict[str, Any], ...] = ()
    return_metadata: dict[str, Any] = field(default_factory=dict)
    parameter_metadata: dict[str, Any] = field(default_factory=dict)
    side_effect: bool = False
    source_url: str | None = None
    source_module: str = ""

    def as_metadata(self, *, include_schema: bool = True) -> dict[str, Any]:
        """Return JSON-safe metadata suitable for a catalog or RAG result."""
        payload: dict[str, Any] = {
            "name": self.name,
            "display_name": self.display_name or self.name,
            "category": self.category,
            "aliases": list(self.aliases),
            "use_cases": list(self.use_cases),
            "examples": list(self.examples),
            "description": self.description,
            "signature": self.signature,
            "return": self.return_metadata,
            "parameters": self.parameter_metadata,
            "side_effect": self.side_effect,
            "source_url": self.source_url,
            "source_module": self.source_module,
        }
        if include_schema:
            payload["input_schema"] = self.input_schema
        return payload


def _annotation_schema(annotation: Any) -> dict[str, Any]:
    if annotation in (inspect.Parameter.empty, Any, typing.Any):
        return {}
    if annotation is None or annotation is type(None):
        return {"type": "null"}
    if annotation in JSON_PRIMITIVES:
        return {"type": JSON_PRIMITIVES[annotation]}
    if annotation is pd.DataFrame:
        records = {"type": "array", "items": {"type": "object"}}
        return {
            "oneOf": [
                records,
                {
                    "type": "object",
                    "properties": {
                        "rows": records,
                        "index": {"type": "array"},
                        "index_column": {"type": "string"},
                    },
                    "required": ["rows"],
                },
            ]
        }
    if annotation is pd.Series:
        return {"type": "array"}

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Literal:
        schema: dict[str, Any] = {"enum": list(args)}
        value_types = {type(item) for item in args}
        if len(value_types) == 1 and next(iter(value_types)) in JSON_PRIMITIVES:
            schema["type"] = JSON_PRIMITIVES[next(iter(value_types))]
        return schema
    if origin in (list, tuple, set, frozenset):
        item_schema = _annotation_schema(args[0]) if args else {}
        return {"type": "array", "items": item_schema}
    if origin is dict:
        return {"type": "object"}
    if origin in (typing.Union, types.UnionType):
        return {"anyOf": [_annotation_schema(item) for item in args]}

    # AKShare uses a small number of non-JSON annotations. Accepting an
    # unconstrained JSON value is more useful than dropping the function.
    return {}


def signature_to_schema(function: Callable[..., Any]) -> dict[str, Any]:
    """Create a permissive JSON Schema from a Python callable signature."""
    properties: dict[str, Any] = {}
    required: list[str] = []
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        return {"type": "object", "properties": {}, "additionalProperties": True}
    try:
        type_hints = typing.get_type_hints(function)
    except (AttributeError, ImportError, NameError, TypeError):
        type_hints = {}

    for parameter in signature.parameters.values():
        if parameter.name in {"self", "cls"}:
            continue
        if parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD):
            continue
        annotation = type_hints.get(parameter.name, parameter.annotation)
        schema = _annotation_schema(annotation)
        if parameter.default is not inspect.Parameter.empty:
            default = parameter.default
            if default is None or isinstance(default, (str, int, float, bool, list, dict)):
                schema = {**schema, "default": default}
        else:
            required.append(parameter.name)
        properties[parameter.name] = schema

    result: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        result["required"] = required
    return result


def coerce_arguments(function: Callable[..., Any], arguments: dict[str, Any]) -> dict[str, Any]:
    """Translate JSON representations into annotated pandas inputs."""
    try:
        type_hints = typing.get_type_hints(function)
    except (AttributeError, ImportError, NameError, TypeError):
        type_hints = {}
    coerced = dict(arguments)

    def includes(annotation: Any, target: Any) -> bool:
        if annotation is target:
            return True
        return target in typing.get_args(annotation)

    for name, value in arguments.items():
        annotation = type_hints.get(name)
        if (
            includes(annotation, pd.DataFrame)
            and not isinstance(value, pd.DataFrame)
            and value is not None
        ):
            records = value.get("rows", []) if isinstance(value, dict) else value
            frame = pd.DataFrame(records)
            if isinstance(value, dict) and "index" in value:
                frame.index = pd.Index(value["index"])
            elif (
                isinstance(value, dict)
                and value.get("index_column")
                and value.get("index_column") in frame.columns
            ):
                frame.set_index(value["index_column"], inplace=True)
            if len(frame.index) and all(isinstance(item, str) for item in frame.index):
                with suppress(TypeError, ValueError):
                    frame.index = pd.to_datetime(frame.index)
            coerced[name] = frame
        elif (
            includes(annotation, pd.Series)
            and not isinstance(value, pd.Series)
            and value is not None
        ):
            coerced[name] = pd.Series(value)
    return coerced


def _description(function: Callable[..., Any], name: str) -> str:
    doc = inspect.getdoc(function) or f"AKShare API: {name}"
    return doc[:4000]


_CATEGORY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("stock", ("stock", "share", "equity", "a_stock")),
    ("fund", ("fund", "etf", "lof", "qdii")),
    ("bond", ("bond", "convertible")),
    ("futures", ("future", "futures", "commodity")),
    ("option", ("option", "options")),
    ("index", ("index", "indices")),
    ("macro", ("macro", "gdp", "cpi", "ppi", "pmi", "nbs")),
    ("forex", ("forex", "fx", "currency", "exchange_rate")),
    ("crypto", ("crypto", "bitcoin", "btc", "eth")),
    ("news", ("news", "notice", "report", "announcement")),
    ("banking", ("bank", "loan", "deposit", "interbank")),
    ("insurance", ("insurance", "insure")),
    ("energy", ("energy", "oil", "gas", "coal", "electricity")),
    ("calculation", ("volatility", "indicator", "technical", "factor", "ta_")),
)

CATEGORY_LABELS: dict[str, str] = {
    "stock": "股票",
    "fund": "基金",
    "bond": "债券",
    "futures": "期货与商品",
    "option": "期权",
    "index": "指数",
    "macro": "宏观经济",
    "forex": "外汇",
    "crypto": "数字资产",
    "news": "公告与资讯",
    "banking": "银行与利率",
    "insurance": "保险",
    "energy": "能源",
    "calculation": "指标与计算",
    "other": "其他",
}

_SECRET_NAMES = re.compile(
    r"(?:token|secret|password|passwd|api[_-]?key|authorization|cookie)", re.I
)
_URL_RE = re.compile(r"https?://[^\s)]+")
_PARAM_RE = re.compile(r"^:param\s+(?P<name>[^:]+):\s*(?P<description>.*)$", re.I)
_TYPE_RE = re.compile(r"^:type\s+(?P<name>[^:]+):\s*(?P<type>.*)$", re.I)


def _category_for(name: str, description: str) -> str:
    haystack = name.lower()
    for category, terms in _CATEGORY_RULES:
        if any(term in haystack for term in terms):
            return category
    # A few APIs have useful Chinese-only descriptions and no obvious prefix.
    text = description.lower()
    if any(term in text for term in ("股票", "个股", "a股")):
        return "stock"
    if "基金" in text:
        return "fund"
    if any(term in text for term in ("宏观", "国内生产总值", "消费者价格")):
        return "macro"
    return "other"


def _parse_doc_metadata(
    function: Callable[..., Any], name: str, description: str
) -> tuple[str, dict[str, Any], str | None]:
    lines = [line.strip() for line in description.splitlines()]
    title = next(
        (
            line
            for line in lines
            if line and not line.startswith(":") and not line.startswith("http")
        ),
        name,
    )
    params: dict[str, Any] = {}
    current: str | None = None
    for line in lines:
        match = _PARAM_RE.match(line)
        if match:
            current = match.group("name").strip()
            params.setdefault(current, {})["description"] = match.group("description").strip()
            continue
        match = _TYPE_RE.match(line)
        if match:
            current = match.group("name").strip()
            params.setdefault(current, {})["type"] = match.group("type").strip()
    source_url = next(iter(_URL_RE.findall(description)), None)
    return title[:180], params, source_url


def _return_metadata(function: Callable[..., Any], description: str) -> dict[str, Any]:
    try:
        hints = typing.get_type_hints(function)
    except (AttributeError, ImportError, NameError, TypeError):
        hints = getattr(function, "__annotations__", {}) or {}
    annotation = hints.get("return")
    if annotation is pd.DataFrame:
        kind = "dataframe"
    elif annotation is pd.Series:
        kind = "series"
    elif annotation in JSON_PRIMITIVES:
        kind = JSON_PRIMITIVES[annotation]
    elif annotation in (None, type(None)):
        kind = "null"
    else:
        kind = "unknown"
    return_line = ""
    for line in description.splitlines():
        if line.strip().lower().startswith(":return:"):
            return_line = line.split(":", 2)[-1].strip()
            break
    return {"kind": kind, "description": return_line or None, "columns": []}


def _example_for(function: Callable[..., Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Build a safe, deterministic example without invoking the provider."""
    values: dict[str, Any] = {}
    try:
        signature = inspect.signature(function)
    except (TypeError, ValueError):
        signature = None
    if signature:
        for parameter in signature.parameters.values():
            if parameter.name in {"self", "cls"} or parameter.kind in (
                parameter.VAR_POSITIONAL,
                parameter.VAR_KEYWORD,
            ):
                continue
            if parameter.default is not inspect.Parameter.empty:
                default = parameter.default
                if default is None or isinstance(default, (str, int, float, bool)):
                    values[parameter.name] = default
            elif parameter.name in {"symbol", "code", "stock", "security"}:
                values[parameter.name] = "000001"
            elif parameter.name in {"start_date", "begin_date"}:
                values[parameter.name] = "20240101"
            elif parameter.name in {"end_date", "finish_date"}:
                values[parameter.name] = "20240131"
            elif parameter.annotation is str:
                values[parameter.name] = ""
    return values


def _semantic_aliases(name: str, title: str) -> tuple[str, ...]:
    """Generate a small bilingual synonym set from stable name fragments."""
    aliases: list[str] = [name.replace("_", " "), name.lower(), title]
    fragments = set(name.casefold().split("_"))
    fragment_aliases = {
        "stock": "股票",
        "fund": "基金",
        "bond": "债券",
        "futures": "期货",
        "option": "期权",
        "index": "指数",
        "macro": "宏观经济",
        "forex": "外汇",
        "currency": "汇率",
        "hist": "历史数据",
        "spot": "实时行情",
        "realtime": "实时行情",
        "daily": "日线数据",
        "minute": "分钟行情",
        "news": "财经资讯",
        "cpi": "消费者价格指数",
        "ppi": "生产者价格指数",
        "pmi": "采购经理指数",
        "em": "东方财富",
    }
    aliases.extend(
        fragment_aliases[token] for token in sorted(fragments) if token in fragment_aliases
    )
    if name.startswith("stock_zh_a_"):
        aliases.append("A股")
        if "hist" in fragments:
            aliases.append("A股历史行情")
        if "spot" in fragments or "realtime" in fragments:
            aliases.append("A股实时行情")
    if name.startswith("fund_") and "hist" in fragments:
        aliases.append("基金历史净值")
    if name.startswith("macro_china_"):
        aliases.append("中国宏观经济")
    return tuple(dict.fromkeys(alias for alias in aliases if alias))


def _semantic_metadata(
    name: str,
    function: Callable[..., Any],
    description: str,
    schema: dict[str, Any],
    signature: str,
) -> dict[str, Any]:
    title, parameter_metadata, source_url = _parse_doc_metadata(function, name, description)
    category = _category_for(name, description)
    aliases = _semantic_aliases(name, title)
    use_cases = (
        f"查询{CATEGORY_LABELS.get(category, CATEGORY_LABELS['other'])}数据",
        "作为结构化数据分析或回测的输入",
    )
    if category == "calculation":
        use_cases = ("对本地或传入数据执行指标计算", "生成可进一步分析的结构化结果")
    side_effect = bool(
        _SECRET_NAMES.search(name) or name.startswith(("set_", "login", "logout", "configure"))
    )
    return {
        "display_name": title if title != name else name.replace("_", " "),
        "category": category,
        "aliases": aliases,
        "use_cases": use_cases,
        "examples": (_example_for(function, schema),),
        "return_metadata": _return_metadata(function, description),
        "parameter_metadata": parameter_metadata,
        "side_effect": side_effect,
        "source_url": source_url,
        "signature": signature,
    }


def _load_overrides(path: str | os.PathLike[str] | None = None) -> dict[str, dict[str, Any]]:
    override_path = path or os.getenv("AKBRIDGE_CATALOG_OVERRIDES")
    if not override_path:
        return {}
    candidate = os.fspath(override_path)
    try:
        with open(candidate, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def discover_functions(
    *, overrides_path: str | os.PathLike[str] | None = None
) -> dict[str, ApiFunction]:
    """Return every public function exported by the top-level AKShare package."""
    catalog: dict[str, ApiFunction] = {}
    overrides = _load_overrides(overrides_path)
    for name in sorted(dir(akshare)):
        if name.startswith("_"):
            continue
        value = getattr(akshare, name)
        if not callable(value) or inspect.isclass(value):
            continue
        try:
            signature = str(inspect.signature(value))
        except (TypeError, ValueError):
            signature = "(*args, **kwargs)"
        description = _description(value, name)
        metadata = _semantic_metadata(
            name, value, description, signature_to_schema(value), signature
        )
        override = overrides.get(name, {})
        if isinstance(override, dict):
            for key in (
                "display_name",
                "category",
                "aliases",
                "use_cases",
                "examples",
                "return_metadata",
                "parameter_metadata",
                "side_effect",
                "source_url",
            ):
                if key in override:
                    metadata[key] = override[key]
        catalog[name] = ApiFunction(
            name=name,
            function=value,
            description=description,
            input_schema=signature_to_schema(value),
            signature=signature,
            display_name=str(metadata["display_name"]),
            category=str(metadata["category"]),
            aliases=tuple(str(item) for item in metadata["aliases"]),
            use_cases=tuple(str(item) for item in metadata["use_cases"]),
            examples=tuple(item for item in metadata["examples"] if isinstance(item, dict)),
            return_metadata=dict(metadata["return_metadata"]),
            parameter_metadata=dict(metadata["parameter_metadata"]),
            side_effect=bool(metadata["side_effect"]),
            source_url=metadata["source_url"],
            source_module=str(getattr(value, "__module__", "")),
        )
    return catalog
