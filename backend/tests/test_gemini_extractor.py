import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.extraction import ExtractionProviderError
from app.gemini_extractor import (
    GeminiPackageImageExtractor,
    EXTRACTION_PROMPT,
    EXTRACTION_PROMPT_MULTIPLE,
)

VALID_PAYLOAD = {
    "product_name": "Example Medicine",
    "active_ingredients": "Paracetamol",
    "strength": "500 mg",
    "dosage_form": "Tablet",
    "nafdac_reg_number": "A1-1234",
    "marketer": "Example Pharma",
    "batch_number": "B123",
    "manufacturing_date": None,
    "expiry_date": "2028-06",
}


def install_fake_groq(monkeypatch, responses, should_raise=False):
    calls = []
    groq_module = ModuleType("groq")

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if should_raise:
                raise RuntimeError("simulated provider failure")
            value = responses.pop(0)
            return SimpleNamespace(
                choices=[SimpleNamespace(
                    message=SimpleNamespace(content=value)
                )]
            )

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "test-key"
            self.chat = SimpleNamespace(
                completions=FakeCompletions()
            )

    groq_module.Groq = FakeClient
    monkeypatch.setitem(sys.modules, "groq", groq_module)
    return calls


def make_extractor():
    return GeminiPackageImageExtractor(
        api_key="test-key",
        model_name="test-model",
    )


def test_groq_adapter_extracts_structured_claims(monkeypatch):
    calls = install_fake_groq(monkeypatch, [json.dumps(VALID_PAYLOAD)])
    result = make_extractor().extract(b"front-photo", "image/png")

    assert result.claims.product_name == "Example Medicine"
    assert result.claims.marketer == "Example Pharma"
    assert result.claims.strength == "500 mg"
    assert result.claims.nafdac_reg_number == "A1-1234"
    assert result.review_required is True
    assert result.model_name == "test-model"
    assert calls[0]["model"] == "test-model"
    assert calls[0]["temperature"] == 0
    assert calls[0]["response_format"] == {"type": "json_object"}


def test_single_image_uses_single_image_prompt_and_data_url(monkeypatch):
    calls = install_fake_groq(monkeypatch, [json.dumps(VALID_PAYLOAD)])
    make_extractor().extract(b"front-photo", "image/png")

    content = calls[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": EXTRACTION_PROMPT}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_multiple_images_use_multiple_image_prompt(monkeypatch):
    calls = install_fake_groq(monkeypatch, [json.dumps(VALID_PAYLOAD)])
    make_extractor().extract_multiple([
        (b"front", "image/png"),
        (b"back", "image/jpeg"),
    ])

    content = calls[0]["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": EXTRACTION_PROMPT_MULTIPLE}
    assert len(content) == 3


def test_six_images_are_sent_in_two_batches(monkeypatch):
    calls = install_fake_groq(
        monkeypatch,
        [json.dumps(VALID_PAYLOAD), json.dumps(VALID_PAYLOAD)],
    )
    result = make_extractor().extract_multiple([
        (f"photo-{i}".encode(), "image/jpeg") for i in range(6)
    ])

    assert len(calls) == 2
    assert [len(call["messages"][0]["content"]) - 1 for call in calls] == [3, 3]
    assert result.claims.product_name == "Example Medicine"
    assert any("separate batches" in item for item in result.limitations)


def test_conflicting_batch_values_are_left_blank(monkeypatch):
    first = {**VALID_PAYLOAD, "strength": "500 mg"}
    second = {**VALID_PAYLOAD, "strength": "250 mg"}
    install_fake_groq(monkeypatch, [json.dumps(first), json.dumps(second)])

    result = make_extractor().extract_multiple([
        (b"front", "image/jpeg"),
        (b"back", "image/jpeg"),
        (b"side", "image/jpeg"),
        (b"top", "image/jpeg"),
    ])

    assert result.claims.strength is None
    assert "strength" in result.uncertain_fields


def test_malformed_json_is_rejected(monkeypatch):
    install_fake_groq(monkeypatch, ["{not valid JSON"])
    with pytest.raises(ExtractionProviderError, match="unusable extraction data"):
        make_extractor().extract(b"photo", "image/png")


def test_provider_details_are_not_returned_to_user(monkeypatch):
    install_fake_groq(monkeypatch, [], should_raise=True)
    with pytest.raises(ExtractionProviderError, match="Image extraction failed") as error:
        make_extractor().extract(b"photo", "image/png")
    assert "simulated provider failure" not in str(error.value)


def test_empty_image_list_is_rejected():
    with pytest.raises(ExtractionProviderError, match="at least one"):
        make_extractor().extract_multiple([])


def test_more_than_six_images_is_rejected():
    with pytest.raises(ExtractionProviderError, match="maximum of six"):
        make_extractor().extract_multiple([
            (b"photo", "image/jpeg") for _ in range(7)
        ])


def test_missing_groq_key_is_reported(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    extractor = GeminiPackageImageExtractor(api_key=None, model_name="test-model")
    with pytest.raises(ExtractionProviderError, match="GROQ_API_KEY"):
        extractor.extract(b"photo", "image/png")
