"""Read-only MCP tools exposing CONFAM's existing evidence sources."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from app.greenbook_client import GreenbookClient
from app.main import ALERT_RECORDS
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
    """Retrieve curated alerts matching product name plus NRN or batch."""
    claims = PackageClaims(
        product_name=product_name,
        nafdac_reg_number=nafdac_reg_number,
        batch_number=batch_number,
    )
    alerts = find_relevant_alerts(claims, ALERT_RECORDS)
    return {
        "source": "CONFAM validated safety-alert snapshot",
        "status": "matches_found" if alerts else "no_matches_found",
        "alert_count": len(alerts),
        "alerts": [alert.model_dump(mode="json") for alert in alerts],
        "interpretation": (
            "A relevant alert is evidence for review, not proof that this "
            "package is affected. No matching alert does not establish safety."
        ),
    }


if __name__ == "__main__":
    mcp.run(transport="stdio")
