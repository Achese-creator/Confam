import json
from unittest.mock import patch

from app.greenbook_client import (
    GreenbookClient,
    GreenbookUnavailable,
    _row_to_record,
    normalize_registration_number,
)


def sample_row(**overrides):
    row = {
        "product_id": 2,
        "product_name": "AG Spironolactone##",
        "NAFDAC": "A4-100160",
        "status": "Active",
        "strength": "25 mg",
        "composition": (
            "Each film coated tablet contains: Spironolactone BP 25 mg"
        ),
        "ingredient_name": "Spironolactone",
        "form_name": "Tablet",
        "applicant_name": "AnnyGod Pharmaceutical Company Ltd",
        "approval_date": "2021-12-17",
        "expiry_date": "2026-12-16",
        "route_name": "Oral",
        "pack_size": "1 x 500's (in a jar)",
    }
    row.update(overrides)
    return row


def fake_response(payload):
    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps(payload).encode("utf-8")

    return Response()


def test_registration_number_normalization_removes_punctuation():
    assert normalize_registration_number("A4-100160") == "a4100160"
    assert normalize_registration_number(" a4 / 100160 ") == "a4100160"


def test_row_mapping_preserves_source_and_does_not_invent_manufacturer():
    record = _row_to_record(sample_row(), "2026-10-19")

    assert record["product_name"] == "AG Spironolactone"
    assert record["nafdac_reg_number"] == "A4-100160"
    assert record["active_ingredients"] == "Spironolactone"
    assert record["dosage_form"] == "Tablet"
    assert record["manufacturer"] is None
    assert record["applicant_name"] == "AnnyGod Pharmaceutical Company Ltd"
    assert record["registration_expiry_date"] == "2026-12-16"
    assert record["dataset_label"] == "live_source_record"
    assert record["source_url"].endswith("/products/details/2")


def test_lookup_requires_an_exact_nr_number_match():
    payload = {"data": [sample_row(NAFDAC="A4-100160-EXTRA")]}
    client = GreenbookClient()

    with patch(
        "app.greenbook_client.urlopen",
        return_value=fake_response(payload),
    ):
        result = client.lookup("A4-100160")

    assert result.status == "not_found"
    assert result.record is None


def test_lookup_returns_exact_live_record():
    payload = {"data": [sample_row()]}
    client = GreenbookClient()

    with patch(
        "app.greenbook_client.urlopen",
        return_value=fake_response(payload),
    ):
        result = client.lookup("A4-100160")

    assert result.status == "match"
    assert result.record["nafdac_reg_number"] == "A4-100160"
    assert result.record["source_name"] == "NAFDAC Greenbook"


def test_lookup_marks_malformed_payload_unavailable():
    client = GreenbookClient()

    with patch(
        "app.greenbook_client.urlopen",
        return_value=fake_response({"unexpected": []}),
    ):
        result = client.lookup("A4-100160")

    assert result.status == "unavailable"
    assert result.record is None


def test_lookup_marks_network_failure_unavailable():
    client = GreenbookClient()

    with patch(
        "app.greenbook_client.urlopen",
        side_effect=OSError("network down"),
    ):
        result = client.lookup("A4-100160")

    assert result.status == "unavailable"
    assert result.record is None
