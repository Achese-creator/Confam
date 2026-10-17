from datetime import date

import pytest

from app.verifier import check_package_expiry


@pytest.mark.parametrize(
    ("value", "today", "expected"),
    [
        ("16-10-2026", date(2026, 10, 17), "mismatch"),
        ("18-10-2026", date(2026, 10, 17), "match"),
        ("10/2026", date(2026, 10, 17), "match"),
        ("10/28", date(2026, 10, 17), "match"),
        ("10/25", date(2026, 10, 17), "mismatch"),
        ("09/2026", date(2026, 10, 17), "mismatch"),
        ("2026-10-16", date(2026, 10, 17), "mismatch"),
        ("2026-10-18", date(2026, 10, 17), "match"),
        ("31-02-2026", date(2026, 10, 17), "not_checked"),
        ("13/2026", date(2026, 10, 17), "not_checked"),
        ("", date(2026, 10, 17), "not_checked"),
    ],
)
def test_package_expiry_formats(value, today, expected):
    result = check_package_expiry(value, today=today)
    assert result.outcome == expected
    assert result.check_scope == "package_only"


def test_month_year_expiry_is_valid_through_last_day_of_month():
    result = check_package_expiry("10/2026", today=date(2026, 10, 31))
    assert result.outcome == "match"


def test_expired_package_warning_does_not_claim_counterfeiting():
    result = check_package_expiry("16-10-2026", today=date(2026, 10, 17))
    assert result.outcome == "mismatch"
    assert "Do not use" in result.explanation
    assert "does not establish" in result.explanation
