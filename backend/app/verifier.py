import re
from datetime import date

from rapidfuzz.fuzz import ratio

from app.schemas import (
    FieldCheck,
    PackageClaims,
    RegistryRecord,
    VerificationResult,
)


def normalize(value: str | None) -> str:
    """Normalize text for comparisons without changing the source values."""
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]+", "", value.casefold())


def normalize_registration_number(value: str | None) -> str:
    """Ignore spaces and punctuation in registration numbers."""
    return normalize(value)


def normalize_strength(value: str | None) -> str:
    """Normalize formatting while preserving the actual numbers and units."""
    if not value:
        return ""
    return re.sub(r"\s+", "", value.casefold()).replace(",", ".")


def compare_text(
    field: str,
    package_value: str | None,
    reference_value: str | None,
) -> FieldCheck:
    if not package_value or not reference_value:
        return FieldCheck(
            field=field,
            package_value=package_value,
            reference_value=reference_value,
            outcome="not_checked",
            explanation="One or both values are unavailable.",
        )

    if normalize(package_value) == normalize(reference_value):
        return FieldCheck(
            field=field,
            package_value=package_value,
            reference_value=reference_value,
            outcome="match",
            explanation="The normalized values match.",
        )

    return FieldCheck(
        field=field,
        package_value=package_value,
        reference_value=reference_value,
        outcome="mismatch",
        explanation="The normalized values differ.",
    )


def compare_product_name(
    package_value: str | None,
    reference_value: str | None,
) -> FieldCheck:
    if not package_value or not reference_value:
        return FieldCheck(
            field="product_name",
            package_value=package_value,
            reference_value=reference_value,
            outcome="not_checked",
            explanation="The product name is missing from one or both sources.",
        )

    similarity = ratio(
        normalize(package_value),
        normalize(reference_value),
    )

    if similarity >= 85:
        outcome = "match"
        explanation = (
            f"Names are highly similar (comparison score: {similarity:.0f}/100). "
            "This is a text-similarity score, not a probability of authenticity."
        )
    elif similarity >= 65:
        outcome = "mismatch"
        explanation = (
            f"Names have an ambiguous similarity score ({similarity:.0f}/100); "
            "manual review is needed."
        )
    else:
        outcome = "mismatch"
        explanation = (
            f"Names differ substantially (comparison score: {similarity:.0f}/100)."
        )

    return FieldCheck(
        field="product_name",
        package_value=package_value,
        reference_value=reference_value,
        outcome=outcome,
        explanation=explanation,
    )


def check_expiry(
    reference: RegistryRecord,
    today: date | None = None,
) -> FieldCheck:
    """Check the registration record's expiry date, not the medicine's expiry."""
    value = reference.registration_expiry_date

    if not value:
        return FieldCheck(
            field="registration_expiry_date",
            package_value=None,
            reference_value=None,
            outcome="not_checked",
            check_scope="registry_record_only",
            explanation="The reference record has no registration expiry date.",
        )

    try:
        expiry = date.fromisoformat(value)
    except ValueError:
        return FieldCheck(
            field="registration_expiry_date",
            package_value=None,
            reference_value=value,
            outcome="not_checked",
            check_scope="registry_record_only",
            explanation="The reference expiry date could not be parsed.",
        )

    current_date = today or date.today()
    if expiry < current_date:
        return FieldCheck(
            field="registration_expiry_date",
            package_value=None,
            reference_value=value,
            outcome="mismatch",
            check_scope="registry_record_only",
            explanation="The recorded registration expiry date has passed.",
        )

    return FieldCheck(
        field="registration_expiry_date",
        package_value=None,
        reference_value=value,
        outcome="match",
        explanation="The recorded registration expiry date has not passed.",
    )


def verify_package(
    claims: PackageClaims,
    registry_records: list[RegistryRecord],
    today: date | None = None,
) -> VerificationResult:
    """Compare package claims against local records; never certify authenticity."""

    supplied_number = normalize_registration_number(
        claims.nafdac_reg_number
    )

    if not supplied_number:
        return VerificationResult(
            status="verification_concern_detected",
            summary=(
                "No registration number was available to search. "
                "The product could not be checked against the registry."
            ),
            matched_record=False,
            limitations=["A registration number was not provided."],
        )

    reference = next(
        (
            record
            for record in registry_records
            if normalize_registration_number(record.nafdac_reg_number)
            == supplied_number
        ),
        None,
    )

    if reference is None:
        return VerificationResult(
            status="verification_concern_detected",
            summary=(
                "No matching registration number was found in the local dataset. "
                "This is not proof of counterfeiting."
            ),
            matched_record=False,
            limitations=[
                "The local dataset may be incomplete or outdated.",
                "A missing record is not proof of counterfeiting.",
            ],
        )

    checks = [
        compare_product_name(claims.product_name, reference.product_name),
        compare_text(
            "active_ingredients",
            claims.active_ingredients,
            reference.active_ingredients,
        ),
        compare_text("strength", claims.strength, reference.strength),
        compare_text(
            "dosage_form",
            claims.dosage_form,
            reference.dosage_form,
        ),
        compare_text(
            "manufacturer",
            claims.manufacturer,
            reference.manufacturer,
        ),
        check_expiry(reference, today=today),
    ]

    mismatches = {
        check.field
        for check in checks
        if check.outcome == "mismatch"
    }

    # A registration number pointing to a substantially different product,
    # or a material ingredient/strength mismatch, warrants high-risk review.
    high_risk_fields = {
        "active_ingredients",
        "strength",
    }

    product_check = next(
        check for check in checks if check.field == "product_name"
    )
    product_similarity = (
        ratio(
            normalize(product_check.package_value),
            normalize(product_check.reference_value),
        )
        if product_check.package_value and product_check.reference_value
        else None
    )

    high_risk = bool(mismatches & high_risk_fields) or (
        product_similarity is not None and product_similarity < 65
    )

    if high_risk:
        status = "high_risk_discrepancy_detected"
        summary = (
            "A material discrepancy was found between the package claims "
            "and the record associated with this registration number. "
            "Seek professional or regulatory verification."
        )
    elif mismatches or any(
        check.outcome == "not_checked" for check in checks
    ):
        status = "verification_concern_detected"
        summary = (
            "Some information differs or could not be checked. "
            "Review the field-level evidence before drawing conclusions."
        )
    else:
        status = "no_significant_discrepancy_detected"
        summary = (
            "No significant discrepancy was detected among the fields checked. "
            "This does not prove that the medicine or package is authentic."
        )

    if reference.registration_status:
        status_text = normalize(reference.registration_status)
        if status_text not in {"registered", "active", "valid"}:
            status = "high_risk_discrepancy_detected"
            summary = (
                "The reference record has a registration status that requires "
                "attention. Check the cited source and seek professional or "
                "regulatory verification."
            )

    limitations = [
        "A registration-number match does not prove package authenticity.",
        "The local snapshot may be incomplete or outdated.",
        "This check cannot establish the medicine's chemical composition "
        "or physical authenticity.",
    ]

    return VerificationResult(
        status=status,
        summary=summary,
        matched_record=True,
        checks=checks,
        source_name=reference.source_name,
        source_url=reference.source_url,
        dataset_label=reference.dataset_label,
        limitations=limitations,
    )
