from typing import Literal

from pydantic import BaseModel, Field


class PackageClaims(BaseModel):
    """Claims extracted from a medicine package."""

    product_name: str | None = None
    active_ingredients: str | None = None
    strength: str | None = None
    dosage_form: str | None = None
    nafdac_reg_number: str | None = None
    manufacturer: str | None = None
    batch_number: str | None = None
    manufacturing_date: str | None = None
    expiry_date: str | None = None


class RegistryRecord(BaseModel):
    """A registration record from our local evidence dataset."""

    product_name: str
    active_ingredients: str | None = None
    strength: str | None = None
    dosage_form: str | None = None
    nafdac_reg_number: str
    manufacturer: str | None = None
    registration_status: str | None = None
    registration_expiry_date: str | None = None
    source_name: str
    source_url: str
    retrieved_at: str
    dataset_label: Literal["synthetic_test_fixture", "curated_source_record"]


class FieldCheck(BaseModel):
    """An auditable comparison between a claim and reference evidence."""

    field: str
    package_value: str | None = None
    reference_value: str | None = None
    outcome: Literal["match", "mismatch", "not_checked"]
    explanation: str
    check_scope: Literal[
        "package_vs_registry",
        "registry_record_only",
    ] = "package_vs_registry"


class SafetyAlertRecord(BaseModel):
    """Validated source record for a published medicine safety alert."""

    alert_id: str
    alert_number: str | None = None
    title: str
    product_name: str
    product_ingredients: str | None = None
    product_strength: str | None = None
    reported_registration_number: str | None = None
    reference_product_name: str | None = None
    reference_product_strength: str | None = None
    reference_manufacturer: str | None = None
    batch_number: str | None = None
    manufacturing_date: str | None = None
    expiry_date: str | None = None
    alert_scope: str | None = None
    risk_classification: str | None = None
    source_name: str
    source_url: str
    published_at: str | None = None
    retrieved_at: str
    dataset_label: Literal["synthetic_test_fixture", "curated_source_record"]
    curation_note: str | None = None


class SafetyAlertEvidence(BaseModel):
    """A relevant safety alert; not proof about an individual package."""

    alert_id: str
    title: str
    product_name: str
    source_name: str
    source_url: str
    published_at: str | None = None
    alert_scope: str | None = None
    explanation: str


class VerificationResult(BaseModel):
    """Evidence-based outcome; not a declaration of authenticity."""

    status: Literal[
        "no_significant_discrepancy_detected",
        "verification_concern_detected",
        "high_risk_discrepancy_detected",
    ]
    summary: str
    matched_record: bool
    registry_snapshot_retrieved_at: str | None = None
    alerts_snapshot_retrieved_at: str | None = None
    checks: list[FieldCheck] = Field(default_factory=list)
    safety_alerts: list[SafetyAlertEvidence] = Field(default_factory=list)
    source_name: str | None = None
    source_url: str | None = None
    dataset_label: str | None = None
    limitations: list[str] = Field(default_factory=list)
