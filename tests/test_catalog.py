from __future__ import annotations

from typing import Literal

import pandas as pd

from akbridge.catalog import coerce_arguments, discover_functions, signature_to_schema
from akbridge.router import CatalogIndex


def test_signature_to_schema_marks_required_and_defaults() -> None:
    def sample(symbol: str, count: int = 10) -> None:
        pass

    schema = signature_to_schema(sample)

    assert schema["required"] == ["symbol"]
    assert schema["properties"]["symbol"] == {"type": "string"}
    assert schema["properties"]["count"] == {"type": "integer", "default": 10}


def test_signature_to_schema_resolves_string_annotations() -> None:
    def sample(symbol: str) -> None:
        pass

    assert signature_to_schema(sample)["properties"]["symbol"] == {"type": "string"}


def test_signature_to_schema_supports_literal_enum() -> None:
    def sample(period: Literal["daily", "weekly"] = "daily") -> None:
        pass

    assert signature_to_schema(sample)["properties"]["period"] == {
        "enum": ["daily", "weekly"],
        "type": "string",
        "default": "daily",
    }


def test_dataframe_json_input_is_coerced() -> None:
    def sample(data: pd.DataFrame) -> None:
        pass

    arguments = coerce_arguments(sample, {"data": {"rows": [{"close": 10.5}]}})

    assert isinstance(arguments["data"], pd.DataFrame)
    assert arguments["data"].to_dict(orient="records") == [{"close": 10.5}]


def test_dataframe_json_input_supports_datetime_index_column() -> None:
    def sample(data: pd.DataFrame) -> None:
        pass

    arguments = coerce_arguments(
        sample,
        {"data": {"rows": [{"date": "2026-01-05", "close": 10.5}], "index_column": "date"}},
    )

    assert isinstance(arguments["data"].index, pd.DatetimeIndex)
    assert arguments["data"].index[0] == pd.Timestamp("2026-01-05")


def test_discovery_finds_public_akshare_functions() -> None:
    catalog = discover_functions()

    assert len(catalog) > 100
    assert "stock_zh_a_hist" in catalog
    assert "stock_info_a_code_name" in catalog
    assert catalog["stock_zh_a_hist"].input_schema["type"] == "object"
    assert CatalogIndex(catalog).search("A股历史行情", limit=1)[0].api.name == "stock_zh_a_hist"
