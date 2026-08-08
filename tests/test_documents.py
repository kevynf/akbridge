from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from akbridge.catalog import ApiFunction, signature_to_schema
from akbridge.documents import build_document_corpus, documentation_coverage, load_document_chunks


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


def test_document_index_rejects_an_akshare_version_mismatch(tmp_path: Path) -> None:
    path = tmp_path / "docs.json"
    path.write_text(
        json.dumps({"schema_version": 1, "akshare_version": "1.0.0", "chunks": []}),
        encoding="utf-8",
    )

    assert load_document_chunks(path, expected_version="1.0.0") == []
    with pytest.raises(ValueError, match="does not match"):
        load_document_chunks(path, expected_version="2.0.0")


def test_documentation_coverage_reports_missing_public_interfaces() -> None:
    coverage = documentation_coverage(
        _catalog(),
        [
            {
                "id": "cpi",
                "title": "CPI",
                "path": "data/macro/cpi.md",
                "source_url": "https://example.test/cpi",
                "text": "macro_china_cpi",
                "api_names": ["macro_china_cpi"],
                "categories": ["macro"],
            }
        ],
    )

    assert coverage["status"] == "ok"
    assert coverage["chunk_field_coverage_percent"] == 100.0
    assert coverage["interface_coverage_percent"] == 100.0
