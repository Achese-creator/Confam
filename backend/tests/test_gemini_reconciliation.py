from app.gemini_reconciliation import reconcile_semantic_differences
from app.schemas import FieldCheck, VerificationResult


def make_result(checks):
    return VerificationResult(
        status="verification_concern_detected",
        summary="Some information differs or could not be checked.",
        matched_record=True,
        checks=checks,
    )


def test_no_ambiguous_checks_skips_llm(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = make_result([
        FieldCheck(
            field="strength",
            package_value="500 mg",
            reference_value="500 mg",
            outcome="match",
            explanation="Values match.",
        ),
    ])

    assert reconcile_semantic_differences(result) is None


def test_missing_api_key_returns_safe_fallback(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = make_result([
        FieldCheck(
            field="product_name",
            package_value="Example Tablets",
            reference_value="Example Medicine",
            outcome="mismatch",
            explanation="Names differ.",
        ),
    ])

    explanation = reconcile_semantic_differences(result)

    assert explanation is not None
    assert "not configured" in explanation
    assert "risk assessment remain unchanged" in explanation


def test_semantic_reconciliation_does_not_mutate_result(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    result = make_result([
        FieldCheck(
            field="product_name",
            package_value="Example Tablets",
            reference_value="Example Medicine",
            outcome="mismatch",
            explanation="Names differ.",
        ),
        FieldCheck(
            field="strength",
            package_value="250 mg",
            reference_value="500 mg",
            outcome="mismatch",
            explanation="The normalized values differ.",
        ),
    ])

    original_status = result.status
    original_checks = [check.model_dump() for check in result.checks]

    reconcile_semantic_differences(result)

    assert result.status == original_status
    assert [check.model_dump() for check in result.checks] == original_checks


def test_successful_llm_reconciliation_preserves_deterministic_findings(
    monkeypatch,
):
    from types import SimpleNamespace
    from google import genai

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    expected_text = (
        "The product names may differ in wording, but the evidence "
        "does not establish that they refer to the same product."
    )

    class FakeModels:
        def generate_content(self, **kwargs):
            return SimpleNamespace(text=expected_text)

    class FakeClient:
        def __init__(self, **kwargs):
            self.models = FakeModels()

    monkeypatch.setattr(genai, "Client", FakeClient)

    result = make_result([
        FieldCheck(
            field="product_name",
            package_value="Example Tablets",
            reference_value="Example Medicine",
            outcome="mismatch",
            explanation="Names differ.",
        ),
        FieldCheck(
            field="strength",
            package_value="250 mg",
            reference_value="500 mg",
            outcome="mismatch",
            explanation="The normalized values differ.",
        ),
    ])

    original_status = result.status
    original_checks = [check.model_dump() for check in result.checks]

    explanation = reconcile_semantic_differences(result)

    assert explanation is not None
    assert expected_text in explanation
    assert "risk status were not changed" in explanation
    assert result.status == original_status
    assert [check.model_dump() for check in result.checks] == original_checks
