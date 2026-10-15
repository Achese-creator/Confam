"""Safely validate and promote candidate CONFAM evidence snapshots."""

import json
import os
import tempfile
from pathlib import Path

from app.dataset_validation import (
    validate_alert_snapshot,
    validate_registry_snapshot,
)


DATASET_FILES = {
    "registry": "registry.json",
    "alerts": "alerts.json",
}


def load_and_validate_candidate(dataset: str, candidate_path: Path) -> dict:
    """Load a candidate snapshot and validate it before any replacement."""
    if dataset not in DATASET_FILES:
        raise ValueError(f"Unsupported dataset: {dataset!r}")

    try:
        snapshot = json.loads(candidate_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Candidate is not valid JSON: {exc}"
        ) from exc

    if not isinstance(snapshot, dict):
        raise ValueError("Candidate snapshot must be a JSON object.")

    if dataset == "registry":
        records = validate_registry_snapshot(snapshot)
    else:
        records = validate_alert_snapshot(snapshot)

    return {
        "dataset": dataset,
        "record_count": len(records),
        "snapshot_retrieved_at": snapshot["retrieved_at"],
        "snapshot": snapshot,
    }


def promote_snapshot(
    dataset: str,
    candidate_path: Path,
    destination_dir: Path,
    *,
    apply: bool = False,
) -> dict:
    """Validate first, then atomically replace the selected snapshot."""
    result = load_and_validate_candidate(dataset, candidate_path)
    destination = destination_dir / DATASET_FILES[dataset]

    result["destination"] = str(destination)
    result["applied"] = False

    if not apply:
        result["message"] = (
            "Dry run passed. Existing data was not changed. "
            "Use --apply to promote this snapshot."
        )
        return result

    if not destination_dir.is_dir():
        raise ValueError(
            f"Destination directory does not exist: {destination_dir}"
        )

    serialized = json.dumps(
        result["snapshot"],
        indent=2,
        ensure_ascii=False,
    ) + "\n"

    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=destination_dir,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            temp_path = Path(temp_file.name)
            temp_file.write(serialized)
            temp_file.flush()
            os.fsync(temp_file.fileno())

        # Same-directory replacement is atomic on supported local filesystems.
        os.replace(temp_path, destination)
        temp_path = None
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

    result["applied"] = True
    result["message"] = (
        "Candidate validated and snapshot replaced. "
        "Review the resulting changes and rerun tests before committing."
    )
    return result
