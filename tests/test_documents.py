from __future__ import annotations

import io
import zipfile

from akbridge.catalog import ApiFunction, signature_to_schema
from akbridge.documents import build_document_corpus


def _catalog() -> dict[str, ApiFunction]:
    def macro_china_cpi() -> dict[str, str]:
        return {}

    return {
        "macro_china_cpi": ApiFunction(
            name="macro_china_cpi",
            function=macro_china_cpi,
            description="CPI",
            input_schema=signature_to_schema(macro_china_cpi),
            signature="() -> dict[str, str]",
            category="macro",
        )
    }


def test_build_document_corpus_links_sections_to_public_apis() -> None:
    payload = io.BytesIO()
    with zipfile.ZipFile(payload, "w") as archive:
        archive.writestr(
            "akshare-main/docs/data/macro/cpi.md",
            "# 居民消费价格指数\n\n"
            "使用 `macro_china_cpi` 查询中国 CPI 数据，并了解居民消费价格变化。\n",
        )

    corpus = build_document_corpus(payload.getvalue(), _catalog(), source_ref="abc123")

    assert corpus["chunk_count"] == 1
    assert corpus["chunks"][0]["api_names"] == ["macro_china_cpi"]
    assert corpus["chunks"][0]["categories"] == ["macro"]
    assert "/blob/abc123/docs/" in corpus["chunks"][0]["source_url"]
