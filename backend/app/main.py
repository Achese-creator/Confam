import json
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel, Field

from app.schemas import (
    PackageClaims,
    RegistryRecord,
    SafetyAlertRecord,
    VerificationResult,
    PackageExtractionResult,
)
from app.verifier import verify_package
from app.greenbook_client import GreenbookClient, GREENBOOK_URL
from app.safety_alert_client import NAFDACSafetyAlertClient
from app.extraction import ExtractionProviderError
from app.gemini_extractor import GeminiPackageImageExtractor
from app.gemini_briefing import generate_evidence_briefing
from app.gemini_reconciliation import reconcile_semantic_differences
from app.gemini_mcp_agent import run_gemini_mcp_agent_with_trace
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



class AgentQuestionRequest(BaseModel):
    question: str


class AgentQuestionResponse(BaseModel):
    answer: str
    tools_called: list[str] = Field(default_factory=list)
    tool_call_count: int = 0
    tool_errors: int = 0
    tool_errors_by_name: list[str] = Field(default_factory=list)


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



@app.post(
    "/api/v1/agent/ask",
    response_model=AgentQuestionResponse,
)
def ask_evidence_agent(request: AgentQuestionRequest):
    """Answer evidence questions using Gemini and read-only MCP tools."""
    question = request.question.strip()
    if not question:
        raise HTTPException(
            status_code=422,
            detail="Provide a non-empty question.",
        )
    result = run_gemini_mcp_agent_with_trace(question)
    return AgentQuestionResponse(**result)


@app.get("/api/v1/registry", response_model=RegistryMetadata)
def registry_metadata():
    return RegistryMetadata(
        dataset_label="live_lookup_with_curated_fallback",
        record_count=len(REGISTRY_RECORDS),
        source_note=(
            "CONFAM checks the live NAFDAC Greenbook first when a "
            "registration number is supplied. If that lookup returns no "
            "exact match or the source is unavailable, CONFAM checks its "
            "curated local snapshot as secondary evidence. Local records "
            "may be outdated and do not prove current registration or "
            "package authenticity."
        ),
    )


@app.get("/api/v1/registry/search")
def search_registry_by_product(product_name: str, limit: int = 10):
    """Find product-name candidates; this endpoint does not verify authenticity."""
    if not product_name.strip():
        raise HTTPException(
            status_code=422,
            detail="Provide a non-empty product_name query.",
        )

    search = GreenbookClient().search_by_product_name(
        product_name,
        limit=limit,
    )
    return {
        "query": product_name,
        "status": search.status,
        "message": search.message,
        "candidates": search.candidates or [],
    }


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

    # Prefer a live exact match. If it is absent, fall back to an exact
    # registration-number match in the curated snapshot. Never fuzzy-match
    # registration numbers or present curated evidence as live evidence.
    local_record = None
    if live_record is None and registration_number and lookup_status in {
        "not_found", "unavailable"
    }:
        from app.greenbook_client import normalize_registration_number

        requested_nr = normalize_registration_number(registration_number)
        local_matches = [
            record for record in REGISTRY_RECORDS
            if normalize_registration_number(record.nafdac_reg_number)
            == requested_nr
        ]
        if len(local_matches) == 1:
            local_record = local_matches[0]
        elif len(local_matches) > 1:
            lookup_message = (
                "The curated local snapshot contains multiple records "
                "with this normalized registration number; manual review "
                "is required."
            )

    selected_record = live_record or local_record

    # Live alert evidence supplements, never replaces, the curated snapshot.
    alert_lookup_status = "not_searched"
    live_alert_records = []
    alert_lookup_message = None

    if (claims.product_name or "").strip():
        try:
            alert_lookup = NAFDACSafetyAlertClient().lookup(
                product_name=claims.product_name,
                registration_number=registration_number or None,
                batch_number=(claims.batch_number or "").strip() or None,
            )
            alert_lookup_status = alert_lookup.status
            alert_lookup_message = alert_lookup.message

            if alert_lookup_status == "live_matches_found":
                live_alert_records = alert_lookup.records or []
        except Exception:
            # Alert-source failures must not prevent deterministic verification.
            alert_lookup_status = "live_unavailable"
            alert_lookup_message = (
                "The live safety-alert lookup failed unexpectedly."
            )

    # Prefer live evidence when the same official notice exists in both
    # sources. Deduplicate by official URL, with a conservative title-based
    # fallback for records that do not provide a URL.
    combined_alert_records = []
    seen_alert_keys = set()

    for alert in live_alert_records + ALERT_RECORDS:
        source_url = (alert.get("source_url") or "").strip().rstrip("/")
        title = " ".join((alert.get("title") or "").casefold().split())
        product = " ".join(
            (alert.get("product_name") or "").casefold().split()
        )

        if source_url:
            key = ("url", source_url.casefold())
        else:
            key = ("title_product", title, product)

        if key in seen_alert_keys:
            continue

        seen_alert_keys.add(key)
        combined_alert_records.append(alert)

    result = verify_package(
        claims,
        [selected_record] if selected_record is not None else [],
        alert_records=combined_alert_records,
    )

    if alert_lookup_status == "live_unavailable":
        result = result.model_copy(update={
            "limitations": list(result.limitations) + [
                "The live NAFDAC safety-alert source could not be checked. "
                "Curated alert evidence was still considered; this source "
                "failure does not establish whether the medicine is safe "
                "or counterfeit."
            ]
        })
    elif alert_lookup_status == "live_no_matches":
        result = result.model_copy(update={
            "limitations": list(result.limitations) + [
                "The live safety-alert search returned no qualifying "
                "notices. This does not establish that the medicine is safe."
            ]
        })

    using_local_fallback = live_record is None and local_record is not None
    updates = {
        "registry_snapshot_retrieved_at": (
            REGISTRY_DATA.get("retrieved_at") if using_local_fallback else None
        ),
        "registry_lookup_status": lookup_status,
        "registry_retrieved_at": (
            registry_retrieved_at if live_record is not None else None
        ),
        "registry_applicant_name": (
            selected_record.applicant_name if selected_record else None
        ),
        "registry_manufacturer": (
            selected_record.manufacturer if selected_record else None
        ),
        "registry_registration_status": (
            selected_record.registration_status if selected_record else None
        ),
        "registry_registration_expiry_date": (
            selected_record.registration_expiry_date if selected_record else None
        ),
        "alerts_snapshot_retrieved_at": ALERT_DATA["retrieved_at"],
        "alert_lookup_status": alert_lookup_status,
    }

    if live_record is not None:
        updates.update({
            "source_name": live_record.source_name,
            "source_url": live_record.source_url,
            "dataset_label": "live_source_record",
        })
    elif local_record is not None:
        updates.update({
            "source_name": local_record.source_name,
            "source_url": local_record.source_url,
            "dataset_label": "curated_source_record",
            "summary": (
                "A matching registration number was found in CONFAM's "
                "curated local snapshot, but the live NAFDAC Greenbook "
                "lookup did not confirm it. Treat this as historical "
                "reference evidence, not confirmation of current "
                "registration or package authenticity."
            ),
            "limitations": list(result.limitations) + [
                "This result uses a curated local record because the live "
                "lookup did not provide an exact match. The local record "
                "may be outdated.",
                f"Local registry snapshot retrieved: "
                f"{REGISTRY_DATA.get('retrieved_at', 'date unknown')}.",
            ],
        })
        if result.status != "high_risk_discrepancy_detected":
            updates["status"] = "verification_concern_detected"
        if lookup_message:
            updates["limitations"].append(lookup_message)
    elif lookup_status == "not_found":
        updates.update({
            "source_name": "NAFDAC Greenbook",
            "source_url": GREENBOOK_URL,
            "dataset_label": "live_lookup",
            "summary": (
                "No exact registration-number match was found in the live "
                "Greenbook lookup or CONFAM's curated local snapshot. "
                "This does not prove that the medicine is counterfeit "
                "or unregistered."
            ),
            "limitations": list(result.limitations) + [
                "No exact registration-number match was found in either "
                "the live search results or the curated local snapshot."
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
                "The live NAFDAC Greenbook was unavailable, and no exact "
                "registration-number match was found in CONFAM's curated "
                "local snapshot. Current registration could not be "
                "confirmed. This is not evidence that the medicine is "
                "counterfeit."
            ),
            "limitations": list(result.limitations) + [
                "Live registry comparison was unavailable and no exact "
                "local fallback record was found."
            ],
        })
        if result.status != "high_risk_discrepancy_detected":
            updates["status"] = "verification_concern_detected"
    else:
        updates.update({
            "dataset_label": None,
            "limitations": list(result.limitations) + [
                "No registration number was supplied, so the registration "
                "number lookup and exact-NRN local fallback were skipped."
            ],
        })

    if lookup_message and lookup_status in {"not_found", "unavailable"}:
        if local_record is None:
            updates["limitations"] = list(updates.get(
                "limitations", result.limitations
            )) + [lookup_message]

    result = result.model_copy(update=updates)

    semantic_reconciliation = reconcile_semantic_differences(result)
    if semantic_reconciliation:
        result = result.model_copy(
            update={"semantic_reconciliation": semantic_reconciliation}
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
