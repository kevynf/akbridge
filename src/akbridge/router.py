"""Deterministic catalog search and routing for model-friendly MCP usage.

The router is intentionally lexical and local.  It does not call an LLM, a
remote embedding service, or a documentation website, so search results are
repeatable in CI and can be used as a small RAG context window.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .catalog import CATEGORY_LABELS, ApiFunction

ROUTER_TOOL_NAMES = ("akbridge_search", "akbridge_describe", "akbridge_call")

DOMAIN_TERMS: dict[str, tuple[str, ...]] = {
    "stock": ("stock", "股票", "a股", "港股", "美股", "证券"),
    "fund": ("fund", "基金", "etf", "理财"),
    "macro": ("macro", "宏观", "经济", "cpi", "ppi", "gdp"),
    "futures": ("futures", "期货", "商品"),
    "option": ("option", "期权"),
    "bond": ("bond", "债券", "国债"),
    "forex": ("forex", "fx", "外汇", "汇率"),
    "index": ("index", "指数"),
    "crypto": ("crypto", "加密", "数字货币"),
    "energy": ("energy", "能源", "原油", "煤炭"),
    "banking": ("banking", "银行", "利率"),
}

SEARCH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "description": "Function name, alias, category, or natural-language keywords.",
            "default": "",
        },
        "category": {
            "type": "string",
            "description": "Optional category id, such as stock or macro.",
        },
        "limit": {"type": "integer", "minimum": 1, "maximum": 50, "default": 8},
    },
    "additionalProperties": False,
}

DESCRIBE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {
            "type": "string",
            "description": "Canonical AKShare function name or unique alias.",
        },
        "include_schema": {"type": "boolean", "default": True},
    },
    "required": ["name"],
    "additionalProperties": False,
}

CALL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Canonical function name or unique alias."},
        "arguments": {"type": "object", "default": {}},
        "output_mode": {
            "type": "string",
            "enum": ["raw", "compact", "summary"],
            "default": "compact",
        },
        "page": {"type": "integer", "minimum": 1, "default": 1},
        "page_size": {"type": "integer", "minimum": 1, "maximum": 5000},
        "include_metadata": {"type": "boolean", "default": True},
    },
    "required": ["name"],
    "additionalProperties": False,
}

_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _compact(value: str) -> str:
    """Normalize punctuation and spacing for mixed Chinese/English queries."""
    return re.sub(r"[^\w\u4e00-\u9fff]", "", value.casefold())


def _tokens(value: str) -> tuple[str, ...]:
    normalized = value.casefold().replace("-", "_")
    pieces = [piece for piece in _TOKEN_RE.findall(normalized) if piece]
    # Include underscore-separated terms as well as the complete phrase.
    expanded: list[str] = []
    for piece in pieces:
        expanded.append(piece)
        expanded.extend(part for part in piece.split("_") if part and part != piece)
        if re.fullmatch(r"[\u4e00-\u9fff]+", piece):
            for width in range(2, min(4, len(piece)) + 1):
                expanded.extend(
                    piece[offset : offset + width] for offset in range(len(piece) - width + 1)
                )
    return tuple(dict.fromkeys(expanded))


@dataclass(frozen=True, slots=True)
class SearchHit:
    api: ApiFunction
    score: float
    documentation: tuple[dict[str, str], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.api.name,
            "display_name": self.api.display_name or self.api.name,
            "category": self.api.category,
            "category_label": CATEGORY_LABELS.get(self.api.category, self.api.category),
            "aliases": list(self.api.aliases),
            "use_cases": list(self.api.use_cases),
            "signature": self.api.signature,
            "description": self.api.description.splitlines()[0][:500],
            "return": self.api.return_metadata,
            "side_effect": self.api.side_effect,
            "score": round(self.score, 4),
            "documentation": [
                {key: item[key] for key in ("title", "source_url")} for item in self.documentation
            ],
        }


class CatalogIndex:
    """An in-memory inverted lexical index over :class:`ApiFunction` values."""

    def __init__(
        self, catalog: dict[str, ApiFunction], documents: list[dict[str, Any]] | None = None
    ):
        self.catalog = catalog
        self._aliases: dict[str, list[str]] = {}
        self._documents: dict[str, str] = {}
        self._compact_documents: dict[str, str] = {}
        self._documentation: dict[str, list[dict[str, str]]] = {name: [] for name in catalog}
        for name, api in catalog.items():
            for alias in (name, *api.aliases):
                key = alias.casefold().strip()
                if key:
                    self._aliases.setdefault(key, []).append(name)
            self._documents[name] = " ".join(
                (
                    name,
                    api.display_name,
                    " ".join(api.aliases),
                    api.category,
                    CATEGORY_LABELS.get(api.category, ""),
                    " ".join(api.use_cases),
                    api.description,
                )
            ).casefold()
            self._compact_documents[name] = _compact(self._documents[name])
        for chunk in documents or []:
            text = str(chunk.get("text", "")).strip()
            if not text:
                continue
            reference = {
                "title": str(chunk.get("title", "")),
                "source_url": str(chunk.get("source_url", "")),
            }
            for name in chunk.get("api_names", []):
                if name in self.catalog:
                    self._documentation[name].append(reference)
                    self._documents[name] += " " + text.casefold()
                    self._compact_documents[name] = _compact(self._documents[name])

    def resolve(self, name: str) -> ApiFunction | None:
        if not isinstance(name, str):
            return None
        direct = self.catalog.get(name)
        if direct is not None:
            return direct
        matches = self._aliases.get(name.casefold().strip(), [])
        if len(matches) == 1:
            return self.catalog[matches[0]]
        return None

    def suggestions(self, name: str, *, limit: int = 5) -> list[dict[str, Any]]:
        return [hit.as_dict() for hit in self.search(name, limit=limit)]

    def search(
        self,
        query: str = "",
        *,
        category: str | None = None,
        limit: int = 8,
    ) -> list[SearchHit]:
        query = str(query or "").strip()
        limit = max(1, min(int(limit), 50))
        query_terms = _tokens(query)
        compact_query = _compact(query)
        category_key = str(category or "").casefold().strip()
        routed_categories = {
            category_name
            for category_name, terms in DOMAIN_TERMS.items()
            if any(term in compact_query or term in query.casefold() for term in terms)
        }
        hits: list[SearchHit] = []
        for name, api in self.catalog.items():
            if category_key and api.category.casefold() != category_key:
                continue
            document = self._documents[name]
            if not query_terms:
                score = 1.0
            else:
                score = 0.0
                name_key = name.casefold()
                alias_keys = {alias.casefold() for alias in api.aliases}
                for term in query_terms:
                    if term == name_key:
                        score += 100.0
                    elif term in alias_keys:
                        score += 60.0
                    elif term in name_key:
                        score += 30.0
                    elif term in document:
                        score += 5.0
                if query.casefold() in name_key:
                    score += 40.0
                if query.casefold() and query.casefold() in document:
                    score += 10.0
                if compact_query and compact_query in self._compact_documents[name]:
                    score += 35.0
                if api.category in routed_categories:
                    score += 15.0
            if score > 0:
                hits.append(SearchHit(api, score, tuple(self._documentation[name][:3])))
        hits.sort(key=lambda hit: (-hit.score, hit.api.name))
        return hits[:limit]

    def search_payload(
        self, query: str = "", *, category: str | None = None, limit: int = 8
    ) -> dict[str, Any]:
        hits = self.search(query, category=category, limit=limit)
        return {
            "query": query,
            "category": category,
            "count": len(hits),
            "results": [hit.as_dict() for hit in hits],
            "context": self.context(query, category=category, limit=limit),
        }

    def describe(self, name: str, *, include_schema: bool = True) -> dict[str, Any]:
        api = self.resolve(name)
        if api is None:
            suggestions = self.suggestions(name)
            detail = f"Unknown AKShare API: {name}"
            if suggestions:
                detail += "; suggestions: " + ", ".join(item["name"] for item in suggestions)
            raise KeyError(detail)
        payload = api.as_metadata(include_schema=include_schema)
        payload["category_label"] = CATEGORY_LABELS.get(api.category, api.category)
        payload["required_parameters"] = api.input_schema.get("required", [])
        payload["router_usage"] = {
            "tool": "akbridge_call",
            "name": api.name,
            "arguments": api.examples[0] if api.examples else {},
        }
        return payload

    def context(self, query: str = "", *, category: str | None = None, limit: int = 8) -> str:
        """Return a compact, deterministic RAG context block."""
        hits = self.search(query, category=category, limit=limit)
        if not hits:
            return "No matching AKShare interface. Search by canonical name or category."
        lines = ["AKBridge catalog matches:"]
        for hit in hits:
            api = hit.api
            summary = api.description.splitlines()[0][:180]
            lines.append(f"- {api.name} [{api.category}]: {summary} | {api.signature}")
        return "\n".join(lines)


def build_catalog_index(catalog: dict[str, ApiFunction]) -> CatalogIndex:
    """Construct the deterministic local index (explicit public helper)."""
    return CatalogIndex(catalog)


def search_catalog(
    catalog: dict[str, ApiFunction], query: str = "", *, category: str | None = None, limit: int = 8
) -> dict[str, Any]:
    """Search a catalog without requiring callers to manage an index object."""
    return CatalogIndex(catalog).search_payload(query, category=category, limit=limit)


def describe_api(
    catalog: dict[str, ApiFunction], name: str, *, include_schema: bool = True
) -> dict[str, Any]:
    """Describe one API using the same resolver as ``akbridge_describe``."""
    return CatalogIndex(catalog).describe(name, include_schema=include_schema)


def router_tool_definitions() -> tuple[dict[str, Any], ...]:
    """Return serializable definitions used by the MCP server and tests."""
    return (
        {
            "name": "akbridge_search",
            "description": (
                "Search the local AKShare semantic catalog and return a small RAG context."
            ),
            "inputSchema": SEARCH_SCHEMA,
        },
        {
            "name": "akbridge_describe",
            "description": (
                "Describe one AKShare interface, including parameters and return metadata."
            ),
            "inputSchema": DESCRIBE_SCHEMA,
        },
        {
            "name": "akbridge_call",
            "description": (
                "Resolve and call one AKShare interface with deterministic output controls."
            ),
            "inputSchema": CALL_SCHEMA,
        },
    )
