import copy
import json
from pathlib import Path

import pytest

from app.snapshot_ingestion import (
    load_and_validate_candidate,
    promote_snapshot,
)


DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def read_snapshot(filename: str) -> dict:
    return json.loads((DATA_DIR / filename).read_text(encoding="utf-8"))


def write_candidate(tmp_path: Path, snapshot: dict) -> Path:
    path = tmp_path / "candidate.json"
    path.write_text(
        json.dumps(snapshot, indent=2),
        encoding="utf-8",
    )
    return path


def test_valid_registry_candidate_can_be_loaded(tmp_path):
    candidate = write_candidate(tmp_path, read_snapshot("registry.json"))

    result = load_and_validate_candidate("registry", candidate)

    assert result["record_count"] == 3
    assert result["snapshot_retrieved_at"] == "2026-10-14"


def test_invalid_candidate_is_rejected(tmp_path):
    snapshot = read_snapshot("registry.json")
    snapshot["records"][0]["nafdac_reg_number"] = ""
    candidate = write_candidate(tmp_path, snapshot)

    with pytest.raises(ValueError):
        load_and_validate_candidate("registry", candidate)


def test_dry_run_does_not_modify_destination(tmp_path):
    candidate = write_candidate(tmp_path, read_snapshot("registry.json"))
    destination_dir = tmp_path / "data"
    destination_dir.mkdir()
    existing = destination_dir / "registry.json"
    original = '{"existing": true}\n'
    existing.write_text(original, encoding="utf-8")

    result = promote_snapshot(
        "registry",
        candidate,
        destination_dir,
    )

    assert result["applied"] is False
    assert existing.read_text(encoding="utf-8") == original


def test_apply_replaces_destination_only_after_validation(tmp_path):
    candidate = write_candidate(tmp_path, read_snapshot("registry.json"))
    destination_dir = tmp_path / "data"
    destination_dir.mkdir()
    existing = destination_dir / "registry.json"
    existing.write_text('{"old": true}\n', encoding="utf-8")

    result = promote_snapshot(
        "registry",
        candidate,
        destination_dir,
        apply=True,
    )

    promoted = json.loads(existing.read_text(encoding="utf-8"))
    assert result["applied"] is True
    assert len(promoted["records"]) == 3
    assert promoted["dataset_name"] == "CONFAM curated medicine registry snapshot"


def test_invalid_candidate_never_replaces_existing_snapshot(tmp_path):
    snapshot = read_snapshot("registry.json")
    snapshot["records"].append(copy.deepcopy(snapshot["records"][0]))
    candidate = write_candidate(tmp_path, snapshot)

    destination_dir = tmp_path / "data"
    destination_dir.mkdir()
    existing = destination_dir / "registry.json"
    original = '{"keep": "unchanged"}\n'
    existing.write_text(original, encoding="utf-8")

    with pytest.raises(ValueError):
        promote_snapshot(
            "registry",
            candidate,
            destination_dir,
            apply=True,
        )

    assert existing.read_text(encoding="utf-8") == original


def test_unknown_dataset_is_rejected(tmp_path):
    candidate = write_candidate(tmp_path, read_snapshot("registry.json"))

    with pytest.raises(ValueError, match="Unsupported dataset"):
        load_and_validate_candidate("medicines", candidate)
