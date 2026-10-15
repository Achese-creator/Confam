import copy
import json
from pathlib import Path

import pytest

from app.dataset_validation import (
    normalize_registration_number,
    validate_alert_snapshot,
    validate_registry_snapshot,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def read_snapshot(filename: str) -> dict:
    with (DATA_DIR / filename).open(encoding="utf-8") as file:
        return json.load(file)


def registry_snapshot() -> dict:
    return read_snapshot("registry.json")


def alert_snapshot() -> dict:
    return read_snapshot("alerts.json")


def test_current_registry_snapshot_passes_integrity_checks():
    records = validate_registry_snapshot(registry_snapshot())

    assert len(records) == len(registry_snapshot()["records"])
    assert all(record.source_url.startswith("https://") for record in records)


def test_current_alert_snapshot_passes_integrity_checks():
    records = validate_alert_snapshot(alert_snapshot())

    assert len(records) == 5
    assert records[0].alert_id == "NAFDAC-PUBLIC-ALERT-037-2025"


def test_registration_number_normalization_only_ignores_formatting():
    assert normalize_registration_number("A4-100160") == "a4100160"
    assert normalize_registration_number(" a4 / 100160 ") == "a4100160"
    assert normalize_registration_number("A4-100161") != "a4100160"


def test_registry_rejects_duplicate_normalized_registration_numbers():
    snapshot = registry_snapshot()
    duplicate = copy.deepcopy(snapshot["records"][0])
    duplicate["nafdac_reg_number"] = "A4 100160"
    duplicate["source_url"] = snapshot["sources"][0]["url"]
    duplicate["source_name"] = snapshot["sources"][0]["name"]
    snapshot["records"].append(duplicate)

    with pytest.raises(ValueError, match="Duplicate normalized"):
        validate_registry_snapshot(snapshot)


def test_registry_rejects_invalid_snapshot_date():
    snapshot = registry_snapshot()
    snapshot["retrieved_at"] = "14-10-2026"

    with pytest.raises(ValueError, match="ISO date"):
        validate_registry_snapshot(snapshot)


def test_registry_rejects_undeclared_record_source():
    snapshot = registry_snapshot()
    snapshot["records"][0]["source_url"] = (
        "https://example.invalid/unlisted-record"
    )

    with pytest.raises(ValueError, match="not declared"):
        validate_registry_snapshot(snapshot)


def test_registry_rejects_record_retrieved_after_snapshot_date():
    snapshot = registry_snapshot()
    snapshot["records"][0]["retrieved_at"] = "2026-10-16"

    with pytest.raises(ValueError, match="later than the snapshot"):
        validate_registry_snapshot(snapshot)


def test_registry_rejects_non_https_source_url():
    snapshot = registry_snapshot()
    snapshot["sources"][0]["url"] = "http://example.com/record"
    snapshot["records"][0]["source_name"] = snapshot["sources"][0]["name"]
    snapshot["records"][0]["source_url"] = snapshot["sources"][0]["url"]

    with pytest.raises(ValueError, match="HTTPS"):
        validate_registry_snapshot(snapshot)


def test_alerts_reject_duplicate_alert_ids():
    snapshot = alert_snapshot()
    snapshot["records"].append(copy.deepcopy(snapshot["records"][0]))

    with pytest.raises(ValueError, match="Duplicate safety alert ID"):
        validate_alert_snapshot(snapshot)


def test_alerts_reject_publication_date_after_retrieval_date():
    snapshot = alert_snapshot()
    snapshot["records"][0]["published_at"] = "2026-10-15"

    with pytest.raises(ValueError, match="published_at is later"):
        validate_alert_snapshot(snapshot)
