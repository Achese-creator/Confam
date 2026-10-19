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
from app.greenbook_client import GreenbookClient, GREENBOOK_URL
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
    return RegistryMetadata(
        dataset_label="live_lookup",
        record_count=0,
        source_note=(
            "CONFAM queries the NAFDAC Greenbook for each verification "
            "that includes a registration number. No complete local copy "
            "of the Greenbook is treated as the current registry source. "
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
    claims = request.claims
    registration_number = (claims.nafdac_reg_number or "").strip()

    lookup_status = "not_searched"
    live_record = None
    registry_retrieved_at = None
    lookup_message = None

    if registration_number:
        lookup = GreenbookClient().lookup(registration_number)
        lookup_status = lookup.status
        lookup_message = lookup.message

        if lookup.status == "match" and lookup.record is not None:
            live_record = RegistryRecord.model_validate(lookup.record)
            registry_retrieved_at = live_record.retrieved_at

    # Registry comparisons use only an exact live Greenbook match.
    # The local registry snapshot is not used as a fallback.
    result = verify_package(
        claims,
        [live_record] if live_record is not None else [],
        alert_records=ALERT_RECORDS,
    )

    updates = {
        "registry_snapshot_retrieved_at": None,
        "registry_lookup_status": lookup_status,
        "registry_retrieved_at": registry_retrieved_at,
        "alerts_snapshot_retrieved_at": ALERT_DATA["retrieved_at"],
    }

    if live_record is not None:
        updates.update({
            "source_name": live_record.source_name,
            "source_url": live_record.source_url,
            "dataset_label": "live_source_record",
        })
    elif lookup_status == "not_found":
        updates.update({
            "source_name": "NAFDAC Greenbook",
            "source_url": GREENBOOK_URL,
            "dataset_label": "live_lookup",
            "summary": (
                "No exact match for this registration number was found "
                "in the live NAFDAC Greenbook lookup. This is not proof "
                "of counterfeiting."
            ),
            "limitations": list(result.limitations) + [
                "No exact live Greenbook registration match was available "
                "for comparison."
            ],
        })
        if result.status != "high_risk_discrepancy_detected":
            updates["status"] = "verification_concern_detected"
    elif lookup_status == "unavailable":
        updates.update({
            "source_name": "NAFDAC Greenbook",
            "source_url": GREENBOOK_URL,
            "dataset_label": "live_lookup",
            "summary": (
                "The live NAFDAC Greenbook could not be reached, so the "
                "registration could not be checked. This is not evidence "
                "that the medicine is counterfeit."
            ),
            "limitations": list(result.limitations) + [
                "Live registry comparison was unavailable; no local "
                "registry fallback was used."
            ],
        })
        if result.status != "high_risk_discrepancy_detected":
            updates["status"] = "verification_concern_detected"
    else:
        updates["limitations"] = list(result.limitations) + [
            "No registration number was supplied, so no live Greenbook "
            "lookup was performed."
        ]

    if lookup_message and lookup_status in {"not_found", "unavailable"}:
        updates["limitations"] = list(updates.get(
            "limitations", result.limitations
        )) + [lookup_message]

    result = result.model_copy(update=updates)
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
