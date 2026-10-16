from datetime import date

from app.schemas import PackageClaims, RegistryRecord
from app.verifier import verify_package


def sample_record(**overrides) -> RegistryRecord:
    values = {
        "product_name": "Example Paracetamol",
        "active_ingredients": "Paracetamol",
        "strength": "500 mg",
        "dosage_form": "Tablet",
        "nafdac_reg_number": "A1-1234",
        "manufacturer": "Example Pharmaceuticals",
        "registration_status": "Registered",
        "registration_expiry_date": "2030-12-31",
        "source_name": "Synthetic test fixture",
        "source_url": "https://example.invalid/test-fixture",
        "retrieved_at": "2026-10-14",
        "dataset_label": "synthetic_test_fixture",
    }
    values.update(overrides)
    return RegistryRecord(**values)


def matching_claims(**overrides) -> PackageClaims:
    values = {
        "product_name": "Example Paracetamol",
        "active_ingredients": "Paracetamol",
        "strength": "500 mg",
        "dosage_form": "Tablet",
        "nafdac_reg_number": "A1-1234",
        "manufacturer": "Example Pharmaceuticals",
    }
    values.update(overrides)
    return PackageClaims(**values)


def test_matching_claims_find_no_significant_discrepancy():
    result = verify_package(
        matching_claims(),
        [sample_record()],
        today=date(2026, 10, 14),
    )

    assert result.matched_record is True
    assert result.status == "no_significant_discrepancy_detected"
    assert result.dataset_label == "synthetic_test_fixture"


def test_registration_number_for_different_product_is_high_risk():
    record = sample_record(product_name="Mebendazole")

    result = verify_package(
        matching_claims(product_name="Example Paracetamol"),
        [record],
        today=date(2026, 10, 14),
    )

    assert result.matched_record is True
    assert result.status == "high_risk_discrepancy_detected"
    assert result.source_url == "https://example.invalid/test-fixture"


def test_strength_mismatch_is_high_risk():
    result = verify_package(
        matching_claims(strength="250 mg"),
        [sample_record()],
        today=date(2026, 10, 14),
    )

    assert result.status == "high_risk_discrepancy_detected"
    strength_check = next(
        check for check in result.checks if check.field == "strength"
    )
    assert strength_check.outcome == "mismatch"


def test_unknown_registration_number_is_not_called_fake():
    result = verify_package(
        matching_claims(nafdac_reg_number="UNKNOWN-999"),
        [sample_record()],
        today=date(2026, 10, 14),
    )

    assert result.matched_record is False
    assert result.status == "verification_concern_detected"
    assert "not proof of counterfeiting" in result.summary


def test_missing_fields_require_review():
    result = verify_package(
        PackageClaims(nafdac_reg_number="A1-1234"),
        [sample_record()],
        today=date(2026, 10, 14),
    )

    assert result.status == "verification_concern_detected"



def test_expired_registration_requires_attention():
    record = sample_record(registration_expiry_date="2025-12-31")

    result = verify_package(
        matching_claims(),
        [record],
        today=date(2026, 10, 14),
    )

    assert result.status == "verification_concern_detected"
    expiry_check = next(
        check
        for check in result.checks
        if check.field == "registration_expiry_date"
    )
    assert expiry_check.outcome == "mismatch"
    assert expiry_check.check_scope == "registry_record_only"
    assert expiry_check.package_value is None


def test_valid_registration_expiry_uses_registry_only_scope():
    record = sample_record(registration_expiry_date="2030-12-31")

    result = verify_package(
        matching_claims(),
        [record],
        today=date(2026, 10, 14),
    )

    expiry_check = next(
        check
        for check in result.checks
        if check.field == "registration_expiry_date"
    )
    assert expiry_check.outcome == "not_checked"
    assert expiry_check.check_scope == "registry_record_only"
    assert expiry_check.package_value is None
    assert expiry_check.reference_value == "2030-12-31"
    assert "no package-label comparison was performed" in (
        expiry_check.explanation
    )


def test_active_registration_status_is_reported_as_registry_only():
    result = verify_package(
        matching_claims(),
        [sample_record(registration_status="Active")],
        today=date(2026, 10, 14),
    )

    status_check = next(
        check for check in result.checks
        if check.field == "registration_status"
    )
    assert status_check.outcome == "match"
    assert status_check.reference_value == "Active"
    assert status_check.package_value is None
    assert status_check.check_scope == "registry_record_only"
    assert "does not prove package authenticity" in status_check.explanation


def test_non_active_registration_status_requires_high_risk_review():
    result = verify_package(
        matching_claims(),
        [sample_record(registration_status="Suspended")],
        today=date(2026, 10, 14),
    )

    status_check = next(
        check for check in result.checks
        if check.field == "registration_status"
    )
    assert result.status == "high_risk_discrepancy_detected"
    assert status_check.outcome == "mismatch"
    assert status_check.reference_value == "Suspended"
    assert status_check.check_scope == "registry_record_only"


def test_missing_registration_status_is_not_checked():
    result = verify_package(
        matching_claims(),
        [sample_record(registration_status=None)],
        today=date(2026, 10, 14),
    )

    status_check = next(
        check for check in result.checks
        if check.field == "registration_status"
    )
    assert status_check.outcome == "not_checked"
    assert status_check.reference_value is None
    assert status_check.check_scope == "registry_record_only"

def test_registration_number_formatting_differences_still_match():
    result = verify_package(
        matching_claims(nafdac_reg_number=" A1 / 1234 "),
        [sample_record(nafdac_reg_number="A1-1234")],
        today=date(2026, 10, 14),
    )

    assert result.matched_record is True
    assert result.status == "no_significant_discrepancy_detected"


def test_duplicate_normalized_registration_numbers_require_review():
    first_record = sample_record()
    second_record = sample_record(
        nafdac_reg_number="A1 1234",
        product_name="Different Medicine",
        active_ingredients="Different Ingredient",
    )

    result = verify_package(
        matching_claims(),
        [first_record, second_record],
        today=date(2026, 10, 14),
    )

    assert result.matched_record is False
    assert result.status == "verification_concern_detected"
    assert "ambiguous" in result.summary.lower()
    assert result.checks == []
    assert result.source_url is None
    assert any(
        "duplicate normalized registration numbers" in item.lower()
        for item in result.limitations
    )


def test_duplicate_registration_numbers_do_not_hide_a_matching_alert():
    from app.verifier import verify_package

    alert = {
        "alert_id": "TEST-ALERT-1",
        "title": "Test safety alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "BATCH-7",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    result = verify_package(
        matching_claims(batch_number="BATCH-7"),
        [
            sample_record(),
            sample_record(nafdac_reg_number="A1 1234"),
        ],
        today=date(2026, 10, 14),
        alert_records=[alert],
    )

    assert result.matched_record is False
    assert result.status == "high_risk_discrepancy_detected"
    assert len(result.safety_alerts) == 1


def test_alert_batch_expiry_is_historical_after_expiry_month():
    from app.verifier import find_relevant_alerts

    alert = {
        "alert_id": "TEST-EXPIRED-BATCH",
        "title": "Historical batch alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "BATCH-7",
        "expiry_date": "07/2026",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    matches = find_relevant_alerts(
        matching_claims(),
        [alert],
        today=date(2026, 10, 15),
    )

    assert len(matches) == 1
    assert matches[0].batch_expiry_date == "07/2026"
    assert matches[0].batch_expiry_status == "expired"


def test_alert_batch_expiry_is_not_expired_on_last_day_of_month():
    from app.verifier import classify_alert_batch_expiry

    assert classify_alert_batch_expiry(
        "07/2026",
        today=date(2026, 7, 31),
    ) == "not_expired"


def test_alert_batch_expiry_unknown_when_missing_or_invalid():
    from app.verifier import classify_alert_batch_expiry

    assert classify_alert_batch_expiry(
        None,
        today=date(2026, 10, 15),
    ) == "unknown"
    assert classify_alert_batch_expiry(
        "not-a-date",
        today=date(2026, 10, 15),
    ) == "unknown"


def test_expired_batch_does_not_remove_historical_alert_evidence():
    from app.verifier import find_relevant_alerts

    alert = {
        "alert_id": "TEST-HISTORICAL",
        "title": "Historical alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "BATCH-7",
        "expiry_date": "2025-07-31",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    matches = find_relevant_alerts(
        matching_claims(),
        [alert],
        today=date(2026, 10, 15),
    )

    assert len(matches) == 1
    assert matches[0].batch_expiry_status == "expired"


def test_alert_matches_exact_product_and_registration_number():
    from app.verifier import find_relevant_alerts

    alert = {
        "alert_id": "TEST-ALERT-2",
        "title": "Test alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "OTHER-BATCH",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    matches = find_relevant_alerts(
        matching_claims(),
        [alert],
    )

    assert len(matches) == 1
    assert matches[0].alert_id == "TEST-ALERT-2"


def test_alert_matches_exact_product_and_batch_number():
    from app.verifier import find_relevant_alerts

    alert = {
        "alert_id": "TEST-ALERT-3",
        "title": "Test alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "DIFFERENT-NUMBER",
        "batch_number": "BATCH-7",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    matches = find_relevant_alerts(
        matching_claims(batch_number="BATCH-7"),
        [alert],
    )

    assert len(matches) == 1
    assert matches[0].alert_id == "TEST-ALERT-3"


def test_alert_does_not_match_on_registration_number_alone():
    from app.verifier import find_relevant_alerts

    alert = {
        "alert_id": "TEST-ALERT-4",
        "title": "Test alert",
        "product_name": "Completely Different Medicine",
        "reported_registration_number": "A1-1234",
        "batch_number": "UNRELATED-BATCH",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    matches = find_relevant_alerts(
        matching_claims(),
        [alert],
    )

    assert matches == []


def test_alert_does_not_match_when_package_product_name_is_missing():
    from app.verifier import find_relevant_alerts

    alert = {
        "alert_id": "TEST-ALERT-5",
        "title": "Test alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "BATCH-7",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    matches = find_relevant_alerts(
        PackageClaims(
            nafdac_reg_number="A1-1234",
            batch_number="BATCH-7",
        ),
        [alert],
    )

    assert matches == []

def test_unrecognized_registration_status_requires_review_not_high_risk():
    result = verify_package(
        matching_claims(),
        [sample_record(registration_status="Under Administrative Review")],
        today=date(2026, 10, 14),
    )

    status_check = next(
        check for check in result.checks
        if check.field == "registration_status"
    )
    assert status_check.outcome == "not_checked"
    assert status_check.check_scope == "registry_record_only"
    assert result.status == "verification_concern_detected"
    assert "unrecognized status" in status_check.explanation.lower()


def test_revoked_registration_status_requires_high_risk_review():
    result = verify_package(
        matching_claims(),
        [sample_record(registration_status="Revoked")],
        today=date(2026, 10, 14),
    )

    status_check = next(
        check for check in result.checks
        if check.field == "registration_status"
    )
    assert status_check.outcome == "mismatch"
    assert result.status == "high_risk_discrepancy_detected"



def test_alert_does_not_match_same_product_and_registration_with_different_batch():
    from app.verifier import find_relevant_alerts

    alert = {
        "alert_id": "TEST-DIFFERENT-BATCH",
        "title": "Batch-specific alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "AFFECTED-001",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    matches = find_relevant_alerts(
        matching_claims(batch_number="UNRELATED-999"),
        [alert],
    )

    # The product alert remains relevant, but the package batch differs.
    assert len(matches) == 1
    assert matches[0].batch_match_status == "different"
    assert "differs from the reported batch" in matches[0].explanation


def test_alert_does_not_match_on_batch_number_when_product_name_is_dissimilar():
    from app.verifier import find_relevant_alerts

    alert = {
        "alert_id": "TEST-DISSIMILAR-PRODUCT",
        "title": "Batch-specific alert",
        "product_name": "Completely Different Medicine",
        "reported_registration_number": "OTHER-1234",
        "batch_number": "BATCH-7",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    matches = find_relevant_alerts(
        matching_claims(batch_number="BATCH-7"),
        [alert],
    )

    assert matches == []


def test_alert_does_not_match_on_registration_number_when_product_name_is_only_loosely_similar():
    from app.verifier import find_relevant_alerts

    alert = {
        "alert_id": "TEST-LOOSE-NAME",
        "title": "Test alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "OTHER-BATCH",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    matches = find_relevant_alerts(
        PackageClaims(
            product_name="Example",
            nafdac_reg_number="A1-1234",
            batch_number="UNRELATED-BATCH",
        ),
        [alert],
    )

    assert matches == []


def test_product_and_registration_match_with_different_batch_is_concern_not_high_risk():
    alert = {
        "alert_id": "TEST-DIFFERENT-BATCH-RISK",
        "title": "Batch-specific alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "AFFECTED-001",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    result = verify_package(
        matching_claims(batch_number="UNRELATED-999"),
        [sample_record()],
        today=date(2026, 10, 15),
        alert_records=[alert],
    )

    assert len(result.safety_alerts) == 1
    assert result.safety_alerts[0].batch_match_status == "different"
    assert result.status == "verification_concern_detected"
    assert "batch has not been confirmed" in result.summary.lower()


def test_exact_batch_match_escalates_to_high_risk():
    alert = {
        "alert_id": "TEST-EXACT-BATCH-RISK",
        "title": "Batch-specific alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "BATCH-7",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    result = verify_package(
        matching_claims(batch_number="BATCH-7"),
        [sample_record()],
        today=date(2026, 10, 15),
        alert_records=[alert],
    )

    assert len(result.safety_alerts) == 1
    assert result.safety_alerts[0].batch_match_status == "match"
    assert result.status == "high_risk_discrepancy_detected"


def test_unchecked_batch_keeps_alert_visible_without_alert_only_high_risk():
    alert = {
        "alert_id": "TEST-UNKNOWN-BATCH-RISK",
        "title": "Product-level alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "BATCH-7",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    result = verify_package(
        matching_claims(batch_number=None),
        [sample_record()],
        today=date(2026, 10, 15),
        alert_records=[alert],
    )

    assert len(result.safety_alerts) == 1
    assert result.safety_alerts[0].batch_match_status == "not_checked"
    assert result.status == "verification_concern_detected"


def test_independent_material_discrepancy_stays_high_risk_with_different_batch():
    alert = {
        "alert_id": "TEST-INDEPENDENT-RISK",
        "title": "Batch-specific alert",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_number": "AFFECTED-001",
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    claims = matching_claims(
        batch_number="UNRELATED-999",
        strength="900 mg",
    )
    result = verify_package(
        claims,
        [sample_record()],
        today=date(2026, 10, 15),
        alert_records=[alert],
    )

    assert result.safety_alerts[0].batch_match_status == "different"
    assert result.status == "high_risk_discrepancy_detected"




def test_alert_matches_any_reported_batch_in_multi_batch_notice():
    from app.verifier import find_relevant_alerts

    alert = {
        "alert_id": "TEST-MULTI-BATCH",
        "title": "Notice covering multiple batches",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_numbers": ["BATCH-1", "BATCH-2", "BATCH-3"],
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    matches = find_relevant_alerts(
        matching_claims(batch_number="BATCH-2"),
        [alert],
    )

    assert len(matches) == 1
    assert matches[0].batch_match_status == "match"
    assert matches[0].reported_batch_numbers == [
        "BATCH-1", "BATCH-2", "BATCH-3"
    ]
    assert matches[0].matched_batch_number == "BATCH-2"


def test_multi_batch_alert_does_not_high_risk_flag_unlisted_batch_alone():
    alert = {
        "alert_id": "TEST-MULTI-BATCH-DIFFERENT",
        "title": "Notice covering multiple batches",
        "product_name": "Example Paracetamol",
        "reported_registration_number": "A1-1234",
        "batch_numbers": ["BATCH-1", "BATCH-2"],
        "source_name": "Test source",
        "source_url": "https://example.invalid/alert",
    }

    result = verify_package(
        matching_claims(batch_number="UNLISTED-BATCH"),
        [sample_record()],
        today=date(2026, 10, 15),
        alert_records=[alert],
    )

    assert len(result.safety_alerts) == 1
    assert result.safety_alerts[0].batch_match_status == "different"
    assert result.safety_alerts[0].matched_batch_number is None
    assert result.status == "verification_concern_detected"
