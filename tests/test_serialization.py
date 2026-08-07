from __future__ import annotations

import pandas as pd

from akbridge.serialization import to_jsonable


def test_dataframe_serialization_has_shape_and_truncation() -> None:
    frame = pd.DataFrame({"value": [1, 2, 3]})

    result = to_jsonable(frame, row_limit=2)

    assert result == {
        "type": "dataframe",
        "columns": ["value"],
        "rows": [{"value": 1}, {"value": 2}],
        "row_count": 3,
        "truncated": True,
    }
