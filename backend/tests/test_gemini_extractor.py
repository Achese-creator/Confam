import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.extraction import ExtractionProviderError
from app.gemini_extractor import GeminiPackageImageExtractor

from app.gemini_extractor import (
    EXTRACTION_PROMPT,
    EXTRACTION_PROMPT_MULTIPLE,
)


def install_fake_gemini(monkeypatch, response_text=None, should_raise=False):
    google = ModuleType("google")
    google.__path__ = []

    types_module = ModuleType("google.genai.types")

    class FakePart:
        @staticmethod
        def from_bytes(**kwargs):
            return kwargs

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            self.options = kwargs

    types_module.Part = FakePart
    types_module.GenerateContentConfig = FakeGenerateContentConfig

    class FakeModels:
        def generate_content(self, **kwargs):
            assert kwargs["model"] == "test-model"
            assert kwargs["config"].options["temperature"] == 0

            if should_raise:
                raise RuntimeError("simulated provider failure")

            return SimpleNamespace(text=response_text)

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "test-key"
            self.models = FakeModels()

    genai_module = ModuleType("google.genai")
    genai_module.Client = FakeClient
    genai_module.types = types_module
    genai_module.__path__ = []

    google.genai = genai_module

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_module)


def test_gemini_adapter_extracts_structured_claims(monkeypatch):
    payload = {
        "product_name": "Example Medicine",
        "active_ingredients": None,
        "strength": "500 mg",
        "dosage_form": "Tablet",
        "nafdac_reg_number": None,
        "manufacturer": "Example Pharma",
        "batch_number": "B123",
        "manufacturing_date": None,
        "expiry_date": "2028-06",
    }

    install_fake_gemini(
        monkeypatch,
        response_text=json.dumps(payload),
    )

    extractor = GeminiPackageImageExtractor(
        api_key="test-key",
        model_name="test-model",
    )

    result = extractor.extract(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
        "image/png",
    )

    assert result.claims.product_name == "Example Medicine"
    assert result.claims.strength == "500 mg"
    assert result.claims.batch_number == "B123"
    assert result.claims.nafdac_reg_number is None
    assert "nafdac_reg_number" in result.uncertain_fields
    assert result.model_name == "test-model"
    assert result.review_required is True


def test_gemini_adapter_rejects_malformed_json(monkeypatch):
    install_fake_gemini(monkeypatch, response_text="{not valid JSON")

    extractor = GeminiPackageImageExtractor(
        api_key="test-key",
        model_name="test-model",
    )

    with pytest.raises(
        ExtractionProviderError,
        match="unusable extraction data",
    ):
        extractor.extract(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
            "image/png",
        )


def test_gemini_adapter_hides_provider_error_details(monkeypatch):
    install_fake_gemini(
        monkeypatch,
        should_raise=True,
    )

    extractor = GeminiPackageImageExtractor(
        api_key="test-key",
        model_name="test-model",
    )

    with pytest.raises(
        ExtractionProviderError,
        match="Image extraction failed",
    ) as error:
        extractor.extract(
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,
            "image/png",
        )

    assert "simulated provider failure" not in str(error.value)


def install_capturing_fake_gemini(monkeypatch, response_text):
    """Install a fake SDK that records the exact model request."""
    calls = []
    google = ModuleType("google")
    google.__path__ = []
    types_module = ModuleType("google.genai.types")

    class FakePart:
        @staticmethod
        def from_bytes(**kwargs):
            return kwargs

    class FakeGenerateContentConfig:
        def __init__(self, **kwargs):
            self.options = kwargs

    class FakeModels:
        def generate_content(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(text=response_text)

    class FakeClient:
        def __init__(self, api_key):
            assert api_key == "test-key"
            self.models = FakeModels()

    types_module.Part = FakePart
    types_module.GenerateContentConfig = FakeGenerateContentConfig

    genai_module = ModuleType("google.genai")
    genai_module.Client = FakeClient
    genai_module.types = types_module
    genai_module.__path__ = []
    google.genai = genai_module

    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.genai", genai_module)
    monkeypatch.setitem(sys.modules, "google.genai.types", types_module)
    return calls


def test_single_image_uses_single_image_prompt(monkeypatch):
    from app.gemini_extractor import EXTRACTION_PROMPT

    calls = install_capturing_fake_gemini(
        monkeypatch,
        '{"product_name":"Example Medicine"}',
    )
    extractor = GeminiPackageImageExtractor(
        api_key="test-key",
        model_name="test-model",
    )

    extractor.extract(b"front-photo", "image/png")

    assert len(calls) == 1
    assert calls[0]["contents"][0] == EXTRACTION_PROMPT
    assert calls[0]["contents"][1:] == [
        {"data": b"front-photo", "mime_type": "image/png"}
    ]


def test_multiple_images_use_one_request_in_order(monkeypatch):
    from app.gemini_extractor import EXTRACTION_PROMPT_MULTIPLE

    calls = install_capturing_fake_gemini(
        monkeypatch,
        '{"product_name":"Example Medicine"}',
    )
    extractor = GeminiPackageImageExtractor(
        api_key="test-key",
        model_name="test-model",
    )

    extractor.extract_multiple([
        (b"front-photo", "image/png"),
        (b"back-photo", "image/jpeg"),
        (b"side-photo", "image/webp"),
    ])

    assert len(calls) == 1
    assert calls[0]["contents"][0] == EXTRACTION_PROMPT_MULTIPLE
    assert calls[0]["contents"][1:] == [
        {"data": b"front-photo", "mime_type": "image/png"},
        {"data": b"back-photo", "mime_type": "image/jpeg"},
        {"data": b"side-photo", "mime_type": "image/webp"},
    ]
    assert calls[0]["config"].options["temperature"] == 0


def test_multiple_image_extraction_rejects_empty_list():
    extractor = GeminiPackageImageExtractor(
        api_key="test-key",
        model_name="test-model",
    )

    with pytest.raises(
        ExtractionProviderError,
        match="at least one",
    ):
        extractor.extract_multiple([])
