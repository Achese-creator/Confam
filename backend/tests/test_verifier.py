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
    assert expiry_check.outcome == "match"
    assert expiry_check.check_scope == "registry_record_only"
    assert expiry_check.package_value is None
