"""Tests for CONFAM's read-only MCP evidence tools."""

from unittest.mock import patch

from app import mcp_server


def test_exact_nrn_tool_preserves_lookup_status():
    with patch(
        'app.mcp_server.GreenbookClient',
    ) as client_class:
        client_class.return_value.lookup.return_value.status = "not_found"
        client_class.return_value.lookup.return_value.message = "No exact match."
        client_class.return_value.lookup.return_value.record = None

        result = mcp_server.search_greenbook_by_nrn("TEST-NRN")

    assert result["status"] == "not_found"
    assert result["record"] is None
    assert "not proof" in result["interpretation"].lower()


def test_product_search_caps_candidate_limit():
    with patch(
        'app.mcp_server.GreenbookClient',
    ) as client_class:
        client_class.return_value.search_by_product_name.return_value.status = (
            "not_found"
        )
        client_class.return_value.search_by_product_name.return_value.message = (
            "No candidates."
        )
        client_class.return_value.search_by_product_name.return_value.candidates = []

        result = mcp_server.search_greenbook_by_product(
            "Example medicine",
            limit=100,
        )

    client_class.return_value.search_by_product_name.assert_called_once_with(
        "Example medicine",
        limit=10,
    )
    assert result["candidates"] == []


def test_alert_tool_reports_no_matches_without_claiming_safety():
    with patch.object(mcp_server, "find_relevant_alerts", return_value=[]) as finder:
        result = mcp_server.get_relevant_safety_alerts(
            product_name="Example medicine",
            nafdac_reg_number="TEST-NRN",
            batch_number="BATCH-1",
        )

    finder.assert_called_once()
    assert result["status"] == "no_matches_found"
    assert result["alert_count"] == 0
    assert "does not establish safety" in result["interpretation"]


def test_live_only_alert_sets_overall_match_status():
    live_record = {
        "title": "Example NAFDAC alert",
        "url": "https://nafdac.gov.ng/example-alert",
    }
    live_result = type(
        "LiveResult",
        (),
        {
            "status": "matches_found",
            "records": [live_record],
            "message": "One live notice matched.",
        },
    )()

    with (
        patch.object(mcp_server, "find_relevant_alerts", return_value=[]),
        patch.object(mcp_server, "NAFDACSafetyAlertClient") as client_class,
    ):
        client_class.return_value.lookup.return_value = live_result
        result = mcp_server.get_relevant_safety_alerts(
            product_name="Example medicine",
        )

    assert result["status"] == "matches_found"
    assert result["live_status"] == "matches_found"
    assert result["live_alert_count"] == 1
    assert result["curated_status"] == "no_matches_found"
    assert result["curated_alert_count"] == 0


def test_alert_tool_returns_serialized_evidence():
    fake_alert = type(
        "FakeAlert",
        (),
        {"model_dump": lambda self, **kwargs: {"alert_id": "ALERT-1"}},
    )()

    with patch.object(
        mcp_server,
        "find_relevant_alerts",
        return_value=[fake_alert],
    ):
        result = mcp_server.get_relevant_safety_alerts(
            product_name="Example medicine",
        )

    assert result["status"] == "matches_found"
    assert result["alert_count"] == 1
    assert result["alerts"] == [{"alert_id": "ALERT-1"}]
