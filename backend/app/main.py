
from fastapi import FastAPI
from pydantic import BaseModel

from app.schemas import PackageClaims, RegistryRecord, VerificationResult
from app.verifier import verify_package

app = FastAPI(
    title="CONFAM API",
    description=(
        "AI-assisted medicine-package and regulatory evidence checker. "
        "Results identify discrepancies; they do not certify authenticity."
    ),
    version="0.1.0",
)


# Temporary development fixtures only.
# These are NOT real NAFDAC records.
SAMPLE_REGISTRY: list[RegistryRecord] = [
    RegistryRecord(
        product_name="Example Paracetamol Tablets",
        active_ingredients="Paracetamol",
        strength="500 mg",
        dosage_form="Tablet",
        nafdac_reg_number="SYNTHETIC-TEST-001",
        manufacturer="Example Manufacturer Ltd",
        registration_status="Registered",
        registration_expiry_date="2030-12-31",
        source_name="CONFAM synthetic development fixture",
        source_url="https://example.invalid/synthetic-fixture",
        retrieved_at="2026-10-14",
        dataset_label="synthetic_test_fixture",
    )
]


class VerifyRequest(BaseModel):
    claims: PackageClaims


class RegistryMetadata(BaseModel):
    dataset_label: str
    record_count: int
    source_note: str


@app.get("/health")
def health_check():
    return {"status": "ok", "service": "confam-api"}


@app.get("/api/v1/registry", response_model=RegistryMetadata)
def registry_metadata():
    return RegistryMetadata(
        dataset_label="synthetic_test_fixture",
        record_count=len(SAMPLE_REGISTRY),
        source_note=(
            "Development-only synthetic data. "
            "No records in this dataset represent verified NAFDAC products."
        ),
    )


@app.post("/api/v1/verify", response_model=VerificationResult)
def verify(request: VerifyRequest):
    return verify_package(request.claims, SAMPLE_REGISTRY)
