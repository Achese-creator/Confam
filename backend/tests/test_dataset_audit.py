import copy
import json
from datetime import date
from pathlib import Path

from app.dataset_audit import audit_snapshots


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def read_snapshot(filename: str) -> dict:
    with (DATA_DIR / filename).open(encoding="utf-8") as file:
        return json.load(file)


def test_audit_reports_current_snapshot_counts_and_validation():
    report = audit_snapshots(
        read_snapshot("registry.json"),
        read_snapshot("alerts.json"),
        as_of=date(2026, 10, 15),
    )

    assert report["datasets"]["registry"]["record_count"] == 3
    assert report["datasets"]["alerts"]["record_count"] == 1
    assert report["datasets"]["registry"]["validation_passed"] is True
    assert report["datasets"]["alerts"]["validation_passed"] is True


def test_audit_warns_about_small_snapshots():
    report = audit_snapshots(
        read_snapshot("registry.json"),
        read_snapshot("alerts.json"),
        as_of=date(2026, 10, 15),
    )

    codes = {finding["code"] for finding in report["findings"]}
    assert "SMALL_REGISTRY_SNAPSHOT" in codes
    assert "LIMITED_ALERT_SNAPSHOT" in codes


def test_audit_reports_optional_field_completeness():
    registry = read_snapshot("registry.json")
    registry["records"][0]["manufacturer"] = None

    report = audit_snapshots(
        registry,
        read_snapshot("alerts.json"),
        as_of=date(2026, 10, 15),
    )

    manufacturer = report["datasets"]["registry"][
        "optional_field_completeness"
    ]["manufacturer"]

    assert manufacturer == {
        "populated": 2,
        "missing": 1,
        "completeness_percent": 66.7,
    }


def test_audit_flags_duplicate_registration_numbers():
    registry = read_snapshot("registry.json")
    duplicate = copy.deepcopy(registry["records"][0])
    duplicate["nafdac_reg_number"] = "A4 100160"
    registry["records"].append(duplicate)

    report = audit_snapshots(
        registry,
        read_snapshot("alerts.json"),
        as_of=date(2026, 10, 15),
    )

    assert report["datasets"]["registry"]["validation_passed"] is False
    assert report["integrity_checks"][
        "duplicate_normalized_registration_numbers"
    ] == ["a4100160"]


def test_audit_flags_stale_snapshots_using_explicit_audit_date():
    report = audit_snapshots(
        read_snapshot("registry.json"),
        read_snapshot("alerts.json"),
        as_of=date(2027, 2, 1),
    )

    stale = {
        finding["code"]
        for finding in report["findings"]
        if finding["severity"] == "warning"
    }
    assert "STALE_SNAPSHOT" in stale


def test_audit_explains_its_scope_and_limitations():
    report = audit_snapshots(
        read_snapshot("registry.json"),
        read_snapshot("alerts.json"),
        as_of=date(2026, 10, 15),
    )

    assert "does not verify medicine authenticity" in report["purpose"]
    assert any(
        "does not prove package authenticity" in item
        for item in report["limitations"]
    )
