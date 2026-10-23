import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gemini_mcp_agent import _run_with_session, _tool_result_payload


def tool(name="get_relevant_safety_alerts"):
    return SimpleNamespace(
        name=name,
        description="Retrieve evidence.",
        inputSchema={
            "type": "object",
            "properties": {"product_name": {"type": "string"}},
            "required": ["product_name"],
        },
    )


def completion(message):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message)]
    )


def assistant_text(text):
    return SimpleNamespace(content=text, tool_calls=None)


def assistant_tool_call(
    name="get_relevant_safety_alerts",
    arguments='{"product_name":"Example medicine"}',
    call_id="call-1",
):
    return SimpleNamespace(
        content=None,
        tool_calls=[
            SimpleNamespace(
                id=call_id,
                function=SimpleNamespace(name=name, arguments=arguments),
            )
        ],
    )


def make_session(tools=None, result=None, error=None):
    session = MagicMock()
    session.list_tools = AsyncMock(
        return_value=SimpleNamespace(tools=tools or [])
    )
    if error:
        session.call_tool = AsyncMock(side_effect=error)
    else:
        session.call_tool = AsyncMock(
            return_value=result or SimpleNamespace(isError=False, content=[])
        )
    return session


def make_client(*messages):
    client = MagicMock()
    client.chat.completions.create.side_effect = [
        completion(message) for message in messages
    ]
    return client


def test_tool_result_parses_json_content():
    result = SimpleNamespace(
        isError=False,
        content=[SimpleNamespace(text='{"status":"not_found","record":null}')],
    )
    payload = _tool_result_payload(result)
    assert payload["tool_error"] is False
    assert payload["content"][0]["status"] == "not_found"


def test_tool_error_is_not_negative_evidence():
    payload = _tool_result_payload(
        SimpleNamespace(isError=True, content=[])
    )
    assert payload["tool_error"] is True
    assert "error" in payload["message"].lower()


def test_empty_query_does_not_call_provider():
    session, client = MagicMock(), MagicMock()
    answer = asyncio.run(_run_with_session(session, client, "  "))
    assert "provide a question" in answer.lower()
    session.list_tools.assert_not_called()
    client.chat.completions.create.assert_not_called()


def test_invalid_round_limit_is_rejected():
    with pytest.raises(ValueError):
        asyncio.run(
            _run_with_session(
                MagicMock(), MagicMock(), "Check medicine", max_tool_rounds=0
            )
        )


def test_missing_tools_returns_safe_message():
    session = make_session()
    answer = asyncio.run(
        _run_with_session(session, MagicMock(), "Check medicine")
    )
    assert "no evidence tools" in answer.lower()


def test_groq_tool_call_uses_mcp_and_records_trace():
    session = make_session(
        [tool()],
        SimpleNamespace(
            isError=False,
            content=[
                SimpleNamespace(
                    text='{"status":"no_matches_found","alerts":[]}'
                )
            ],
        ),
    )
    client = make_client(
        assistant_tool_call(),
        assistant_text("No matching alert was found in this snapshot."),
    )
    trace = {
        "tools_called": [],
        "tool_call_count": 0,
        "tool_errors": 0,
    }

    answer = asyncio.run(
        _run_with_session(
            session, client, "Check alerts for Example medicine", trace=trace
        )
    )

    session.call_tool.assert_awaited_once_with(
        "get_relevant_safety_alerts", {"product_name": "Example medicine"}
    )
    assert trace == {
        "tools_called": ["get_relevant_safety_alerts"],
        "tool_call_count": 1,
        "tool_errors": 0,
        "tool_errors_by_name": [],
    }
    assert "No matching alert" in answer
    assert client.chat.completions.create.call_count == 2
    first_call = client.chat.completions.create.call_args_list[0].kwargs
    assert first_call["tool_choice"] == "auto"
    assert first_call["tools"][0]["function"]["name"] == (
        "get_relevant_safety_alerts"
    )


def test_tool_call_loop_is_bounded():
    session = make_session(
        [tool("search_greenbook_by_nrn")]
    )
    client = make_client(
        assistant_tool_call(
            "search_greenbook_by_nrn",
            '{"product_name":"Example"}',
        ),
        assistant_tool_call(
            "search_greenbook_by_nrn",
            '{"product_name":"Example"}',
            "call-2",
        ),
    )
    answer = asyncio.run(
        _run_with_session(
            session, client, "Look up this medicine", max_tool_rounds=1
        )
    )
    assert "tool-call limit" in answer.lower()
    assert client.chat.completions.create.call_count == 2
    assert session.call_tool.await_count == 1


def test_mcp_error_result_is_traced():
    session = make_session(
        [tool()],
        SimpleNamespace(isError=True, content=[]),
    )
    client = make_client(
        assistant_tool_call(),
        assistant_text("The evidence tool failed; no conclusion can be drawn."),
    )
    trace = {"tools_called": [], "tool_call_count": 0, "tool_errors": 0}

    answer = asyncio.run(
        _run_with_session(session, client, "Check alerts", trace=trace)
    )
    assert trace["tool_errors"] == 1
    assert trace["tool_errors_by_name"] == ["get_relevant_safety_alerts"]
    assert "no conclusion" in answer.lower()


def test_mcp_exception_is_traced():
    session = make_session([tool()], error=RuntimeError("private error"))
    client = make_client(
        assistant_tool_call(),
        assistant_text("The registry lookup failed; the result is unknown."),
    )
    trace = {"tools_called": [], "tool_call_count": 0, "tool_errors": 0}

    answer = asyncio.run(
        _run_with_session(session, client, "Check registry", trace=trace)
    )
    assert trace["tool_errors"] == 1
    assert trace["tool_errors_by_name"] == ["get_relevant_safety_alerts"]
    assert "unknown" in answer.lower()


def test_unregistered_tool_is_rejected_and_traced():
    session = make_session([tool("search_greenbook_by_nrn")])
    client = make_client(
        assistant_tool_call("delete_registry_records"),
        assistant_text("That requested tool is unavailable."),
    )
    trace = {"tools_called": [], "tool_call_count": 0, "tool_errors": 0}

    answer = asyncio.run(
        _run_with_session(session, client, "Check medicine", trace=trace)
    )
    session.call_tool.assert_not_awaited()
    assert trace["tool_call_count"] == 1
    assert trace["tool_errors"] == 1
    assert trace["tool_errors_by_name"] == ["delete_registry_records"]
    assert "unavailable" in answer.lower()


def test_malformed_tool_arguments_are_rejected():
    session = make_session([tool()])
    client = make_client(
        assistant_tool_call(arguments="{bad json"),
        assistant_text("Tool arguments were invalid."),
    )
    trace = {"tools_called": [], "tool_call_count": 0, "tool_errors": 0}

    asyncio.run(
        _run_with_session(session, client, "Check medicine", trace=trace)
    )
    session.call_tool.assert_not_awaited()
    assert trace["tool_errors"] == 1


def test_blank_final_answer_uses_fallback():
    session = make_session([tool()])
    client = make_client(assistant_text("  "))
    answer = asyncio.run(
        _run_with_session(session, client, "Check medicine")
    )
    assert "could not produce a usable response" in answer.lower()
