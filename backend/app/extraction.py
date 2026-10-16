"""Provider-independent contract for medicine-package image extraction."""

from typing import Protocol

from app.schemas import PackageClaims, PackageExtractionResult


CLAIM_FIELDS = set(PackageClaims.model_fields)


class ExtractionProviderError(RuntimeError):
    """Raised when an extraction provider cannot return usable claims."""


class PackageImageExtractor(Protocol):
    """Interface implemented by Gemini or another vision-capable provider."""

    def extract(
        self,
        image_bytes: bytes,
        mime_type: str,
    ) -> PackageExtractionResult:
        """Extract visible package claims without deciding authenticity."""
        ...


def validate_claims_payload(payload: object) -> PackageClaims:
    """Validate untrusted structured output from a vision provider.

    Missing claim fields are allowed and default to None. Unknown keys and
    non-string field values are rejected rather than silently accepted.
    """
    if not isinstance(payload, dict):
        raise ValueError("Extraction output must be a JSON object.")

    unknown = set(payload) - CLAIM_FIELDS
    if unknown:
        raise ValueError(
            f"Unexpected extraction fields: {sorted(unknown)}"
        )

    for field, value in payload.items():
        if value is not None and not isinstance(value, str):
            raise ValueError(
                f"Extraction field {field!r} must be a string or null."
            )

    return PackageClaims.model_validate(payload)


def build_extraction_result(
    payload: object,
    *,
    model_name: str,
    uncertain_fields: list[str] | None = None,
) -> PackageExtractionResult:
    """Build a review-required result from validated provider output."""
    if not model_name.strip():
        raise ValueError("model_name must not be empty.")

    uncertain = uncertain_fields or []
    unknown_uncertain = set(uncertain) - CLAIM_FIELDS
    if unknown_uncertain:
        raise ValueError(
            f"Unknown uncertain claim fields: {sorted(unknown_uncertain)}"
        )

    return PackageExtractionResult(
        claims=validate_claims_payload(payload),
        uncertain_fields=uncertain,
        model_name=model_name,
        review_required=True,
    )
