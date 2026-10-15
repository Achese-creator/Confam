"""Reproducible quality and coverage report for CONFAM evidence snapshots."""

from collections import Counter
from datetime import date
from urllib.parse import urlparse

from app.dataset_validation import (
    normalize_registration_number,
    validate_alert_snapshot,
    validate_registry_snapshot,
)


REGISTRY_OPTIONAL_FIELDS = (
    "active_ingredients",
    "strength",
    "dosage_form",
    "manufacturer",
    "registration_status",
    "registration_expiry_date",
)

ALERT_OPTIONAL_FIELDS = (
    "alert_number",
    "product_ingredients",
    "product_strength",
    "reported_registration_number",
    "reference_product_name",
    "reference_product_strength",
    "reference_manufacturer",
    "batch_number",
    "manufacturing_date",
    "expiry_date",
    "alert_scope",
    "risk_classification",
    "published_at",
    "curation_note",
)


def _completeness(records: list[dict], fields: tuple[str, ...]) -> dict:
    """Count populated and missing optional values without guessing."""
    result = {}

    for field in fields:
        populated = sum(
            isinstance(record.get(field), str)
            and bool(record[field].strip())
            for record in records
        )
        total = len(records)
        result[field] = {
            "populated": populated,
            "missing": total - populated,
            "completeness_percent": (
                round(populated / total * 100, 1) if total else 0.0
            ),
        }

    return result


def _source_summary(records: list[dict]) -> dict:
    counts = Counter(
        record["source_name"]
        for record in records
        if isinstance(record.get("source_name"), str)
        and record["source_name"].strip()
    )
    domains = Counter(
        urlparse(record["source_url"]).hostname
        for record in records
        if isinstance(record.get("source_url"), str)
        and urlparse(record["source_url"]).hostname
    )

    return {
        "record_count_by_source_name": dict(sorted(counts.items())),
        "record_count_by_source_domain": dict(sorted(domains.items())),
    }


def _snapshot_date(snapshot: dict) -> str | None:
    value = snapshot.get("retrieved_at")
    return value if isinstance(value, str) else None


def audit_snapshots(
    registry_snapshot: dict,
    alert_snapshot: dict,
    *,
    as_of: date | None = None,
) -> dict:
    """Audit snapshot quality and coverage; does not assess product authenticity."""
    as_of = as_of or date.today()
    findings: list[dict] = []

    registry_records = registry_snapshot.get("records", [])
    alert_records = alert_snapshot.get("records", [])

    validation_errors: list[dict] = []

    try:
        validate_registry_snapshot(registry_snapshot)
        registry_valid = True
    except (ValueError, TypeError, KeyError) as exc:
        registry_valid = False
        validation_errors.append(
            {"dataset": "registry", "message": str(exc)}
        )

    try:
        validate_alert_snapshot(alert_snapshot)
        alerts_valid = True
    except (ValueError, TypeError, KeyError) as exc:
        alerts_valid = False
        validation_errors.append(
            {"dataset": "alerts", "message": str(exc)}
        )

    if len(registry_records) < 100:
        findings.append({
            "severity": "warning",
            "code": "SMALL_REGISTRY_SNAPSHOT",
            "message": (
                f"Registry snapshot contains {len(registry_records)} records; "
                "coverage is too small to represent the Nigerian medicine "
                "market comprehensively."
            ),
        })

    if len(alert_records) < 10:
        findings.append({
            "severity": "warning",
            "code": "LIMITED_ALERT_SNAPSHOT",
            "message": (
                f"Alert snapshot contains {len(alert_records)} records; "
                "absence of a matching alert must not be interpreted as "
                "evidence that a product is safe."
            ),
        })

    registry_date = _snapshot_date(registry_snapshot)
    alerts_date = _snapshot_date(alert_snapshot)

    for dataset_name, snapshot_date in (
        ("registry", registry_date),
        ("alerts", alerts_date),
    ):
        if snapshot_date is None:
            findings.append({
                "severity": "error",
                "code": "MISSING_SNAPSHOT_DATE",
                "message": f"{dataset_name} snapshot has no retrieval date.",
            })
            continue

        try:
            parsed_date = date.fromisoformat(snapshot_date)
        except ValueError:
            findings.append({
                "severity": "error",
                "code": "INVALID_SNAPSHOT_DATE",
                "message": (
                    f"{dataset_name} snapshot has an invalid retrieval date."
                ),
            })
            continue

        age_days = (as_of - parsed_date).days
        if age_days < 0:
            findings.append({
                "severity": "error",
                "code": "FUTURE_SNAPSHOT_DATE",
                "message": (
                    f"{dataset_name} snapshot date is after the audit date."
                ),
            })
        elif age_days > 90:
            findings.append({
                "severity": "warning",
                "code": "STALE_SNAPSHOT",
                "message": (
                    f"{dataset_name} snapshot is {age_days} days old; "
                    "refresh the source evidence before relying on it."
                ),
            })

    normalized_numbers = [
        normalize_registration_number(record["nafdac_reg_number"])
        for record in registry_records
        if isinstance(record, dict)
        and isinstance(record.get("nafdac_reg_number"), str)
    ]
    duplicate_registration_numbers = sorted(
        number for number, count in Counter(normalized_numbers).items()
        if number and count > 1
    )

    alert_ids = [
        record["alert_id"]
        for record in alert_records
        if isinstance(record, dict)
        and isinstance(record.get("alert_id"), str)
    ]
    duplicate_alert_ids = sorted(
        alert_id for alert_id, count in Counter(alert_ids).items()
        if count > 1
    )

    if duplicate_registration_numbers:
        findings.append({
            "severity": "error",
            "code": "DUPLICATE_REGISTRATION_NUMBERS",
            "message": (
                "Registry contains duplicate normalized registration numbers."
            ),
        })

    if duplicate_alert_ids:
        findings.append({
            "severity": "error",
            "code": "DUPLICATE_ALERT_IDS",
            "message": "Alert snapshot contains duplicate alert IDs.",
        })

    return {
        "report_version": "1.0",
        "audit_as_of": as_of.isoformat(),
        "purpose": (
            "Dataset quality and coverage audit. This report does not verify "
            "medicine authenticity, current regulatory status, or source "
            "transcription accuracy."
        ),
        "datasets": {
            "registry": {
                "dataset_name": registry_snapshot.get("dataset_name"),
                "schema_version": registry_snapshot.get("schema_version"),
                "snapshot_retrieved_at": registry_date,
                "record_count": len(registry_records),
                "validation_passed": registry_valid,
                "source_summary": _source_summary(registry_records),
                "optional_field_completeness": _completeness(
                    registry_records, REGISTRY_OPTIONAL_FIELDS
                ),
            },
            "alerts": {
                "dataset_name": alert_snapshot.get("dataset_name"),
                "schema_version": alert_snapshot.get("schema_version"),
                "snapshot_retrieved_at": alerts_date,
                "record_count": len(alert_records),
                "validation_passed": alerts_valid,
                "source_summary": _source_summary(alert_records),
                "optional_field_completeness": _completeness(
                    alert_records, ALERT_OPTIONAL_FIELDS
                ),
            },
        },
        "integrity_checks": {
            "duplicate_normalized_registration_numbers":
                duplicate_registration_numbers,
            "duplicate_alert_ids": duplicate_alert_ids,
        },
        "validation_errors": validation_errors,
        "findings": findings,
        "limitations": [
            "Snapshot completeness is not established by this audit.",
            "HTTPS URLs and declared sources do not prove source authenticity.",
            "Field completeness does not prove values were transcribed correctly.",
            "A matching registration number does not prove package authenticity.",
            "No matching safety alert does not establish that a medicine is safe.",
        ],
    }
