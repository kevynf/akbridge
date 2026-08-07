"""AKBridge MCP server with full-catalog and model-friendly router modes."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import Mapping
from typing import Any

import mcp.server.stdio
from mcp.server import Server
from mcp.server.lowlevel import NotificationOptions
from mcp.server.models import InitializationOptions
from mcp.types import Resource, TextContent, Tool, ToolAnnotations

from . import __version__
from .catalog import ApiFunction, coerce_arguments, discover_functions
from .observability import configure_logging
from .reliability import (
    CallExecutor,
    RateLimiter,
    RetryPolicy,
    TTLCache,
    proxy_environment,
    redact_secrets,
)
from .router import CatalogIndex, router_tool_definitions
from .serialization import to_jsonable
from .skill import SKILL_TEXT, SKILL_URI

VALID_MODES = {"all", "router", "hybrid"}
METRICS_URI = "akbridge://metrics"


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _apply_proxy_aliases() -> None:
    # AKBRIDGE_* variables are explicit opt-in aliases and never overwrite a
    # conventional proxy already supplied by the parent process.
    environ = proxy_environment()
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        if environ.get(key) and not os.environ.get(key):
            os.environ[key] = environ[key]


def _default_executor() -> CallExecutor:
    cache_ttl = max(0.0, _env_float("AKBRIDGE_CACHE_TTL", 0.0))
    cache = (
        TTLCache(
            maxsize=max(1, _env_int("AKBRIDGE_CACHE_SIZE", 256)),
            ttl=cache_ttl,
        )
        if cache_ttl > 0
        else None
    )
    return CallExecutor(
        retry_policy=RetryPolicy.from_env(),
        cache=cache,
        rate_limiter=RateLimiter(max(0.0, _env_float("AKBRIDGE_RATE_LIMIT_SECONDS", 0.0))),
        failure_threshold=max(1, _env_int("AKBRIDGE_CIRCUIT_FAILURE_THRESHOLD", 5)),
        recovery_timeout=max(0.0, _env_float("AKBRIDGE_CIRCUIT_RECOVERY_SECONDS", 30.0)),
        logger=configure_logging(),
    )


def _json_content(payload: Any) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, default=str))]


def build_mcp_tools(catalog: Mapping[str, ApiFunction], *, mode: str) -> list[Tool]:
    """Build the exact MCP tool list without starting a transport.

    Maintenance uses this pure construction step to validate every generated
    input schema offline before a client or provider is involved.
    """
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}; choose all, router, or hybrid")
    tools: list[Tool] = []
    if mode in {"router", "hybrid"}:
        tools.extend(
            Tool(
                name=item["name"],
                description=item["description"],
                inputSchema=item["inputSchema"],
                annotations=ToolAnnotations(
                    readOnlyHint=item["name"] != "akbridge_call",
                    destructiveHint=False,
                    idempotentHint=item["name"] != "akbridge_call",
                    openWorldHint=item["name"] == "akbridge_call",
                ),
            )
            for item in router_tool_definitions()
        )
    if mode in {"all", "hybrid"}:
        tools.extend(
            Tool(
                name=api.name,
                description=api.description,
                inputSchema=api.input_schema,
                annotations=ToolAnnotations(
                    title=api.display_name or api.name,
                    readOnlyHint=not api.side_effect,
                    destructiveHint=api.side_effect,
                    idempotentHint=not api.side_effect,
                    openWorldHint=True,
                ),
            )
            for api in catalog.values()
        )
    return tools


def invoke_api(
    api: ApiFunction,
    arguments: Mapping[str, Any] | None,
    *,
    executor: CallExecutor,
    row_limit: int,
    output_mode: str = "raw",
    page: int = 1,
    page_size: int | None = None,
    include_metadata: bool = False,
) -> Any:
    """Coerce, execute and serialize one catalog entry.

    This synchronous helper is public so acceptance and offline tests can
    exercise exactly the same path as the MCP handler without a client or LLM.
    """
    call_arguments = coerce_arguments(api.function, dict(arguments or {}))
    value = executor.call(api.name, api.function, call_arguments, side_effect=api.side_effect)
    payload = to_jsonable(
        value,
        row_limit=row_limit,
        mode=output_mode,
        page=page,
        page_size=page_size,
        include_metadata=include_metadata,
    )
    # Credential helper APIs must not echo a configured token over MCP.
    return redact_secrets(payload, key_hint=api.name)


async def invoke_api_async(
    api: ApiFunction,
    arguments: Mapping[str, Any] | None,
    *,
    executor: CallExecutor,
    row_limit: int,
    output_mode: str = "raw",
    page: int = 1,
    page_size: int | None = None,
    include_metadata: bool = False,
    call_timeout: float | None = None,
) -> Any:
    """Async MCP boundary with a response timeout around the sync provider call."""
    coroutine = asyncio.to_thread(
        invoke_api,
        api,
        arguments,
        executor=executor,
        row_limit=row_limit,
        output_mode=output_mode,
        page=page,
        page_size=page_size,
        include_metadata=include_metadata,
    )
    if call_timeout is None or call_timeout <= 0:
        return await coroutine
    try:
        return await asyncio.wait_for(coroutine, timeout=call_timeout)
    except TimeoutError as exc:
        raise TimeoutError(f"AKBridge call exceeded {call_timeout}s: {api.name}") from exc


def create_server(
    *,
    row_limit: int = 5000,
    mode: str | None = None,
    output_mode: str | None = None,
    catalog: dict[str, ApiFunction] | None = None,
    executor: CallExecutor | None = None,
    call_timeout: float | None = None,
) -> Server:
    """Create an MCP server.

    ``all`` preserves the original one-tool-per-AKShare-function surface.
    ``router`` exposes only three stable tools that search and call the local
    semantic catalog.  ``hybrid`` publishes both surfaces for migration and
    debugging.
    """
    if row_limit < 1:
        raise ValueError("row_limit must be at least 1")
    selected_mode = (mode or os.getenv("AKBRIDGE_MODE", "all")).casefold()
    if selected_mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {selected_mode}; choose all, router, or hybrid")
    _apply_proxy_aliases()
    catalog = discover_functions() if catalog is None else catalog
    index = CatalogIndex(catalog)
    call_executor = executor or _default_executor()
    default_output = output_mode or os.getenv(
        "AKBRIDGE_OUTPUT_MODE", "compact" if selected_mode == "router" else "raw"
    )
    if default_output not in {"raw", "compact", "summary"}:
        raise ValueError("output_mode must be raw, compact, or summary")
    effective_timeout = (
        call_timeout if call_timeout is not None else _env_float("AKBRIDGE_CALL_TIMEOUT", 120.0)
    )
    effective_timeout = effective_timeout if effective_timeout and effective_timeout > 0 else None
    server = Server("akbridge")

    # Expose observability and catalog handles for embedders without adding
    # them to the MCP wire protocol.
    server.akbridge_catalog = catalog  # type: ignore[attr-defined]
    server.akbridge_index = index  # type: ignore[attr-defined]
    server.akbridge_executor = call_executor  # type: ignore[attr-defined]
    server.akbridge_mode = selected_mode  # type: ignore[attr-defined]
    server.akbridge_call_timeout = effective_timeout  # type: ignore[attr-defined]

    async def invoke_in_thread(
        api: ApiFunction,
        call_arguments: Mapping[str, Any] | None,
        *,
        mode_value: str,
        page: int = 1,
        page_size: int | None = None,
        include_metadata: bool = False,
    ) -> Any:
        return await invoke_api_async(
            api,
            call_arguments,
            executor=call_executor,
            row_limit=row_limit,
            output_mode=mode_value,
            page=page,
            page_size=page_size,
            include_metadata=include_metadata,
            call_timeout=effective_timeout,
        )

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return build_mcp_tools(catalog, mode=selected_mode)

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        supplied = dict(arguments or {})
        if name == "akbridge_search":
            payload = index.search_payload(
                supplied.get("query", ""),
                category=supplied.get("category"),
                limit=supplied.get("limit", 8),
            )
            return _json_content(payload)
        if name == "akbridge_describe":
            requested = supplied.get("name") or supplied.get("api")
            if not requested:
                raise ValueError("akbridge_describe requires name")
            return _json_content(
                index.describe(requested, include_schema=supplied.get("include_schema", True))
            )
        if name == "akbridge_call":
            requested = supplied.get("name") or supplied.get("api")
            if not requested:
                raise ValueError("akbridge_call requires name")
            api = index.resolve(requested)
            if api is None:
                suggestions = ", ".join(item["name"] for item in index.suggestions(str(requested)))
                suffix = f"; suggestions: {suggestions}" if suggestions else ""
                raise ValueError(f"Unknown AKShare API: {requested}{suffix}")
            mode_value = supplied.get("output_mode", default_output)
            result = await invoke_in_thread(
                api,
                supplied.get("arguments", {}),
                mode_value=mode_value,
                page=int(supplied.get("page", 1)),
                page_size=(
                    int(supplied["page_size"]) if supplied.get("page_size") is not None else None
                ),
                include_metadata=bool(supplied.get("include_metadata", True)),
            )
            return _json_content(
                {
                    "name": api.name,
                    "requested_name": requested,
                    "output_mode": mode_value,
                    "result": result,
                    "metrics": call_executor.metrics.snapshot(),
                }
            )

        api: ApiFunction | None = catalog.get(name)
        if api is None:
            raise ValueError(f"Unknown AKShare API: {name}")
        result = await invoke_in_thread(
            api,
            supplied,
            mode_value=default_output,
        )
        return _json_content(result)

    @server.list_resources()
    async def list_resources() -> list[Resource]:
        return [
            Resource(
                uri=SKILL_URI,
                name="akbridge-skill",
                description="Deterministic instructions for searching and calling AKBridge.",
                mimeType="text/markdown",
            ),
            Resource(
                uri=METRICS_URI,
                name="akbridge-metrics",
                description="Process-local AKBridge call counters and durations.",
                mimeType="application/json",
            ),
        ]

    @server.read_resource()
    async def read_resource(uri: Any) -> str:
        if str(uri) != SKILL_URI:
            if str(uri) == METRICS_URI:
                return json.dumps(call_executor.metrics.snapshot(), ensure_ascii=False)
            raise ValueError(f"Unknown AKBridge resource: {uri}")
        return SKILL_TEXT

    return server


async def run_stdio(*, row_limit: int, mode: str = "all", output_mode: str | None = None) -> None:
    server = create_server(row_limit=row_limit, mode=mode, output_mode=output_mode)
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="akbridge",
                server_version=__version__,
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


def create_sse_app(
    *,
    row_limit: int = 5000,
    mode: str | None = None,
    output_mode: str | None = None,
) -> Any:
    """Build an optional Starlette SSE app without starting a listener.

    Starlette/uvicorn remain optional at import time; callers that select SSE
    receive a clear dependency error instead of breaking stdio deployments.
    """
    try:
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.responses import Response
        from starlette.routing import Mount, Route
    except ImportError as exc:  # pragma: no cover - depends on installation extras
        raise RuntimeError("SSE transport requires starlette and mcp SSE dependencies") from exc

    server = create_server(row_limit=row_limit, mode=mode, output_mode=output_mode)
    sse_path = os.getenv("AKBRIDGE_SSE_PATH", "/sse")
    message_path = os.getenv("AKBRIDGE_MESSAGE_PATH", "/messages/")
    transport = SseServerTransport(message_path)

    async def handle_sse(request: Any) -> Response:
        async with transport.connect_sse(request.scope, request.receive, request._send) as streams:
            await server.run(
                streams[0],
                streams[1],
                server.create_initialization_options(),
            )
        return Response()

    return Starlette(
        routes=[
            Route(sse_path, endpoint=handle_sse, methods=["GET"]),
            Mount(message_path, app=transport.handle_post_message),
        ]
    )


async def run_sse(
    *,
    row_limit: int,
    mode: str = "all",
    output_mode: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run the optional HTTP/SSE transport."""
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - depends on installation extras
        raise RuntimeError("SSE transport requires uvicorn") from exc
    app = create_sse_app(row_limit=row_limit, mode=mode, output_mode=output_mode)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    await uvicorn.Server(config).serve()


def main() -> None:
    parser = argparse.ArgumentParser(description="Expose AKShare over MCP")
    parser.add_argument("--row-limit", type=int, default=5000)
    parser.add_argument(
        "--mode", choices=sorted(VALID_MODES), default=os.getenv("AKBRIDGE_MODE", "all")
    )
    parser.add_argument("--output-mode", choices=["raw", "compact", "summary"])
    parser.add_argument(
        "--transport", choices=["stdio", "sse"], default=os.getenv("AKBRIDGE_TRANSPORT", "stdio")
    )
    parser.add_argument("--host", default=os.getenv("AKBRIDGE_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=_env_int("AKBRIDGE_PORT", 8000))
    parser.add_argument(
        "--call-timeout", type=float, default=_env_float("AKBRIDGE_CALL_TIMEOUT", 120.0)
    )
    args = parser.parse_args()
    if args.transport == "sse":
        # The SSE constructor reads the same environment defaults.  Preserve
        # the explicit CLI override for the process it launches.
        os.environ["AKBRIDGE_CALL_TIMEOUT"] = str(args.call_timeout)
        asyncio.run(
            run_sse(
                row_limit=args.row_limit,
                mode=args.mode,
                output_mode=args.output_mode,
                host=args.host,
                port=args.port,
            )
        )
    else:
        os.environ["AKBRIDGE_CALL_TIMEOUT"] = str(args.call_timeout)
        asyncio.run(
            run_stdio(row_limit=args.row_limit, mode=args.mode, output_mode=args.output_mode)
        )


if __name__ == "__main__":
    main()
