"""Live, read-only lookup client for the NAFDAC Greenbook."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from html import unescape
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


GREENBOOK_URL = "https://greenbook.nafdac.gov.ng/"
USER_AGENT = "CONFAM/0.1 (medicine regulatory evidence lookup)"


class GreenbookUnavailable(RuntimeError):
    """The Greenbook source could not be queried reliably."""


@dataclass(frozen=True)
class GreenbookLookupResult:
    status: str
    record: dict | None = None
    message: str | None = None


def normalize_registration_number(value: object) -> str:
    """Normalize punctuation and case without fuzzy-matching registration IDs."""
    return re.sub(r"[^a-zA-Z0-9]", "", str(value or "")).casefold()


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = unescape(str(value))
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _row_to_record(row: dict, retrieved_at: str) -> dict:
    product_name = _clean_text(row.get("product_name"))
    if product_name:
        product_name = re.sub(r"\s*#+\s*$", "", product_name).strip()

    registration_number = _clean_text(row.get("NAFDAC"))
    if not product_name or not registration_number:
        raise GreenbookUnavailable(
            "Greenbook returned a matching row without a product name "
            "or registration number."
        )

    product_id = row.get("product_id")
    if product_id is None:
        raise GreenbookUnavailable(
            "Greenbook returned a matching row without a product ID."
        )

    product_id_text = str(product_id).strip()
    if not product_id_text.isdigit():
        raise GreenbookUnavailable(
            "Greenbook returned an invalid product ID."
        )

    return {
        "product_name": product_name,
        "active_ingredients": _clean_text(row.get("ingredient_name")),
        "strength": _clean_text(row.get("strength")),
        "dosage_form": _clean_text(row.get("form_name")),
        "nafdac_reg_number": registration_number,
        # Greenbook's applicant field is not necessarily the manufacturer.
        "manufacturer": None,
        "registration_status": _clean_text(row.get("status")),
        "registration_expiry_date": _clean_text(row.get("expiry_date")),
        "source_name": "NAFDAC Greenbook",
        "source_url": (
            f"https://greenbook.nafdac.gov.ng/products/details/"
            f"{product_id_text}"
        ),
        "retrieved_at": retrieved_at,
        "dataset_label": "live_source_record",
        "applicant_name": _clean_text(row.get("applicant_name")),
        "composition": _clean_text(row.get("composition")),
        "route": _clean_text(row.get("route_name")),
        "pack_size": _clean_text(row.get("pack_size")),
        "approval_date": _clean_text(row.get("approval_date")),
    }


class GreenbookClient:
    """Search Greenbook and confirm exact NRN matches from live results."""

    def __init__(
        self,
        base_url: str = GREENBOOK_URL,
        timeout: float = 15.0,
    ) -> None:
        self.base_url = base_url
        self.timeout = timeout

    def _fetch_rows(self, registration_number: str) -> list[dict]:
        params = {
            "draw": "1",
            "start": "0",
            "length": "25",
            "search[value]": registration_number,
            "search[regex]": "false",
        }
        url = f"{self.base_url}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(request, timeout=self.timeout) as response:
                if response.status != 200:
                    raise GreenbookUnavailable(
                        f"Greenbook returned HTTP {response.status}."
                    )
                payload = json.loads(response.read().decode("utf-8"))
        except GreenbookUnavailable:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise GreenbookUnavailable(
                "Could not reach the NAFDAC Greenbook."
            ) from exc
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GreenbookUnavailable(
                "Greenbook returned a response that was not valid JSON."
            ) from exc

        if not isinstance(payload, dict):
            raise GreenbookUnavailable(
                "Greenbook returned an unexpected response structure."
            )

        rows = payload.get("data")
        if not isinstance(rows, list):
            raise GreenbookUnavailable(
                "Greenbook response did not contain a valid data list."
            )

        if any(not isinstance(row, dict) for row in rows):
            raise GreenbookUnavailable(
                "Greenbook returned an invalid result row."
            )

        return rows

    def lookup(self, registration_number: str) -> GreenbookLookupResult:
        requested = normalize_registration_number(registration_number)
        if not requested:
            return GreenbookLookupResult(
                status="not_found",
                message="No registration number was supplied.",
            )

        try:
            rows = self._fetch_rows(registration_number)
        except GreenbookUnavailable as exc:
            return GreenbookLookupResult(
                status="unavailable",
                message=str(exc),
            )

        # Never trust the search count or a fuzzy search hit alone.
        exact_matches = [
            row for row in rows
            if normalize_registration_number(row.get("NAFDAC")) == requested
        ]

        if not exact_matches:
            return GreenbookLookupResult(
                status="not_found",
                message=(
                    "No exact registration-number match was found in the "
                    "Greenbook search results. This alone does not establish "
                    "that a medicine is counterfeit or unregistered."
                ),
            )

        if len(exact_matches) > 1:
            return GreenbookLookupResult(
                status="unavailable",
                message=(
                    "Greenbook returned multiple records with the same "
                    "normalized registration number; manual review is needed."
                ),
            )

        try:
            record = _row_to_record(
                exact_matches[0],
                retrieved_at=date.today().isoformat(),
            )
        except GreenbookUnavailable as exc:
            return GreenbookLookupResult(
                status="unavailable",
                message=str(exc),
            )

        return GreenbookLookupResult(
            status="match",
            record=record,
            message="Exact registration-number match found in Greenbook.",
        )
