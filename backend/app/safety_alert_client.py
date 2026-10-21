"""Read-only client for NAFDAC's public WordPress safety-alert notices.

Search results are discovery candidates, not evidence that a package is affected.
Only conservatively parsed product-level records are returned for deterministic
matching. Broader notices remain available as notice metadata for review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from html.parser import HTMLParser
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


NAFDAC_SEARCH_URL = "https://nafdac.gov.ng/wp-json/wp/v2/search"
NAFDAC_POSTS_URL = "https://nafdac.gov.ng/wp-json/wp/v2/posts"
USER_AGENT = "CONFAM-Demo/1.0 (public medicine-safety evidence lookup)"


@dataclass
class LiveAlertLookupResult:
    status: str
    records: list[dict] = field(default_factory=list)
    notices: list[dict] = field(default_factory=list)
    message: str = ""
    retrieved_at: str = field(default_factory=lambda: date.today().isoformat())


class _TextExtractor(HTMLParser):
    """Extract readable text while keeping table/list content separable."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        cleaned = " ".join(data.split())
        if cleaned:
            self.parts.append(cleaned)

    @property
    def text(self) -> str:
        return " ".join(self.parts)


class NAFDACSafetyAlertClient:
    def __init__(self, timeout: float = 8.0, max_notices: int = 5):
        self.timeout = timeout
        self.max_notices = max(1, min(max_notices, 10))

    def _get_json(self, url: str) -> object:
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        with urlopen(request, timeout=self.timeout) as response:
            if response.status != 200:
                raise OSError(f"NAFDAC returned HTTP {response.status}")
            return json.loads(response.read().decode("utf-8"))

    def _search(self, query: str) -> list[dict]:
        url = NAFDAC_SEARCH_URL + "?" + urlencode({
            "search": query,
            "per_page": self.max_notices,
        })
        payload = self._get_json(url)
        if not isinstance(payload, list):
            raise ValueError("Unexpected NAFDAC search response.")
        results = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            title = item.get("title")
            link = item.get("url")
            post_id = item.get("id")
            if isinstance(title, str) and isinstance(link, str) and isinstance(post_id, int):
                if link.startswith("https://nafdac.gov.ng/"):
                    results.append({
                        "id": post_id,
                        "title": title,
                        "url": link,
                        "type": item.get("type"),
                        "subtype": item.get("subtype"),
                    })
        return results

    def _fetch_post(self, post_id: int) -> dict:
        payload = self._get_json(f"{NAFDAC_POSTS_URL}/{post_id}")
        if not isinstance(payload, dict):
            raise ValueError("Unexpected NAFDAC post response.")
        return payload

    @staticmethod
    def _parse_notice(post: dict) -> tuple[dict, str]:
        title_obj = post.get("title")
        content_obj = post.get("content")
        title = title_obj.get("rendered") if isinstance(title_obj, dict) else None
        content = content_obj.get("rendered") if isinstance(content_obj, dict) else None
        link = post.get("link")
        published = post.get("date")
        modified = post.get("modified")

        if not isinstance(title, str) or not isinstance(content, str):
            raise ValueError("NAFDAC notice is missing its title or content.")
        if not isinstance(link, str) or not link.startswith("https://nafdac.gov.ng/"):
            raise ValueError("NAFDAC notice URL is missing or untrusted.")

        parser = _TextExtractor()
        parser.feed(content)
        text = parser.text

        alert_match = re.search(
            r"\b(?:public\s+)?alert\s*(?:no\.?\s*)?([0-9]{1,3}\s*/\s*20[0-9]{2})",
            title,
            flags=re.IGNORECASE,
        )
        notice = {
            "alert_id": f"NAFDAC-LIVE-{post.get('id')}",
            "alert_number": re.sub(r"\s+", "", alert_match.group(1)) if alert_match else None,
            "title": title,
            "source_name": "NAFDAC",
            "source_url": link,
            "published_at": published if isinstance(published, str) else None,
            "modified_at": modified if isinstance(modified, str) else None,
            "retrieved_at": date.today().isoformat(),
            "dataset_label": "live_source_record",
        }
        return notice, text

    @staticmethod
    def _extract_identifiers(text: str) -> tuple[list[str], list[str]]:
        """Extract explicit batch identifiers and NAFDAC registration numbers.

        These patterns are intentionally conservative. Unstructured text is
        not treated as a reliable batch list merely because it has digits.
        """
        batches: list[str] = []
        for match in re.finditer(
            r"\b(?:batch|lot)\s*(?:number|no\.?|#)?\s*[:\-]?\s*"
            r"([A-Z0-9][A-Z0-9./-]{2,})",
            text,
            flags=re.IGNORECASE,
        ):
            value = match.group(1).strip(" .,:;")
            if value and value.casefold() not in {x.casefold() for x in batches}:
                batches.append(value)

        numbers: list[str] = []
        for match in re.finditer(
            r"\b(?:NAFDAC\s*(?:registration|reg\.?\s*no\.?)|"
            r"registration\s*(?:number|no\.?))\s*[:#\-]?\s*"
            r"([A-Z0-9][A-Z0-9/-]{3,})",
            text,
            flags=re.IGNORECASE,
        ):
            value = match.group(1).strip(" .,:;")
            if value and value.casefold() not in {x.casefold() for x in numbers}:
                numbers.append(value)
        return batches, numbers

    def lookup(self, product_name: str, registration_number: str | None = None,
               batch_number: str | None = None) -> LiveAlertLookupResult:
        product_name = (product_name or "").strip()
        registration_number = (registration_number or "").strip()
        batch_number = (batch_number or "").strip()

        if not product_name:
            return LiveAlertLookupResult(
                status="live_unavailable",
                message="A product name is required for live alert discovery.",
            )

        try:
            candidates = self._search(product_name)
            if registration_number:
                candidates += self._search(registration_number)

            unique: dict[int, dict] = {}
            for candidate in candidates:
                unique[candidate["id"]] = candidate

            notices: list[dict] = []
            records: list[dict] = []

            for candidate in list(unique.values())[: self.max_notices]:
                post = self._fetch_post(candidate["id"])
                notice, text = self._parse_notice(post)
                notices.append(notice)

                # Only accept notices whose text explicitly includes the
                # supplied product name and an additional supplied identifier
                # matching a reported identifier. Broader notices are still
                # returned as notice metadata but not fed to the matcher.
                product_in_notice = product_name.casefold() in text.casefold()
                reported_batches, reported_numbers = self._extract_identifiers(text)

                batch_match = bool(
                    batch_number
                    and any(x.casefold() == batch_number.casefold()
                            for x in reported_batches)
                )
                number_match = bool(
                    registration_number
                    and any(re.sub(r"[^a-z0-9]", "", x.casefold())
                            == re.sub(r"[^a-z0-9]", "", registration_number.casefold())
                            for x in reported_numbers)
                )

                if product_in_notice and (batch_match or number_match):
                    records.append({
                        **notice,
                        "product_name": product_name,
                        "reported_registration_number": (
                            registration_number if number_match else None
                        ),
                        "batch_number": None,
                        "batch_numbers": (
                            [batch_number] if batch_match else []
                        ),
                        "alert_scope": "batch_specific" if batch_match else "registration_number_specific",
                        "expiry_date": None,
                    })

            return LiveAlertLookupResult(
                status="live_matches_found" if records else "live_no_matches",
                records=records,
                notices=notices,
                message=(
                    "Live NAFDAC notices were retrieved. Matching records require review."
                    if records else
                    "Live NAFDAC search completed; no notice met the strict product-and-identifier criteria. This does not establish safety."
                ),
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError,
                json.JSONDecodeError) as exc:
            return LiveAlertLookupResult(
                status="live_unavailable",
                message=f"Live NAFDAC alert lookup could not be completed ({type(exc).__name__}).",
            )
