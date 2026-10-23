from fastapi.testclient import TestClient

from app.main import app
from app.gemini_extractor import GeminiPackageImageExtractor
from app.schemas import PackageClaims, PackageExtractionResult
from io import BytesIO
from PIL import Image


def valid_png_bytes():
    buffer = BytesIO()
    Image.new("RGB", (1, 1), color="white").save(buffer, format="PNG")
    return buffer.getvalue()


client = TestClient(app)


def test_extract_rejects_empty_image():
    response = client.post(
        "/api/v1/extract",
        files={"image": ("empty.png", b"", "image/png")},
    )
    assert response.status_code == 415


def test_extract_rejects_mismatched_image_signature():
    response = client.post(
        "/api/v1/extract",
        files={"image": ("fake.png", b"not a real png", "image/png")},
    )
    assert response.status_code == 415


def test_extract_rejects_unsupported_mime_type():
    response = client.post(
        "/api/v1/extract",
        files={"image": ("file.gif", b"GIF89a", "image/gif")},
    )
    assert response.status_code == 415


def test_extract_returns_configuration_error_without_api_key(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    response = client.post(
        "/api/v1/extract",
        files={
            "image": (
                "package.png",
                valid_png_bytes(),
                "image/png",
            )
        },
    )

    assert response.status_code == 503
    assert "GROQ_API_KEY" in response.json()["detail"]


def test_extract_endpoint_uses_extractor_result(monkeypatch):
    class MockExtractor:
        def __init__(self):
            pass

        def extract(self, image_bytes, mime_type):
            assert mime_type == "image/png"
            assert image_bytes.startswith(b"\x89PNG\r\n\x1a\n")
            return PackageExtractionResult(
                claims=PackageClaims(product_name="Example Medicine"),
                uncertain_fields=["expiry_date"],
                model_name="mock-vision-model",
            )

    monkeypatch.setattr(
        "app.main.GeminiPackageImageExtractor",
        MockExtractor,
    )

    response = client.post(
        "/api/v1/extract",
        files={
            "image": (
                "package.png",
                valid_png_bytes(),
                "image/png",
            )
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["claims"]["product_name"] == "Example Medicine"
    assert data["claims"]["expiry_date"] is None
    assert data["uncertain_fields"] == ["expiry_date"]
    assert data["review_required"] is True


def test_extract_multiple_accepts_several_package_views(monkeypatch):
    class MockExtractor:
        def __init__(self):
            pass

        def extract_multiple(self, images):
            assert len(images) == 2
            assert all(mime == "image/png" for _, mime in images)
            assert all(data.startswith(b"\x89PNG\r\n\x1a\n") for data, _ in images)
            return PackageExtractionResult(
                claims=PackageClaims(product_name="Example Medicine"),
                uncertain_fields=["expiry_date"],
                model_name="mock-vision-model",
            )

    monkeypatch.setattr(
        "app.main.GeminiPackageImageExtractor",
        MockExtractor,
    )

    photo = valid_png_bytes()
    response = client.post(
        "/api/v1/extract-multiple",
        files=[
            ("images", ("front.png", photo, "image/png")),
            ("images", ("back.png", photo, "image/png")),
        ],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["claims"]["product_name"] == "Example Medicine"
    assert data["uncertain_fields"] == ["expiry_date"]
    assert data["review_required"] is True


def test_extract_multiple_rejects_more_than_six_photos():
    photo = valid_png_bytes()
    response = client.post(
        "/api/v1/extract-multiple",
        files=[
            ("images", (f"photo-{index}.png", photo, "image/png"))
            for index in range(7)
        ],
    )

    assert response.status_code == 413
    assert "six" in response.json()["detail"].lower()


def test_extract_multiple_rejects_invalid_photo():
    photo = valid_png_bytes()
    response = client.post(
        "/api/v1/extract-multiple",
        files=[
            ("images", ("front.png", photo, "image/png")),
            ("images", ("bad.png", b"not an image", "image/png")),
        ],
    )

    assert response.status_code == 415
    assert "Photo 2" in response.json()["detail"]

