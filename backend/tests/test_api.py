import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.greenbook_client import GreenbookLookupResult
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def mock_greenbook_lookup(monkeypatch):
    """Keep API tests deterministic and independent of the live Greenbook."""
    record = {
        "product_name": "AG Spironolactone",
        "active_ingredients": "Spironolactone",
        "strength": "25 mg",
        "dosage_form": "Tablet",
        "nafdac_reg_number": "A4-100160",
        "manufacturer": None,
        "registration_status": "Active",
        "registration_expiry_date": "2026-12-16",
        "source_name": "NAFDAC Greenbook",
        "source_url": "https://greenbook.nafdac.gov.ng/products/details/2",
        "retrieved_at": "2026-10-19",
        "dataset_label": "live_source_record",
        "applicant_name": "AnnyGod Pharmaceutical Company Ltd",
        "composition": (
            "Each film coated tablet contains: Spironolactone BP 25 mg "
            "Excipients q.s."
        ),
        "route": "Oral",
        "pack_size": "1 x 500's (in a jar)",
        "approval_date": "2021-12-17",
    }

    def fake_lookup(self, registration_number):
        normalized = "".join(
            character.lower()
            for character in str(registration_number or "")
            if character.isalnum()
        )
        if normalized == "a4100160":
            return GreenbookLookupResult(
                status="match",
                record=record,
                message="Exact registration-number match found in Greenbook.",
            )

        return GreenbookLookupResult(
            status="not_found",
            record=None,
            message=(
                "No exact match for this registration number was found "
                "in the live NAFDAC Greenbook lookup. This is not proof "
                "of counterfeiting."
            ),
        )

    monkeypatch.setattr(
        main_module.GreenbookClient,
        "lookup",
        fake_lookup,
    )

    # Keep API tests independent of external Gemini calls.
    monkeypatch.setattr(
        main_module,
        "reconcile_semantic_differences",
        lambda result: None,
    )
    monkeypatch.setattr(
        main_module,
        "generate_evidence_briefing",
        lambda result: "Test briefing.",
    )
    from types import SimpleNamespace

    monkeypatch.setattr(
        main_module.NAFDACSafetyAlertClient,
        "lookup",
        lambda self, **kwargs: SimpleNamespace(
            status="live_no_matches",
            records=[],
            message="Live lookup mocked in API tests.",
        ),
    )


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_registry_metadata_reports_live_lookup_with_curated_fallback():
    response = client.get("/api/v1/registry")

    assert response.status_code == 200
    data = response.json()

    assert data["dataset_label"] == "live_lookup_with_curated_fallback"
    assert data["record_count"] > 0
    assert "live nafdac greenbook first" in data["source_note"].lower()
    assert "snapshot" in data["source_note"].lower()
    assert "outdated" in data["source_note"].lower()


def test_live_registry_record_can_be_checked():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "AG Spironolactone",
                "active_ingredients": "Spironolactone",
                "strength": "25 mg",
                "dosage_form": "Tablet",
                "nafdac_reg_number": "A4-100160",
                "manufacturer": "Alpa Laboratories Pvt Ltd",
            }
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["matched_record"] is True
    assert data["status"] == "no_significant_discrepancy_detected"
    assert data["dataset_label"] == "live_source_record"
    assert data["source_name"] == "NAFDAC Greenbook"
    assert data["registry_lookup_status"] == "match"
    assert data["registry_retrieved_at"] is not None
    assert data["registry_snapshot_retrieved_at"] is None


def test_unknown_number_is_not_called_fake():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "Unknown Product",
                "nafdac_reg_number": "UNKNOWN-999",
            }
        },
    )

    assert response.status_code == 200
    data = response.json()

    assert data["matched_record"] is False
    assert data["status"] == "verification_concern_detected"
    summary = data["summary"].lower()
    assert "does not prove" in summary
    assert "counterfeit" in summary


def test_unavailable_greenbook_is_not_called_counterfeit(monkeypatch):
    def unavailable_lookup(self, registration_number):
        return GreenbookLookupResult(
            status="unavailable",
            record=None,
            message="Could not reach the NAFDAC Greenbook.",
        )

    monkeypatch.setattr(
        main_module.GreenbookClient,
        "lookup",
        unavailable_lookup,
    )

    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "Example Medicine",
                "nafdac_reg_number": "A4-100160",
            }
        },
    )

    assert response.status_code == 200
    data = response.json()

    # Live lookup failed, but the exact NRN exists in the curated snapshot.
    assert data["matched_record"] is True
    assert data["registry_lookup_status"] == "unavailable"
    assert data["dataset_label"] == "curated_source_record"
    assert data["registry_retrieved_at"] is None
    assert data["registry_snapshot_retrieved_at"] is not None
    assert data["status"] == "high_risk_discrepancy_detected"

    summary = data["summary"].lower()
    assert "live nafdac greenbook lookup" in summary
    assert "did not" in summary
    assert "snapshot" in summary or "curated" in summary
    assert "not confirmation of current registration" in summary


def test_alert_metadata_reports_curated_snapshot():
    response = client.get("/api/v1/alerts")

    assert response.status_code == 200
    data = response.json()

    records_response = client.get("/api/v1/alerts/records")
    assert records_response.status_code == 200

    records = records_response.json()["records"]
    assert data["record_count"] == len(records)
    assert "scope" in data["source_note"].lower()


def test_betaclox_alert_is_available_separately():
    response = client.get("/api/v1/alerts/records")

    assert response.status_code == 200
    records = response.json()["records"]

    betaclox = next(
        record
        for record in records
        if record["alert_id"] == "NAFDAC-PUBLIC-ALERT-037-2025"
    )

    assert betaclox["product_name"] == "BETACLOX"
    assert betaclox["reported_registration_number"] == "A4-4724"
    assert betaclox["reference_product_name"] == "Mebendazole"
    assert betaclox["dataset_label"] == "curated_source_record"


def test_malformed_request_is_rejected():
    response = client.post(
        "/api/v1/verify",
        json={"wrong_field": {}},
    )

    assert response.status_code == 422


def test_verify_surfaces_matching_safety_alert():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "BETACLOX",
                "nafdac_reg_number": "A4-4724",
                "batch_number": "230701",
            }
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "high_risk_discrepancy_detected"
    assert len(result["safety_alerts"]) == 1
    alert = result["safety_alerts"][0]
    assert alert["alert_id"] == "NAFDAC-PUBLIC-ALERT-037-2025"
    assert alert["source_name"] == "NAFDAC"
    assert "not proof" in alert["explanation"]


def test_unrelated_product_is_not_flagged_by_shared_alert_number():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "Example Paracetamol",
                "nafdac_reg_number": "A4-4724",
                "active_ingredients": "Paracetamol",
                "strength": "500 mg",
                "dosage_form": "Tablet",
            }
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "verification_concern_detected"
    assert result["safety_alerts"] == []
    assert result["matched_record"] is False




def test_api_zacef_exact_reported_batch_triggers_high_risk():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "ZACEF-TZ Injection",
                "nafdac_reg_number": "A-46344",
                "batch_number": "C-2308-02A",
            }
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "high_risk_discrepancy_detected"
    assert len(result["safety_alerts"]) == 1
    alert = result["safety_alerts"][0]
    assert alert["alert_id"] == "NAFDAC-PUBLIC-ALERT-009-2024"
    assert alert["batch_match_status"] == "match"


def test_api_zacef_different_batch_does_not_escalate_from_alert_alone():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "ZACEF-TZ Injection",
                "nafdac_reg_number": "A-46344",
                "batch_number": "UNRELATED-BATCH-999",
            }
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "verification_concern_detected"
    assert len(result["safety_alerts"]) == 1
    alert = result["safety_alerts"][0]
    assert alert["alert_id"] == "NAFDAC-PUBLIC-ALERT-009-2024"
    assert alert["batch_match_status"] == "different"
    assert "does not establish" in alert["explanation"].lower()


def test_api_cialis_exact_reported_batch_triggers_high_risk():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "Cialis Tadalafil tablets 20mg",
                "nafdac_reg_number": "4464",
                "batch_number": "E3242401",
            }
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "high_risk_discrepancy_detected"
    assert any(
        alert["alert_id"] == "NAFDAC-PUBLIC-ALERT-033-2025"
        and alert["batch_match_status"] == "match"
        for alert in result["safety_alerts"]
    )


def test_api_amoxivue_exact_reported_batch_triggers_high_risk():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "Amoxivue",
                "nafdac_reg_number": "A4-100178",
                "batch_number": "322584",
            }
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "high_risk_discrepancy_detected"
    assert any(
        alert["alert_id"] == "NAFDAC-PUBLIC-ALERT-024-2025"
        and alert["batch_match_status"] == "match"
        for alert in result["safety_alerts"]
    )


def test_api_amoxivue_different_batch_does_not_escalate_from_alert_alone():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "Amoxivue",
                "nafdac_reg_number": "A4-100178",
                "batch_number": "UNRELATED-BATCH-999",
            }
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "verification_concern_detected"
    assert any(
        alert["alert_id"] == "NAFDAC-PUBLIC-ALERT-024-2025"
        and alert["batch_match_status"] == "different"
        for alert in result["safety_alerts"]
    )


def test_api_sporidex_exact_recalled_batch_triggers_high_risk():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "Sporidex Suspension",
                "batch_number": "DFG4606A",
            }
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "high_risk_discrepancy_detected"
    assert any(
        alert["alert_id"] == "NAFDAC-PUBLIC-ALERT-033-2026"
        and alert["batch_match_status"] == "match"
        for alert in result["safety_alerts"]
    )

def test_verify_deduplicates_live_and_curated_alerts(monkeypatch):
    from types import SimpleNamespace
    import app.main as main_module

    curated_alert = next(
        alert for alert in main_module.ALERT_RECORDS
        if "BETACLOX" in alert.get("product_name", "").upper()
    )

    monkeypatch.setattr(
        main_module.NAFDACSafetyAlertClient,
        "lookup",
        lambda self, **kwargs: SimpleNamespace(
            status="live_matches_found",
            records=[dict(curated_alert)],
            message="Test live match.",
        ),
    )

    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "BETACLOX",
                "nafdac_reg_number": "A4-4724",
                "batch_number": "230701",
            }
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "high_risk_discrepancy_detected"
    assert len(result["safety_alerts"]) == 1


def test_verify_continues_when_live_alert_source_is_unavailable(monkeypatch):
    from types import SimpleNamespace
    import app.main as main_module

    monkeypatch.setattr(
        main_module.NAFDACSafetyAlertClient,
        "lookup",
        lambda self, **kwargs: SimpleNamespace(
            status="live_unavailable",
            records=[],
            message="Simulated source timeout.",
        ),
    )

    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "Medicine Without a Known Alert",
                "nafdac_reg_number": "UNKNOWN-TEST-NRN",
                "batch_number": "TEST-BATCH",
            }
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert any(
        "live NAFDAC safety-alert source could not be checked"
        in limitation
        for limitation in result["limitations"]
    )
    assert result["status"] != "high_risk_discrepancy_detected"

