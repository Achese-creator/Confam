import pytest
from pydantic import ValidationError

from app.extraction import (
    build_extraction_result,
    validate_claims_payload,
)
from app.schemas import PackageExtractionResult


def test_partial_extraction_defaults_missing_claims_to_none():
    claims = validate_claims_payload({
        "product_name": "Example Medicine",
        "nafdac_reg_number": None,
    })

    assert claims.product_name == "Example Medicine"
    assert claims.nafdac_reg_number is None
    assert claims.expiry_date is None


def test_extraction_rejects_unknown_claim_fields():
    with pytest.raises(ValueError, match="Unexpected extraction fields"):
        validate_claims_payload({
            "product_name": "Example Medicine",
            "authenticity_verdict": "genuine",
        })


@pytest.mark.parametrize("bad_value", [123, True, [], {}])
def test_extraction_rejects_non_string_claim_values(bad_value):
    with pytest.raises(ValueError, match="must be a string or null"):
        validate_claims_payload({"product_name": bad_value})


def test_extraction_result_always_requires_review():
    result = build_extraction_result(
        {"product_name": "Example Medicine"},
        model_name="mock-vision-model",
        uncertain_fields=["expiry_date"],
    )

    assert result.review_required is True
    assert result.claims.product_name == "Example Medicine"
    assert result.claims.expiry_date is None
    assert "expiry_date" in result.uncertain_fields
    assert result.limitations


def test_extraction_rejects_unknown_uncertain_field():
    with pytest.raises(ValueError, match="Unknown uncertain claim fields"):
        build_extraction_result(
            {"product_name": "Example Medicine"},
            model_name="mock-vision-model",
            uncertain_fields=["authenticity"],
        )


def test_extraction_requires_model_name():
    with pytest.raises(ValueError, match="model_name must not be empty"):
        build_extraction_result(
            {"product_name": "Example Medicine"},
            model_name=" ",
        )


def test_review_required_cannot_be_disabled():
    with pytest.raises(ValidationError):
        PackageExtractionResult(
            claims={"product_name": "Example Medicine"},
            model_name="mock-vision-model",
            review_required=False,
        )
