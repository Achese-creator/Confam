"""Integrity checks for versioned CONFAM evidence snapshots."""

import re
from datetime import date
from urllib.parse import urlparse

from pydantic import ValidationError

from app.schemas import RegistryRecord, SafetyAlertRecord


def _require_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string.")
    return value.strip()


def _require_date(value: object, field: str) -> date:
    text = _require_text(value, field)
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(
            f"{field} must be a valid ISO date (YYYY-MM-DD); got {text!r}."
        ) from exc


def _require_https_url(value: object, field: str) -> str:
    text = _require_text(value, field)
    parsed = urlparse(text)

    if parsed.scheme != "https" or not parsed.netloc:
        raise ValueError(f"{field} must be an absolute HTTPS URL.")

    return text


def _require_snapshot_metadata(snapshot: dict, dataset_kind: str) -> date:
    if not isinstance(snapshot, dict):
        raise ValueError(f"{dataset_kind} snapshot must be a JSON object.")

    _require_text(snapshot.get("dataset_name"), "dataset_name")
    _require_text(snapshot.get("schema_version"), "schema_version")
    return _require_date(snapshot.get("retrieved_at"), "retrieved_at")


def normalize_registration_number(value: str) -> str:
    """Normalize formatting without changing the underlying letters or digits."""
    return re.sub(r"[^a-z0-9]", "", value.casefold())


def validate_registry_snapshot(snapshot: dict) -> list[RegistryRecord]:
    """Validate registry metadata, provenance, records, and uniqueness."""
    snapshot_date = _require_snapshot_metadata(snapshot, "Registry")

    sources = snapshot.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("Registry snapshot must declare at least one source.")

    declared_sources: set[tuple[str, str]] = set()
    for index, source in enumerate(sources):
        if not isinstance(source, dict):
            raise ValueError(f"sources[{index}] must be an object.")

        name = _require_text(source.get("name"), f"sources[{index}].name")
        url = _require_https_url(source.get("url"), f"sources[{index}].url")
        declared_sources.add((name, url))

    raw_records = snapshot.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("Registry snapshot must contain at least one record.")

    records: list[RegistryRecord] = []
    seen_registration_numbers: dict[str, str] = {}

    for index, raw_record in enumerate(raw_records):
        if not isinstance(raw_record, dict):
            raise ValueError(f"records[{index}] must be an object.")

        try:
            record = RegistryRecord.model_validate(raw_record)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid registry record at records[{index}]: {exc}"
            ) from exc

        _require_https_url(record.source_url, f"records[{index}].source_url")
        record_date = _require_date(
            record.retrieved_at, f"records[{index}].retrieved_at"
        )

        if record_date > snapshot_date:
            raise ValueError(
                f"records[{index}].retrieved_at is later than the "
                "snapshot retrieved_at date."
            )

        if (record.source_name, record.source_url) not in declared_sources:
            raise ValueError(
                f"Registry record {record.nafdac_reg_number!r} cites a source "
                "that is not declared in snapshot metadata."
            )

        normalized_number = normalize_registration_number(
            record.nafdac_reg_number
        )
        if not normalized_number:
            raise ValueError(
                f"Registry record at records[{index}] has an empty "
                "normalized registration number."
            )

        previous_number = seen_registration_numbers.get(normalized_number)
        if previous_number is not None:
            raise ValueError(
                "Duplicate normalized NAFDAC registration number: "
                f"{previous_number!r} and {record.nafdac_reg_number!r}. "
                "Resolve the ambiguity before loading this snapshot."
            )

        seen_registration_numbers[normalized_number] = (
            record.nafdac_reg_number
        )
        records.append(record)

    return records


def validate_alert_snapshot(snapshot: dict) -> list[SafetyAlertRecord]:
    """Validate safety-alert metadata, provenance, dates, and unique IDs."""
    snapshot_date = _require_snapshot_metadata(snapshot, "Alert")

    raw_records = snapshot.get("records")
    if not isinstance(raw_records, list) or not raw_records:
        raise ValueError("Alert snapshot must contain at least one record.")

    records: list[SafetyAlertRecord] = []
    seen_alert_ids: set[str] = set()

    for index, raw_record in enumerate(raw_records):
        if not isinstance(raw_record, dict):
            raise ValueError(f"records[{index}] must be an object.")

        try:
            record = SafetyAlertRecord.model_validate(raw_record)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid safety alert at records[{index}]: {exc}"
            ) from exc

        _require_https_url(record.source_url, f"records[{index}].source_url")
        record_date = _require_date(
            record.retrieved_at, f"records[{index}].retrieved_at"
        )

        if record_date > snapshot_date:
            raise ValueError(
                f"records[{index}].retrieved_at is later than the "
                "snapshot retrieved_at date."
            )

        if record.published_at is not None:
            published_date = _require_date(
                record.published_at, f"records[{index}].published_at"
            )
            if published_date > record_date:
                raise ValueError(
                    f"records[{index}].published_at is later than its "
                    "retrieved_at date."
                )

        if record.alert_id in seen_alert_ids:
            raise ValueError(
                f"Duplicate safety alert ID: {record.alert_id!r}."
            )

        seen_alert_ids.add(record.alert_id)
        records.append(record)

    return records
