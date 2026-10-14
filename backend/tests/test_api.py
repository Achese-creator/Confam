from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_registry_metadata_reports_curated_records():
    response = client.get("/api/v1/registry")

    assert response.status_code == 200
    data = response.json()

    assert data["dataset_label"] == "curated_source_record"
    assert data["record_count"] == 3
    assert "snapshot" in data["source_note"].lower()


def test_real_registry_record_can_be_checked():
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
    assert data["dataset_label"] == "curated_source_record"
    assert data["source_name"] == "NAFDAC Greenbook"


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
    assert "not proof of counterfeiting" in data["summary"].lower()


def test_alert_metadata_reports_curated_snapshot():
    response = client.get("/api/v1/alerts")

    assert response.status_code == 200
    data = response.json()

    assert data["record_count"] == 1
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

