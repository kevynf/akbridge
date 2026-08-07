from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from akbridge.catalog import discover_functions


def test_stdio_client_lists_all_discovered_tools() -> None:
    async def exercise() -> None:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "akbridge.server"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=environment,
            encoding="utf-8",
        )
        async with stdio_client(parameters) as streams, ClientSession(*streams) as session:
            await session.initialize()
            result = await session.list_tools()
            fixture_path = (
                Path(__file__).resolve().parents[1] / "artifacts/acceptance/fixtures.json"
            )
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))["volatility_yz_rv"]
            call_result = await session.call_tool("volatility_yz_rv", fixture)

        names = {tool.name for tool in result.tools}
        assert len(names) == len(discover_functions())
        assert "stock_zh_a_hist" in names
        assert not call_result.isError
        payload = json.loads(call_result.content[0].text)
        assert payload["type"] == "dataframe"
        assert payload["row_count"] > 0

    asyncio.run(exercise())


def test_stdio_router_mode_is_small_and_calls_local_fixture() -> None:
    async def exercise() -> None:
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        environment["PYTHONUTF8"] = "1"
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "akbridge.server", "--mode", "router"],
            cwd=str(Path(__file__).resolve().parents[1]),
            env=environment,
            encoding="utf-8",
        )
        async with stdio_client(parameters) as streams, ClientSession(*streams) as session:
            await session.initialize()
            tools = await session.list_tools()
            assert {tool.name for tool in tools.tools} == {
                "akbridge_search",
                "akbridge_describe",
                "akbridge_call",
            }
            search = await session.call_tool(
                "akbridge_search", {"query": "stock_zh_a_hist", "limit": 1}
            )
            assert not search.isError
            search_payload = json.loads(search.content[0].text)
            assert search_payload["results"][0]["name"] == "stock_zh_a_hist"

            fixture_path = (
                Path(__file__).resolve().parents[1] / "artifacts/acceptance/fixtures.json"
            )
            fixture = json.loads(fixture_path.read_text(encoding="utf-8"))["volatility_yz_rv"]
            call_result = await session.call_tool(
                "akbridge_call",
                {
                    "name": "volatility_yz_rv",
                    "arguments": fixture,
                    "output_mode": "summary",
                },
            )
            assert not call_result.isError
            payload = json.loads(call_result.content[0].text)
            assert payload["result"]["type"] == "dataframe_summary"

            resources = await session.list_resources()
            assert any(str(resource.uri) == "akbridge://skill" for resource in resources.resources)
            assert any(
                str(resource.uri) == "akbridge://metrics" for resource in resources.resources
            )
            metrics = await session.read_resource("akbridge://metrics")
            assert json.loads(metrics.contents[0].text)["calls"] >= 1

    asyncio.run(exercise())
