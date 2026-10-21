from fastapi.testclient import TestClient

from app import main


client = TestClient(main.app)


def test_agent_endpoint_returns_answer(monkeypatch):
    def fake_agent(question):
        assert question == "Check this medicine"
        return {
            "answer": "Review the registry evidence and source records.",
            "tools_called": ["search_greenbook_by_nrn"],
            "tool_call_count": 1,
            "tool_errors": 0,
        }

    monkeypatch.setattr(main, "run_gemini_mcp_agent_with_trace", fake_agent)

    response = client.post(
        "/api/v1/agent/ask",
        json={"question": "  Check this medicine  "},
    )

    assert response.status_code == 200
    assert response.json() == {
        "answer": "Review the registry evidence and source records.",
        "tools_called": ["search_greenbook_by_nrn"],
        "tool_call_count": 1,
        "tool_errors": 0,
        "tool_errors_by_name": [],
    }


def test_agent_endpoint_rejects_empty_question():
    response = client.post(
        "/api/v1/agent/ask",
        json={"question": "   "},
    )

    assert response.status_code == 422


def test_agent_endpoint_requires_question():
    response = client.post("/api/v1/agent/ask", json={})

    assert response.status_code == 422
