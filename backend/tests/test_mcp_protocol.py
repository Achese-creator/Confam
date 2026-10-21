"""End-to-end MCP stdio protocol test using the real server process."""

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


async def exercise_mcp_protocol():
    backend_dir = Path(__file__).resolve().parents[1]

    server = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.mcp_server"],
        cwd=str(backend_dir),
        env=None,
    )

    async with stdio_client(server) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            listed = await session.list_tools()
            tool_names = {tool.name for tool in listed.tools}

            assert {
                "search_greenbook_by_nrn",
                "search_greenbook_by_product",
                "get_relevant_safety_alerts",
            }.issubset(tool_names)

            result = await session.call_tool(
                "get_relevant_safety_alerts",
                {
                    "product_name": "CONFAM protocol test medicine",
                    "nafdac_reg_number": "TEST-NRN",
                    "batch_number": "TEST-BATCH",
                },
            )

            assert not result.isError
            assert result.content
            print("Discovered MCP tools:", sorted(tool_names))
            print("Safety-alert tool invocation: successful")


def test_mcp_stdio_protocol_round_trip():
    asyncio.run(exercise_mcp_protocol())
