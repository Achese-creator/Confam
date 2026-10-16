import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from app.extraction import ExtractionProviderError
from app.gemini_extractor import GeminiPackageImageExtractor


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
