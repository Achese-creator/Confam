"""Read-only MCP tools exposing CONFAM's existing evidence sources."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.greenbook_client import GreenbookClient
from app.main import ALERT_RECORDS
from app.safety_alert_client import NAFDACSafetyAlertClient
from app.schemas import PackageClaims
from app.verifier import find_relevant_alerts

mcp = FastMCP(
    "CONFAM Evidence",
    instructions=(
        "Read-only medicine-registry and safety-alert evidence tools. "
        "Candidate searches do not establish product identity. "
        "Missing records or unavailable sources do not prove counterfeiting."
    ),
)


@mcp.tool()
def search_greenbook_by_nrn(registration_number: str) -> dict:
    """Look up an exact NAFDAC registration number in the live Greenbook."""
    result = GreenbookClient().lookup(registration_number)
    return {
        "source": "NAFDAC Greenbook",
        "status": result.status,
        "message": result.message,
        "record": (
            result.record.model_dump(mode="json")
            if result.record is not None
            else None
        ),
        "interpretation": (
            "An exact registry match is evidence about the registered record, "
            "not proof that the physical package is authentic."
        ),
    }


@mcp.tool()
def search_greenbook_by_product(
    product_name: str,
    limit: int = 5,
) -> dict:
    """Find ranked Greenbook product-name candidates; this is not verification."""
    result = GreenbookClient().search_by_product_name(
        product_name,
        limit=max(1, min(limit, 10)),
    )
    return {
        "source": "NAFDAC Greenbook",
        "query": product_name,
        "status": result.status,
        "message": result.message,
        "candidates": result.candidates,
        "interpretation": (
            "Candidates are discovery leads only. Confirm identity using "
            "exact registration details and compare the product attributes."
        ),
    }


@mcp.tool()
def get_relevant_safety_alerts(
    product_name: str,
    nafdac_reg_number: str | None = None,
    batch_number: str | None = None,
) -> dict:
    """Check live NAFDAC alerts and the curated snapshot separately."""
    claims = PackageClaims(
        product_name=product_name,
        nafdac_reg_number=nafdac_reg_number,
        batch_number=batch_number,
    )

    curated_alerts = find_relevant_alerts(claims, ALERT_RECORDS)
    curated_records = [
        alert.model_dump(mode="json") for alert in curated_alerts
    ]

    try:
        live_result = NAFDACSafetyAlertClient().lookup(
            product_name=product_name,
            registration_number=nafdac_reg_number,
            batch_number=batch_number,
        )
        live_status = live_result.status
        live_records = live_result.records or []
        live_message = live_result.message
    except Exception:
        # Live-source failure must not suppress curated evidence.
        live_status = "live_unavailable"
        live_records = []
        live_message = "The live NAFDAC safety-alert lookup failed unexpectedly."

    return {
        "source": "Live NAFDAC safety-alert lookup plus CONFAM curated snapshot",
        "live_status": live_status,
        "live_message": live_message,
        "live_alert_count": len(live_records),
        "live_alerts": live_records,
        "status": (
            "matches_found"
            if live_records or curated_records
            else "no_matches_found"
        ),
        "curated_status": "matches_found" if curated_records else "no_matches_found",
        "alert_count": len(curated_records),
        "curated_alert_count": len(curated_records),
        "alerts": curated_records,
        "curated_alerts": curated_records,
        "interpretation": (
            "Live and curated evidence are reported separately. A relevant "
            "alert warrants review but does not by itself prove this package "
            "is affected. An unavailable source or no matching alert does not "
            "establish safety or authenticity."
        ),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
