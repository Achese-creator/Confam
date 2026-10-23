from app.gemini_briefing import deterministic_briefing
from app.schemas import VerificationResult


def make_result(**overrides):
    values = {
        "status": "verification_concern_detected",
        "summary": "Some information needs review.",
        "matched_record": False,
    }
    values.update(overrides)
    return VerificationResult(**values)


def test_not_searched_does_not_claim_no_alerts():
    result = make_result(
        registry_lookup_status="not_searched",
        alert_lookup_status="not_searched",
    )

    briefing = deterministic_briefing(result)

    assert "registry lookup was not performed" in briefing
    assert "safety-alert lookup was not performed" in briefing
    assert "found no matching alerts" not in briefing


def test_live_no_matches_is_distinguished_from_not_searched():
    result = make_result(
        registry_lookup_status="not_found",
        alert_lookup_status="live_no_matches",
    )

    briefing = deterministic_briefing(result)

    assert "registry lookup found no matching record" in briefing
    assert "live safety-alert lookup found no matching alerts" in briefing
    assert "does not prove that no alert exists" in briefing


def test_unavailable_lookups_do_not_claim_negative_results():
    result = make_result(
        registry_lookup_status="unavailable",
        alert_lookup_status="live_unavailable",
    )

    briefing = deterministic_briefing(result)

    assert "registry lookup was unavailable" in briefing
    assert "safety-alert lookup was unavailable" in briefing
    assert "found no matching alerts" not in briefing


def test_registration_expiry_is_not_package_expiry():
    result = make_result(
        registry_lookup_status="match",
        registry_registration_expiry_date="2027-12-31",
    )

    briefing = deterministic_briefing(result)

    assert "registration expiry as 2027-12-31" in briefing
    assert "not the printed package expiry date" in briefing
    assert "printed package expiry date (" not in briefing


def test_present_alert_records_are_reported():
    result = make_result(
        alert_lookup_status="live_matches_found",
        safety_alerts=[
            {
                "alert_id": "TEST-1",
                "title": "Example alert",
                "product_name": "Example product",
                "source_name": "Example source",
                "source_url": "https://example.com/alert",
                "alert_scope": "product",
                "explanation": "Synthetic test evidence.",
            }
        ],
    )

    briefing = deterministic_briefing(result)

    assert "1 safety-alert record(s)" in briefing
    assert "live safety-alert lookup found no matching alerts" not in briefing



def test_unsupported_briefing_claims_are_rejected():
    from app.gemini_briefing import briefing_has_unsupported_claims

    result = make_result(
        registry_lookup_status="not_searched",
        alert_lookup_status="not_searched",
    )

    assert briefing_has_unsupported_claims(
        "The registry lookup found no record and no alerts were found.",
        result,
    )


def test_unavailable_alert_lookup_cannot_be_described_as_negative():
    from app.gemini_briefing import briefing_has_unsupported_claims

    result = make_result(
        registry_lookup_status="match",
        alert_lookup_status="live_unavailable",
    )

    assert briefing_has_unsupported_claims(
        "No safety alerts were found for this product.",
        result,
    )


def test_package_expiry_claim_requires_an_expiry_check():
    from app.gemini_briefing import briefing_has_unsupported_claims

    result = make_result()

    assert briefing_has_unsupported_claims(
        "The printed package expiry date has not passed.",
        result,
    )


def test_supported_lookup_statuses_and_expiry_are_allowed():
    from app.gemini_briefing import briefing_has_unsupported_claims

    result = make_result(
        registry_lookup_status="match",
        alert_lookup_status="live_no_matches",
        registry_registration_expiry_date="2027-12-31",
    )

    briefing = (
        "A registry record was matched. The live safety-alert lookup found "
        "no matching alerts; this does not prove that no alert exists. "
        "The registry reports registration expiry as 2027-12-31."
    )

    assert not briefing_has_unsupported_claims(briefing, result)



def test_groq_unsupported_briefing_uses_deterministic_fallback(monkeypatch):
    from types import SimpleNamespace
    import groq
    from app.gemini_briefing import (
        deterministic_briefing,
        generate_evidence_briefing,
    )

    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    invalid_text = (
        "The registry lookup found no record and no safety alerts were found. "
        "These findings establish the product status."
    )

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=invalid_text)
                    )
                ]
            )

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = FakeChat()

    monkeypatch.setattr(groq, "Groq", FakeClient)

    result = make_result(
        registry_lookup_status="not_searched",
        alert_lookup_status="not_searched",
    )

    briefing = generate_evidence_briefing(result)

    assert briefing == deterministic_briefing(result)
    assert "registry lookup was not performed" in briefing
    assert "safety-alert lookup was not performed" in briefing


def test_groq_supported_briefing_is_accepted(monkeypatch):
    from types import SimpleNamespace
    import groq
    from app.gemini_briefing import generate_evidence_briefing

    monkeypatch.setenv("GROQ_API_KEY", "test-key")

    expected_text = (
        "Some information needs review. A registry lookup was not performed. "
        "A live safety-alert lookup was not performed, so alert presence "
        "cannot be determined from this result. These findings do not "
        "establish the authenticity or chemical composition of the medicine."
    )

    class FakeCompletions:
        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content=expected_text)
                    )
                ]
            )

    class FakeChat:
        def __init__(self):
            self.completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = FakeChat()

    monkeypatch.setattr(groq, "Groq", FakeClient)

    result = make_result(
        registry_lookup_status="not_searched",
        alert_lookup_status="not_searched",
    )

    briefing = generate_evidence_briefing(result)

    assert briefing == expected_text
