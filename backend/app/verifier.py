import re
from calendar import monthrange
from datetime import date

from rapidfuzz.fuzz import ratio

from app.schemas import (
    FieldCheck,
    PackageClaims,
    RegistryRecord,
     SafetyAlertEvidence,
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



def check_registration_status(reference: RegistryRecord) -> FieldCheck:
    """Report the status recorded in the registry; do not infer package authenticity."""
    value = reference.registration_status

    if not value:
        return FieldCheck(
            field="registration_status",
            package_value=None,
            reference_value=None,
            outcome="not_checked",
            check_scope="registry_record_only",
            explanation="The reference record does not provide a registration status.",
        )

    status_text = normalize(value)

    if status_text in {"registered", "active", "valid"}:
        outcome = "match"
        explanation = (
            "The registry record reports an active/valid registration status. "
            "This is registry-only information and does not prove package authenticity."
        )
    elif status_text in {
        "suspended",
        "revoked",
        "cancelled",
        "withdrawn",
        "inactive",
        "expired",
        "deregistered",
    }:
        outcome = "mismatch"
        explanation = (
            "The registry record reports a known inactive or adverse status. "
            "Check the cited source and seek professional or regulatory verification."
        )
    else:
        outcome = "not_checked"
        explanation = (
            "The registry record contains an unrecognized status value. "
            "Its meaning cannot be safely inferred; verify it against the cited source."
        )

    return FieldCheck(
        field="registration_status",
        package_value=None,
        reference_value=value,
        outcome=outcome,
        check_scope="registry_record_only",
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
        outcome="not_checked",
        check_scope="registry_record_only",
        explanation=(
            "The registry record lists this registration expiry date as "
            "not yet passed. This is registry-only information; no "
            "package-label comparison was performed."
        ),
    )


def classify_alert_batch_expiry(
    value: str | None,
    today: date | None = None,
) -> str:
    """Classify the reported batch expiry without judging the alert's validity.

    Accept ISO dates (YYYY-MM-DD) and month/year values (MM/YYYY).
    Month/year expiry is treated as valid through the final day of that month.
    """
    if not value:
        return "unknown"

    current_date = today or date.today()

    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            expiry = date.fromisoformat(value)
        elif re.fullmatch(r"\d{2}/\d{4}", value):
            month, year = map(int, value.split("/"))
            expiry = date(year, month, monthrange(year, month)[1])
        else:
            return "unknown"
    except ValueError:
        return "unknown"

    return "expired" if expiry < current_date else "not_expired"


def find_relevant_alerts(
    claims: PackageClaims,
    alert_records: list[dict],
    today: date | None = None,
) -> list[SafetyAlertEvidence]:
    """Find alerts supported by a product-name match plus another identifier."""
    relevant_alerts = []

    package_name = normalize(claims.product_name)
    package_number = normalize_registration_number(claims.nafdac_reg_number)
    package_batch = normalize(claims.batch_number)

    if not package_name:
        return relevant_alerts

    for alert in alert_records:
        alert_name = normalize(alert.get("product_name"))
        alert_number = normalize_registration_number(
            alert.get("reported_registration_number")
        )
        alert_batch = normalize(alert.get("batch_number"))

        name_matches = (
            bool(alert_name)
            and ratio(package_name, alert_name) >= 85
        )
        number_matches = (
            bool(package_number)
            and bool(alert_number)
            and package_number == alert_number
        )
        batch_matches = (
            bool(package_batch)
            and bool(alert_batch)
            and package_batch == alert_batch
        )

        if name_matches and (number_matches or batch_matches):
            relevant_alerts.append(
                SafetyAlertEvidence(
                    alert_id=alert["alert_id"],
                    title=alert["title"],
                    product_name=alert["product_name"],
                    source_name=alert["source_name"],
                    source_url=alert["source_url"],
                    published_at=alert.get("published_at"),
                    alert_scope=alert.get("alert_scope"),
                    batch_expiry_date=alert.get("expiry_date"),
                    batch_expiry_status=classify_alert_batch_expiry(
                        alert.get("expiry_date"),
                        today=today,
                    ),
                    batch_match_status=(
                        "match"
                        if batch_matches
                        else (
                            "different"
                            if package_batch and alert_batch
                            else "not_checked"
                        )
                    ),
                    explanation=(
                        (
                            "The product name and reported batch number match "
                            "this safety-alert record. This is evidence "
                            "requiring review, not proof that the package's "
                            "contents are affected."
                        )
                        if batch_matches
                        else (
                            "The product name and registration number match "
                            "this safety-alert record, but the supplied batch "
                            "number differs from the reported batch. Review "
                            "the original notice; this record does not establish "
                            "that the supplied batch is affected."
                        )
                        if number_matches and package_batch and alert_batch
                        else (
                            "The product name and at least one additional "
                            "identifier match this safety-alert record, but an "
                            "exact batch comparison could not be completed. "
                            "This is evidence requiring review, not proof that "
                            "this individual package is affected."
                        )
                    ),
                )
            )

    return relevant_alerts


def _alert_warrants_high_risk(
    safety_alerts: list[SafetyAlertEvidence],
) -> bool:
    """Escalate for an exact reported-batch match, not product-level relevance alone."""
    return any(
        alert.batch_match_status == "match"
        for alert in safety_alerts
    )


def verify_package(
    claims: PackageClaims,
    registry_records: list[RegistryRecord],
    today: date | None = None,
    alert_records: list[dict] | None = None,
) -> VerificationResult:
    """Compare package claims against local records; never certify authenticity."""

    safety_alerts = find_relevant_alerts(
        claims,
        alert_records or [],
        today=today,
    )

    supplied_number = normalize_registration_number(
        claims.nafdac_reg_number
    )

    if not supplied_number:
        return VerificationResult(
            status=(
                "high_risk_discrepancy_detected"
                if _alert_warrants_high_risk(safety_alerts)
                else "verification_concern_detected"
            ),
            summary=(
                (
                    "A reported-batch match was found in a relevant safety "
                    "alert. Seek professional or regulatory verification."
                    if _alert_warrants_high_risk(safety_alerts)
                    else
                    "A product-level safety alert is relevant, but the "
                    "reported batch has not been confirmed. Review the notice "
                    "and seek professional or regulatory verification."
                )
                if safety_alerts
                else (
                    "No registration number was available to search. "
                    "The product could not be checked against the registry."
                )
            ),
            matched_record=False,
            safety_alerts=safety_alerts,
            limitations=[
                "A safety-alert match is not proof that this individual package is affected.",
                "A registration number was not provided.",
            ],
        )

    matching_records = [
        record
        for record in registry_records
        if normalize_registration_number(record.nafdac_reg_number)
        == supplied_number
    ]

    if len(matching_records) > 1:
        return VerificationResult(
            status=(
                "high_risk_discrepancy_detected"
                if _alert_warrants_high_risk(safety_alerts)
                else "verification_concern_detected"
            ),
            summary=(
                "Multiple registry records share this normalized registration "
                "number. The reference is ambiguous and needs manual review."
            ),
            matched_record=False,
            safety_alerts=safety_alerts,
            limitations=[
                "The local dataset contains duplicate normalized registration numbers.",
                "No single registry record was selected because the evidence is ambiguous.",
                "A safety-alert match is not proof that this individual package is affected.",
            ],
        )

    reference = matching_records[0] if matching_records else None

    if reference is None:
        return VerificationResult(
            status=(
                "high_risk_discrepancy_detected"
                if _alert_warrants_high_risk(safety_alerts)
                else "verification_concern_detected"
            ),
            summary=(
                (
                    "A reported-batch match was found in a relevant safety "
                    "alert. Seek professional or regulatory verification."
                    if _alert_warrants_high_risk(safety_alerts)
                    else
                    "A product-level safety alert is relevant, but the "
                    "reported batch has not been confirmed. Review the notice "
                    "and seek professional or regulatory verification."
                )
                if safety_alerts
                else (
                    "No matching registration number was found in the local dataset. "
                    "This is not proof of counterfeiting."
                )
            ),
            matched_record=False,
            safety_alerts=safety_alerts,
            limitations=[
                "The local dataset may be incomplete or outdated.",
                "A safety-alert match is not proof that this individual package is affected.",
                "A missing registry record is not proof of counterfeiting.",
            ],
        )

    checks = [
        check_registration_status(reference),
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
    elif (
        mismatches
        or any(
            check.outcome == "not_checked"
            and check.check_scope != "registry_record_only"
            for check in checks
        )
        or any(
            check.field == "registration_status"
            and check.outcome == "not_checked"
            for check in checks
        )
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
        if status_text in {
            "suspended",
            "revoked",
            "cancelled",
            "withdrawn",
            "inactive",
            "expired",
            "deregistered",
        }:
            status = "high_risk_discrepancy_detected"
            summary = (
                "The reference record reports a known inactive or adverse "
                "registration status. Check the cited source and seek "
                "professional or regulatory verification."
            )

    if safety_alerts:
        if _alert_warrants_high_risk(safety_alerts):
            status = "high_risk_discrepancy_detected"
            summary = (
                "A reported batch number matches a relevant safety alert. "
                "Review the original notice and seek professional or "
                "regulatory verification. This does not prove the contents "
                "of the individual package."
            )
        elif status == "no_significant_discrepancy_detected":
            status = "verification_concern_detected"
            summary = (
                "A product-level safety alert is relevant, but the reported "
                "batch has not been confirmed. Review the original notice. "
                "This does not prove that the individual package is affected."
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
        safety_alerts=safety_alerts,
        source_name=reference.source_name,
        source_url=reference.source_url,
        dataset_label=reference.dataset_label,
        limitations=limitations,
    )
