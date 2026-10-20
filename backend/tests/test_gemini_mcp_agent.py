"""Tests for the bounded Gemini function-calling loop."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gemini_mcp_agent import (
    _run_with_session,
    _tool_result_payload,
)


def test_tool_result_parses_json_content():
    result = SimpleNamespace(
        isError=False,
        content=[
            SimpleNamespace(
                text='{"status":"not_found","record":null}'
            )
        ],
    )

    payload = _tool_result_payload(result)

    assert payload["tool_error"] is False
    assert payload["content"][0]["status"] == "not_found"


def test_tool_error_is_not_misrepresented_as_negative_evidence():
    result = SimpleNamespace(isError=True, content=[])

    payload = _tool_result_payload(result)

    assert payload["tool_error"] is True
    assert "error" in payload["message"].lower()


def test_empty_query_does_not_call_gemini():
    session = MagicMock()
    client = MagicMock()

    answer = asyncio.run(
        _run_with_session(session, client, "   ")
    )

    assert "provide a question" in answer.lower()
    session.list_tools.assert_not_called()
    client.models.generate_content.assert_not_called()


def test_invalid_round_limit_is_rejected():
    with pytest.raises(ValueError):
        asyncio.run(
            _run_with_session(
                MagicMock(),
                MagicMock(),
                "Check this medicine",
                max_tool_rounds=0,
            )
        )


def test_missing_tools_returns_safe_message():
    session = MagicMock()
    session.list_tools = AsyncMock(
        return_value=SimpleNamespace(tools=[])
    )

    answer = asyncio.run(
        _run_with_session(session, MagicMock(), "Check this medicine")
    )

    assert "no evidence tools" in answer.lower()


def test_gemini_tool_call_uses_mcp_session():
    from google.genai import types

    session = MagicMock()
    session.list_tools = AsyncMock(
        return_value=SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="get_relevant_safety_alerts",
                    description="Retrieve relevant safety alerts.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "product_name": {"type": "string"}
                        },
                        "required": ["product_name"],
                    },
                )
            ]
        )
    )
    session.call_tool = AsyncMock(
        return_value=SimpleNamespace(
            isError=False,
            content=[
                SimpleNamespace(
                    text='{"status":"no_matches_found","alerts":[]}'
                )
            ],
        )
    )

    function_call = types.FunctionCall(
        name="get_relevant_safety_alerts",
        args={"product_name": "Example medicine"},
    )
    function_part = types.Part(function_call=function_call)
    call_content = types.Content(role="model", parts=[function_part])
    final_content = types.Content(
        role="model",
        parts=[types.Part(text="No matching alert was found in this snapshot.")],
    )

    client = MagicMock()
    client.models.generate_content.side_effect = [
        SimpleNamespace(
            candidates=[SimpleNamespace(content=call_content)]
        ),
        SimpleNamespace(
            candidates=[SimpleNamespace(content=final_content)]
        ),
    ]

    trace = {
        "tools_called": [],
        "tool_call_count": 0,
        "tool_errors": 0,
    }
    answer = asyncio.run(
        _run_with_session(
            session,
            client,
            "Check alerts for Example medicine",
            trace=trace,
        )
    )

    session.call_tool.assert_awaited_once_with(
        "get_relevant_safety_alerts",
        {"product_name": "Example medicine"},
    )
    assert trace == {
        "tools_called": ["get_relevant_safety_alerts"],
        "tool_call_count": 1,
        "tool_errors": 0,
    }
    assert "No matching alert" in answer
    assert client.models.generate_content.call_count == 2


def test_tool_call_loop_is_bounded():
    from google.genai import types

    session = MagicMock()
    session.list_tools = AsyncMock(
        return_value=SimpleNamespace(
            tools=[
                SimpleNamespace(
                    name="search_greenbook_by_nrn",
                    description="Exact lookup.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "registration_number": {"type": "string"}
                        },
                        "required": ["registration_number"],
                    },
                )
            ]
        )
    )
    session.call_tool = AsyncMock(
        return_value=SimpleNamespace(isError=False, content=[])
    )

    call_content = types.Content(
        role="model",
        parts=[
            types.Part(
                function_call=types.FunctionCall(
                    name="search_greenbook_by_nrn",
                    args={"registration_number": "TEST-NRN"},
                )
            )
        ],
    )
    client = MagicMock()
    client.models.generate_content.return_value = SimpleNamespace(
        candidates=[SimpleNamespace(content=call_content)]
    )

    answer = asyncio.run(
        _run_with_session(
            session,
            client,
            "Look up this NRN",
            max_tool_rounds=1,
        )
    )

    assert "tool-call limit" in answer.lower()
    assert client.models.generate_content.call_count == 2
    assert session.call_tool.await_count == 1
