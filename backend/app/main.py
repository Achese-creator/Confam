import json
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

from app.schemas import (
    PackageClaims,
    RegistryRecord,
    SafetyAlertRecord,
    VerificationResult,
    PackageExtractionResult,
)
from app.verifier import verify_package
from app.extraction import ExtractionProviderError
from app.gemini_extractor import GeminiPackageImageExtractor
from app.gemini_briefing import generate_evidence_briefing
from app.image_validation import (
    InvalidPackageImage,
    MAX_IMAGE_BYTES,
    validate_package_image,
)
from app.dataset_validation import (
    validate_alert_snapshot,
    validate_registry_snapshot,
)


DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def load_json(filename: str) -> dict:
    path = DATA_DIR / filename

    with path.open("r", encoding="utf-8") as file:
        return json.load(file)


# Load curated datasets from version-controlled JSON snapshots.
REGISTRY_DATA = load_json("registry.json")
ALERT_DATA = load_json("alerts.json")

REGISTRY_RECORDS = validate_registry_snapshot(REGISTRY_DATA)
VALIDATED_ALERT_RECORDS = validate_alert_snapshot(ALERT_DATA)

# Keep the existing API response shape while using validated alert records.
ALERT_RECORDS = [
    record.model_dump() for record in VALIDATED_ALERT_RECORDS
]


app = FastAPI(
    title="CONFAM API",
    description=(
        "AI-assisted medicine-package and regulatory evidence checker. "
        "Results identify discrepancies; they do not certify authenticity."
    ),
    version="0.2.0",
)


class VerifyRequest(BaseModel):
    claims: PackageClaims


class RegistryMetadata(BaseModel):
    dataset_label: str
    record_count: int
    source_note: str


class AlertRecordsResponse(BaseModel):
    records: list[SafetyAlertRecord]


class AlertMetadata(BaseModel):
    dataset_name: str
    record_count: int
    source_note: str


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "confam-api"}


@app.get("/api/v1/registry", response_model=RegistryMetadata)
def registry_metadata():
    labels = {record.dataset_label for record in REGISTRY_RECORDS}

    dataset_label = (
        next(iter(labels))
        if len(labels) == 1
        else "mixed_sources"
    )

    return RegistryMetadata(
        dataset_label=dataset_label,
        record_count=len(REGISTRY_RECORDS),
        source_note=(
            f"Versioned snapshot retrieved on "
            f"{REGISTRY_DATA['retrieved_at']}. "
            "The dataset may be incomplete or outdated. "
            "A matching record does not prove package authenticity."
        ),
    )


@app.get("/api/v1/alerts", response_model=AlertMetadata)
def alert_metadata():
    return AlertMetadata(
        dataset_name=ALERT_DATA["dataset_name"],
        record_count=len(ALERT_RECORDS),
        source_note=(
            f"Curated safety-alert snapshot retrieved on "
            f"{ALERT_DATA['retrieved_at']}. "
            "Each alert applies only within its stated scope; "
            "an alert is not a universal verdict on every package "
            "with a matching product name."
        ),
    )


@app.get("/api/v1/alerts/records")
def list_alert_records():
    return {"records": ALERT_RECORDS}


@app.post("/api/v1/verify", response_model=VerificationResult)
def verify(request: VerifyRequest):
    result = verify_package(
        request.claims,
        REGISTRY_RECORDS,
        alert_records=ALERT_RECORDS,
    )
    result = result.model_copy(
        update={
            "registry_snapshot_retrieved_at": REGISTRY_DATA["retrieved_at"],
            "alerts_snapshot_retrieved_at": ALERT_DATA["retrieved_at"],
        }
    )
    return result.model_copy(
        update={"briefing": generate_evidence_briefing(result)}
    )



@app.post("/api/v1/extract-multiple", response_model=PackageExtractionResult)
async def extract_multiple_package_images(
    images: list[UploadFile] = File(
        ...,
        description="One to six views of the same medicine package",
    ),
):
    """Extract visible claims from multiple package views; this does not verify authenticity."""
    try:
        if not 1 <= len(images) <= 6:
            raise HTTPException(
                status_code=413 if len(images) > 6 else 422,
                detail="Upload between one and six package photos.",
            )

        validated_images: list[tuple[bytes, str]] = []

        for index, image in enumerate(images, start=1):
            mime_type = (image.content_type or "").lower()
            image_bytes = await image.read(MAX_IMAGE_BYTES + 1)

            try:
                mime_type = validate_package_image(image_bytes, mime_type)
            except InvalidPackageImage as exc:
                message = f"Photo {index}: {exc}"
                status_code = 413 if "5 MB" in str(exc) else 415
                raise HTTPException(
                    status_code=status_code,
                    detail=message,
                ) from exc

            validated_images.append((image_bytes, mime_type))

        extractor = GeminiPackageImageExtractor()
        return extractor.extract_multiple(validated_images)

    except HTTPException:
        raise
    except ExtractionProviderError as exc:
        status_code = (
            503 if "not configured" in str(exc).lower()
            else 502
        )
        raise HTTPException(
            status_code=status_code,
            detail=str(exc),
        ) from exc
    finally:
        for image in images:
            await image.close()


@app.post("/api/v1/extract", response_model=PackageExtractionResult)
async def extract_package_image(
    image: UploadFile = File(..., description="Medicine package image"),
):
    """Extract visible package claims; this does not verify authenticity."""
    try:
        mime_type = (image.content_type or "").lower()
        image_bytes = await image.read(MAX_IMAGE_BYTES + 1)

        try:
            mime_type = validate_package_image(image_bytes, mime_type)
        except InvalidPackageImage as exc:
            message = str(exc)
            status_code = 413 if "5 MB" in message else 415
            raise HTTPException(
                status_code=status_code,
                detail=message,
            ) from exc

        extractor = GeminiPackageImageExtractor()
        return extractor.extract(image_bytes, mime_type)

    except ExtractionProviderError as exc:
        status_code = (
            503 if "not configured" in str(exc).lower()
            else 502
        )
        raise HTTPException(
            status_code=status_code,
            detail=str(exc),
        ) from exc
    finally:
        await image.close()
