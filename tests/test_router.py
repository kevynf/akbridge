from __future__ import annotations

from typing import Any

from akbridge.catalog import ApiFunction, signature_to_schema
from akbridge.router import CatalogIndex, router_tool_definitions


def _api(
    name: str, description: str, *, category: str = "other", source_module: str = ""
) -> ApiFunction:
    def function(symbol: str = "000001") -> dict[str, Any]:
        return {"symbol": symbol}

    return ApiFunction(
        name=name,
        function=function,
        description=description,
        input_schema=signature_to_schema(function),
        signature="(symbol: str = '000001') -> dict",
        display_name=description,
        category=category,
        aliases=("A股历史行情", "历史行情") if name == "stock_zh_a_hist" else (),
        use_cases=("查询数据",),
        examples=({"symbol": "000001"},),
        return_metadata={"kind": "object"},
        source_module=source_module,
    )


def test_search_is_deterministic_and_resolves_alias() -> None:
    index = CatalogIndex(
        {
            "stock_zh_a_hist": _api("stock_zh_a_hist", "A股历史行情", category="stock"),
            "macro_china_cpi": _api("macro_china_cpi", "中国居民消费价格指数", category="macro"),
        }
    )

    first = index.search_payload("A股历史行情", limit=2)
    second = index.search_payload("A股历史行情", limit=2)

    assert first == second
    assert first["results"][0]["name"] == "stock_zh_a_hist"
    assert index.resolve("历史行情").name == "stock_zh_a_hist"  # type: ignore[union-attr]
    assert "AKBridge catalog matches" in first["context"]


def test_describe_contains_schema_and_router_usage() -> None:
    index = CatalogIndex({"sample": _api("sample", "Sample API")})

    payload = index.describe("sample")

    assert payload["name"] == "sample"
    assert payload["input_schema"]["type"] == "object"
    assert payload["router_usage"]["tool"] == "akbridge_call"


def test_router_exposes_exactly_three_stable_tools() -> None:
    assert {item["name"] for item in router_tool_definitions()} == {
        "akbridge_search",
        "akbridge_describe",
        "akbridge_call",
    }


def test_search_uses_document_text_and_domain_terms_for_routing() -> None:
    index = CatalogIndex(
        {
            "stock_zh_a_hist": _api("stock_zh_a_hist", "A股历史行情", category="stock"),
            "macro_china_cpi": _api("macro_china_cpi", "CPI", category="macro"),
        },
        documents=[
            {
                "title": "居民消费价格指数",
                "source_url": "https://example.test/cpi",
                "text": "居民消费价格指数反映中国宏观经济中的消费价格变化。",
                "api_names": ["macro_china_cpi"],
            }
        ],
    )

    payload = index.search_payload("中国居民消费价格指数")

    assert payload["results"][0]["name"] == "macro_china_cpi"
    assert payload["results"][0]["documentation"][0]["source_url"] == "https://example.test/cpi"
    assert index.search("宏观经济")[0].api.name == "macro_china_cpi"


def test_route_table_is_generated_from_source_modules() -> None:
    stock = _api(
        "stock_zh_a_hist",
        "A股历史行情",
        category="stock",
        source_module="akshare.stock_feature.stock_hist_em",
    )
    macro = _api(
        "macro_china_cpi",
        "CPI",
        category="macro",
        source_module="akshare.economic.macro_china",
    )

    index = CatalogIndex({stock.name: stock, macro.name: macro})

    assert index.route_table()["stock"]["stock_feature.stock_hist_em"] == ["stock_zh_a_hist"]
    result = index.search_payload("stock_hist_em")["results"][0]
    assert result["route"]["path"] == [
        "stock",
        "stock_feature",
        "stock_hist_em",
        "stock_zh_a_hist",
    ]
