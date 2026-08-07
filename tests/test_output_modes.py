from __future__ import annotations

import pandas as pd

from akbridge.serialization import to_jsonable


def test_compact_output_has_pagination_and_units() -> None:
    frame = pd.DataFrame(
        {"date": ["2026-01-01", "2026-01-02", "2026-01-03"], "close": [1.0, 2.0, 3.0]}
    )

    result = to_jsonable(frame, mode="compact", page=2, page_size=1)

    assert result["rows"] == [{"date": "2026-01-02", "close": 2.0}]
    assert result["page_count"] == 3
    assert result["next_page"] == 3
    assert {field["unit"] for field in result["fields"]} == {"date", "price"}


def test_summary_output_avoids_full_table_and_reports_statistics() -> None:
    frame = pd.DataFrame({"value": [1, 2, 3]})

    result = to_jsonable(frame, mode="summary")

    assert result["type"] == "dataframe_summary"
    assert result["row_count"] == 3
    assert result["numeric_summary"]["value"]["mean"] == 2.0
    assert "rows" not in result


def test_default_raw_shape_remains_backward_compatible() -> None:
    frame = pd.DataFrame({"value": [1, 2, 3]})

    assert to_jsonable(frame, row_limit=2) == {
        "type": "dataframe",
        "columns": ["value"],
        "rows": [{"value": 1}, {"value": 2}],
        "row_count": 3,
        "truncated": True,
    }
