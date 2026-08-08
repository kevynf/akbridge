"""Build and load a local lexical corpus from the AKShare documentation tree."""

from __future__ import annotations

import argparse
import importlib.resources
import io
import json
import re
import urllib.request
import zipfile
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import akshare

from .catalog import ApiFunction, discover_functions

GITHUB_ARCHIVE_TEMPLATE = "https://github.com/akfamily/akshare/archive/{ref}.zip"
GITHUB_DOCS_ARCHIVE = GITHUB_ARCHIVE_TEMPLATE.format(ref="main")
GITHUB_REPOSITORY = "https://github.com/akfamily/akshare"
MAX_APIS_PER_CHUNK = 12
BUILTIN_INDEX_PACKAGE = "akbridge.data"
BUILTIN_INDEX_NAME = "akshare-docs.json"
REQUIRED_CHUNK_FIELDS = ("id", "title", "path", "source_url", "text", "api_names", "categories")
_HEADING_RE = re.compile(r"^(.+?)\n[=-]{3,}\s*$|^#{1,6}\s+(.+?)\s*$", re.MULTILINE)


def _sections(text: str) -> Iterable[tuple[str, str]]:
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        yield "", text
        return
    if matches[0].start():
        yield "", text[: matches[0].start()]
    for index, match in enumerate(matches):
        title = (match.group(1) or match.group(2) or "").strip()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        yield title, text[match.end() : end]


def build_document_corpus(
    archive: bytes,
    catalog: dict[str, ApiFunction],
    *,
    source_ref: str = "main",
    archive_url: str = GITHUB_DOCS_ARCHIVE,
    akshare_version: str | None = None,
) -> dict[str, Any]:
    """Turn an AKShare GitHub archive into small, source-linked text chunks."""
    names = tuple(catalog)
    chunks: list[dict[str, Any]] = []
    with zipfile.ZipFile(io.BytesIO(archive)) as payload:
        paths = sorted(
            path
            for path in payload.namelist()
            if "/docs/" in path and path.endswith((".md", ".rst"))
        )
        for path in paths:
            relative = path.split("/docs/", 1)[1]
            text = payload.read(path).decode("utf-8", errors="replace")
            for ordinal, (title, section) in enumerate(_sections(text), start=1):
                body = section.strip()
                if len(body) < 40:
                    continue
                api_names = [name for name in names if re.search(rf"\b{re.escape(name)}\b", body)]
                if not api_names or len(api_names) > MAX_APIS_PER_CHUNK:
                    continue
                categories = sorted({catalog[name].category for name in api_names})
                chunks.append(
                    {
                        "id": f"{relative}:{ordinal}",
                        "title": title or relative,
                        "path": relative,
                        "source_url": f"{GITHUB_REPOSITORY}/blob/{source_ref}/docs/{relative}",
                        "text": body[:12000],
                        "api_names": api_names,
                        "categories": categories,
                    }
                )
    return {
        "schema_version": 1,
        "source": {"archive_url": archive_url, "ref": source_ref},
        "akshare_version": akshare_version or getattr(akshare, "__version__", "unknown"),
        "generated_at": datetime.now(UTC).isoformat(),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }


def _validated_chunks(payload: Any, *, expected_version: str | None = None) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("unsupported document index schema")
    actual_version = str(payload.get("akshare_version", "unknown"))
    if expected_version and actual_version != expected_version:
        raise ValueError(
            f"document index AKShare version {actual_version} does not match {expected_version}"
        )
    chunks = payload.get("chunks", [])
    if not isinstance(chunks, list):
        raise ValueError("document index must contain a chunks list")
    return [item for item in chunks if isinstance(item, dict)]


def load_document_chunks(
    path: str | Path, *, expected_version: str | None = None
) -> list[dict[str, Any]]:
    """Load validated chunks emitted by :func:`build_document_corpus`."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return _validated_chunks(payload, expected_version=expected_version)


def load_builtin_document_chunks(*, expected_version: str | None = None) -> list[dict[str, Any]]:
    """Load the index embedded by the package build, or return an empty source-tree fallback."""
    try:
        resource = importlib.resources.files(BUILTIN_INDEX_PACKAGE).joinpath(BUILTIN_INDEX_NAME)
        if not resource.is_file():
            return []
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except ModuleNotFoundError:
        return []
    return _validated_chunks(payload, expected_version=expected_version)


def documentation_coverage(
    catalog: dict[str, ApiFunction], chunks: list[dict[str, Any]]
) -> dict[str, Any]:
    """Summarize document-index field and public-interface coverage for maintenance reports."""
    documented = {
        str(name)
        for chunk in chunks
        for name in chunk.get("api_names", [])
        if isinstance(name, str) and name in catalog
    }
    missing = sorted(set(catalog) - documented)
    complete_chunks = sum(
        all(bool(chunk.get(field)) for field in REQUIRED_CHUNK_FIELDS) for chunk in chunks
    )
    return {
        "status": "ok" if chunks else "absent",
        "chunk_count": len(chunks),
        "complete_chunk_count": complete_chunks,
        "chunk_field_coverage_percent": round(100 * complete_chunks / len(chunks), 2)
        if chunks
        else 0.0,
        "interface_count": len(catalog),
        "documented_interface_count": len(documented),
        "interface_coverage_percent": round(100 * len(documented) / len(catalog), 2)
        if catalog
        else 100.0,
        "undocumented_interface_count": len(missing),
        "undocumented_interfaces": missing,
    }


def _download(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "akbridge-docs/0.1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a local lexical AKShare documentation index"
    )
    parser.add_argument("build", nargs="?", default="build")
    parser.add_argument("--output", type=Path, default=Path("artifacts/akshare-docs.json"))
    parser.add_argument("--archive-url")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--timeout", type=float, default=60.0)
    args = parser.parse_args()
    archive_url = args.archive_url or GITHUB_ARCHIVE_TEMPLATE.format(ref=args.ref)
    corpus = build_document_corpus(
        _download(archive_url, args.timeout),
        discover_functions(),
        source_ref=args.ref,
        archive_url=archive_url,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(corpus, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {"chunks": corpus["chunk_count"], "output": str(args.output)}, ensure_ascii=False
        )
    )


if __name__ == "__main__":
    main()
