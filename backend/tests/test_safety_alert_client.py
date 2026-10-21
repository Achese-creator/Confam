from unittest.mock import patch

from app.safety_alert_client import NAFDACSafetyAlertClient


def _post(content, title="Public Alert No. 001/2026"):
    return {
        "id": 10,
        "title": {"rendered": title},
        "content": {"rendered": content},
        "link": "https://nafdac.gov.ng/public-alert-test/",
        "date": "2026-08-19T08:42:18",
        "modified": "2026-08-20T08:42:18",
    }


def test_live_client_requires_product_name():
    result = NAFDACSafetyAlertClient().lookup("")
    assert result.status == "live_unavailable"
    assert result.records == []


def test_live_notice_with_exact_product_and_batch_returns_record():
    client = NAFDACSafetyAlertClient()
    with (
        patch.object(client, "_search", return_value=[
            {"id": 10, "title": "Public Alert", "url": "https://nafdac.gov.ng/a/"}
        ]),
        patch.object(
            client,
            "_fetch_post",
            return_value=_post(
                "<p>Example Medicine is affected.</p>"
                "<p>Batch Number: ABC123</p>"
            ),
        ),
    ):
        result = client.lookup("Example Medicine", batch_number="ABC123")

    assert result.status == "live_matches_found"
    assert result.records[0]["batch_numbers"] == ["ABC123"]
    assert result.records[0]["source_url"] == "https://nafdac.gov.ng/public-alert-test/"
    assert result.records[0]["dataset_label"] == "live_source_record"


def test_broad_entity_notice_is_not_a_confirmed_batch_record():
    client = NAFDACSafetyAlertClient()
    with (
        patch.object(client, "_search", return_value=[
            {"id": 10, "title": "Public Alert", "url": "https://nafdac.gov.ng/a/"}
        ]),
        patch.object(
            client,
            "_fetch_post",
            return_value=_post(
                "<p>Products marketed by Example Company are under investigation.</p>"
            ),
        ),
    ):
        result = client.lookup("Example Medicine", batch_number="ABC123")

    assert result.status == "live_no_matches"
    assert result.records == []
    assert len(result.notices) == 1


def test_live_source_failure_is_not_reported_as_no_matches():
    client = NAFDACSafetyAlertClient()
    with patch.object(client, "_search", side_effect=TimeoutError()):
        result = client.lookup("Example Medicine")

    assert result.status == "live_unavailable"
    assert result.records == []
