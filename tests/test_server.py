from __future__ import annotations

import asyncio
import time

import pytest

from akbridge.catalog import ApiFunction, signature_to_schema
from akbridge.reliability import CallExecutor, RetryPolicy
from akbridge.server import create_server, create_sse_app, invoke_api_async


def test_server_can_be_created() -> None:
    assert create_server() is not None


def test_router_and_optional_sse_contracts_can_be_created() -> None:
    server = create_server(mode="router")
    assert server.akbridge_mode == "router"  # type: ignore[attr-defined]
    app = create_sse_app(mode="router")
    assert {route.path for route in app.routes} == {"/sse", "/messages"}


def test_async_provider_timeout_returns_without_human_intervention() -> None:
    def slow() -> int:
        time.sleep(0.1)
        return 1

    api = ApiFunction(
        name="slow",
        function=slow,
        description="slow",
        input_schema=signature_to_schema(slow),
        signature="() -> int",
    )

    async def exercise() -> None:
        with pytest.raises(TimeoutError, match="AKBridge call exceeded"):
            await invoke_api_async(
                api,
                {},
                executor=CallExecutor(retry_policy=RetryPolicy(max_attempts=1)),
                row_limit=10,
                call_timeout=0.01,
            )

    asyncio.run(exercise())
