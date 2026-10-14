
from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_endpoint():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_registry_identifies_synthetic_data():
    response = client.get("/api/v1/registry")

    assert response.status_code == 200
    data = response.json()
    assert data["dataset_label"] == "synthetic_test_fixture"
    assert data["record_count"] == 1
    assert "synthetic" in data["source_note"].lower()


def test_matching_synthetic_record_returns_cautious_status():
    response = client.post(
        "/api/v1/verify",
        json={
            "claims": {
                "product_name": "Example Paracetamol Tablets",
                "active_ingredients": "Paracetamol",
                "strength": "500 mg",
                "dosage_form": "Tablet",
                "nafdac_reg_number": "SYNTHETIC-TEST-001",
                "manufacturer": "Example Manufacturer Ltd",
            }
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["matched_record"] is True
    assert data["status"] == "no_significant_discrepancy_detected"
    assert data["dataset_label"] == "synthetic_test_fixture"


def test_unknown_number_does_not_claim_product_is_fake():
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
    assert data["status"] == "verification_concern_detected"
    assert "not proof of counterfeiting" in data["summary"].lower()


def test_malformed_request_is_rejected():
    response = client.post(
        "/api/v1/verify",
        json={"wrong_field": {}},
    )

    assert response.status_code == 422
