"""Validate a candidate snapshot; optionally promote it into backend/data."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.snapshot_ingestion import promote_snapshot  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a candidate CONFAM snapshot. "
            "Defaults to a non-destructive dry run."
        )
    )
    parser.add_argument(
        "--dataset",
        choices=("registry", "alerts"),
        required=True,
    )
    parser.add_argument(
        "--candidate",
        type=Path,
        required=True,
        help="Path to the candidate JSON snapshot.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Replace the selected snapshot after validation.",
    )
    args = parser.parse_args()

    try:
        result = promote_snapshot(
            args.dataset,
            args.candidate.resolve(),
            BACKEND / "data",
            apply=args.apply,
        )
    except (ValueError, OSError) as exc:
        print(
            json.dumps({"success": False, "error": str(exc)}, indent=2),
            file=sys.stderr,
        )
        return 1

    result.pop("snapshot", None)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
