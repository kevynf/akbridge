"""Isolated worker for one AKShare interface acceptance call."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import sys
from typing import Any

from .catalog import coerce_arguments, discover_functions
from .reliability import redact_secrets
from .serialization import to_jsonable


def _is_empty(value: Any) -> bool:
    if isinstance(value, dict) and value.get("type") in {"dataframe", "series"}:
        return value.get("row_count") == 0
    return value is None or value == [] or value == {}


def execute(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    started = dt.datetime.now(dt.UTC)
    api = discover_functions().get(name)
    if api is None:
        return {"name": name, "status": "worker_failed", "error": "API not found"}
    stdout = io.StringIO()
    stderr = io.StringIO()
    try:
        call_arguments = coerce_arguments(api.function, arguments)
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            value = api.function(**call_arguments)
        preview = redact_secrets(to_jsonable(value, row_limit=3), key_hint=name)
        status = "passed_empty" if _is_empty(preview) else "passed"
        error = None
    except Exception as exc:  # External providers fail independently.
        preview = None
        status = "failed"
        error = redact_secrets(f"{type(exc).__name__}: {exc}"[:4000], key_hint=name)
    return {
        "name": name,
        "status": status,
        "duration_seconds": (dt.datetime.now(dt.UTC) - started).total_seconds(),
        "arguments": redact_secrets(arguments),
        "error": error,
        "worker_logs": redact_secrets((stdout.getvalue() + stderr.getvalue())[-2000:]) or None,
        "preview": preview,
    }


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("usage: acceptance_worker API_NAME ARGUMENTS_JSON")
    sys.stdout.reconfigure(encoding="utf-8")
    result = execute(sys.argv[1], json.loads(sys.argv[2]))
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
