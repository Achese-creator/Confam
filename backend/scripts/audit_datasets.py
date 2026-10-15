"""Run a reproducible audit of CONFAM's checked-in evidence snapshots."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

from app.dataset_audit import audit_snapshots  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit CONFAM registry and safety-alert snapshots."
    )
    parser.add_argument(
        "--as-of",
        help="Audit date in YYYY-MM-DD format (defaults to today's date).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional path for writing the JSON report.",
    )
    args = parser.parse_args()

    try:
        audit_date = date.fromisoformat(args.as_of) if args.as_of else date.today()
    except ValueError:
        parser.error("--as-of must be a valid date in YYYY-MM-DD format.")

    data_dir = BACKEND / "data"
    try:
        registry = json.loads(
            (data_dir / "registry.json").read_text(encoding="utf-8")
        )
        alerts = json.loads(
            (data_dir / "alerts.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError) as exc:
        print(f"Unable to read evidence snapshots: {exc}", file=sys.stderr)
        return 2

    report = audit_snapshots(registry, alerts, as_of=audit_date)
    rendered = json.dumps(report, indent=2, ensure_ascii=False)

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Audit report written to {args.output}")
    else:
        print(rendered)

    has_errors = (
        not report["datasets"]["registry"]["validation_passed"]
        or not report["datasets"]["alerts"]["validation_passed"]
        or any(item["severity"] == "error" for item in report["findings"])
    )
    return 1 if has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
