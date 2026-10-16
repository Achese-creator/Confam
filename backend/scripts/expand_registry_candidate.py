"""Build a non-destructive registry candidate from individual Greenbook pages."""

import argparse
import json
import re
import sys
import time
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.dataset_validation import normalize_registration_number
DATA = BACKEND / "data"
TODAY = date.today().isoformat()
BASE = "https://greenbook.nafdac.gov.ng"
LISTING_URLS = [
    f"{BASE}/ingredient/products/1529",  # Paracetamol
    f"{BASE}/ingredient/products/1546",  # Amoxicillin
    f"{BASE}/ingredient/products/625",   # Metformin
]
TARGET_COUNT = 50
USER_AGENT = "CONFAM-Evidence-Snapshot/1.0 (curated offline ingestion)"


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.links = []
        self._link = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a" and attrs.get("href"):
            self._link = {"href": attrs["href"], "text": []}
            self.links.append(self._link)

    def handle_data(self, data):
        value = " ".join(data.split())
        if not value:
            return
        self.parts.append(value)
        if self._link is not None:
            self._link["text"].append(value)

    def handle_endtag(self, tag):
        if tag == "a":
            self._link = None


def fetch(url):
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with urlopen(request, timeout=20) as response:
        if response.status != 200:
            raise ValueError(f"HTTP {response.status}: {url}")
        return response.read().decode("utf-8", errors="replace")


def clean(value):
    value = re.sub(r"\s+", " ", value or "").strip()
    return value or None


def find_after(lines, label):
    wanted = label.casefold()
    for index, line in enumerate(lines):
        if line.casefold() == wanted and index + 1 < len(lines):
            return clean(lines[index + 1])
    return None


def parse_product(url, html):
    parser = TextParser()
    parser.feed(html)
    lines = [line.strip() for line in parser.parts if line.strip()]

    # Greenbook's page heading may be split across separate HTML text nodes.
    detail_index = None
    for i in range(len(lines) - 1):
        if lines[i].casefold() == "product" and lines[i + 1].casefold() == "details":
            detail_index = i + 2
            break
        if lines[i].casefold() == "product details":
            detail_index = i + 1
            break

    if detail_index is None or detail_index >= len(lines):
        # Fall back to the first visible product heading before the NRN field.
        try:
            nrn_index = next(i for i, line in enumerate(lines) if line.casefold() == "nrn")
        except StopIteration:
            return None, "Product heading and NRN field not found"

        excluded = {
            "home", "products classes", "atc classification",
            "active ingredients", "applicants", "manufacturers",
            "more", "report sf", "report adr", "product", "details",
        }
        product_name = next(
            (line for line in lines[:nrn_index]
             if line.casefold() not in excluded
             and not line.casefold().startswith("product details")),
            None,
        )
    else:
        product_name = lines[detail_index]

    product_name = clean(product_name)
    if product_name:
        product_name = re.sub(r"\s*[\$*#_]+\s*$", "", product_name).strip()
    if not product_name:
        return None, "Product name missing"

    ingredient = None
    strength = None
    dosage_form = None

    # Locate the first occurrence of each labelled field.
    reg_number = find_after(lines, "NRN")
    status = find_after(lines, "Status")
    manufacturer = find_after(lines, "Manufacturer Name")
    expiry = find_after(lines, "Expiry Date")

    # Read fields directly after the product heading.
    values = (
        lines[detail_index + 1:detail_index + 4]
        if detail_index is not None
        else []
    )
    if len(values) >= 3:
        ingredient, strength, dosage_form = values

    if not reg_number or not re.search(r"[A-Za-z0-9]", reg_number):
        return None, "Registration number missing"
    if not status:
        return None, "Registration status missing"

    if expiry:
        match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", expiry)
        expiry = match.group(1) if match else expiry

    record = {
        "product_name": product_name,
        "active_ingredients": ingredient,
        "strength": strength,
        "dosage_form": dosage_form,
        "nafdac_reg_number": reg_number,
        "manufacturer": manufacturer,
        "registration_status": status,
        "registration_expiry_date": expiry,
        "source_name": "NAFDAC Greenbook",
        "source_url": url,
        "retrieved_at": TODAY,
        "dataset_label": "curated_source_record",
    }
    return record, None


def main(argv=None):
    cli = argparse.ArgumentParser(
        description=(
            "Build a non-destructive candidate registry snapshot from "
            "NAFDAC Greenbook product pages."
        )
    )
    cli.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse records, but do not write the candidate file.",
    )
    args = cli.parse_args(argv)
    live_path = DATA / "registry.json"
    live = json.loads(live_path.read_text(encoding="utf-8"))
    records = list(live["records"])
    seen_numbers = {
        normalize_registration_number(r["nafdac_reg_number"])
        for r in records
    }
    seen_urls = {r["source_url"] for r in records}
    candidate_sources = list(live["sources"])
    source_pairs = {(s["name"], s["url"]) for s in candidate_sources}

    links = []
    failures = []

    for listing_url in LISTING_URLS:
        try:
            parser = TextParser()
            parser.feed(fetch(listing_url))
            for link in parser.links:
                href = urljoin(listing_url, link["href"])
                if "/products/details/" not in href:
                    continue
                label = " ".join(link["text"])
                links.append((href, label))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            failures.append(f"Listing failed: {listing_url}: {exc}")

    # Stable order; a source URL is fetched at most once.
    unique_links = []
    link_urls = set()
    for url, label in links:
        if url not in link_urls and url not in seen_urls:
            link_urls.add(url)
            unique_links.append((url, label))

    added = 0
    attempted = 0
    target_additions = max(0, TARGET_COUNT - len(records))

    for url, label in unique_links:
        if added >= target_additions:
            break
        attempted += 1
        try:
            record, error = parse_product(url, fetch(url))
        except (HTTPError, URLError, TimeoutError, OSError, ValueError) as exc:
            failures.append(f"Fetch failed: {url}: {exc}")
            continue

        if error:
            failures.append(f"Parse failed: {url}: {error}")
            continue

        normalized = normalize_registration_number(record["nafdac_reg_number"])
        if normalized in seen_numbers:
            continue

        seen_numbers.add(normalized)
        seen_urls.add(url)
        records.append(record)
        added += 1

        pair = (record["source_name"], record["source_url"])
        if pair not in source_pairs:
            source_pairs.add(pair)
            candidate_sources.append({"name": pair[0], "url": pair[1]})

        # Be polite to the public source; this is an offline ingestion step.
        time.sleep(0.15)

    candidate = {
        "dataset_name": live["dataset_name"],
        "schema_version": live["schema_version"],
        "retrieved_at": TODAY,
        "sources": candidate_sources,
        "records": records,
    }
    output = DATA / "candidates" / "registry_candidate.json"
    if not args.dry_run:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(candidate, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    print(f"Existing records: {len(live['records'])}")
    print(f"New records added: {added}")
    print(f"Candidate total: {len(records)}")
    print(f"Product pages attempted: {attempted}")
    if args.dry_run:
        print(f"Dry run: candidate was not written ({output})")
    else:
        print(f"Candidate written: {output}")
    print(f"Source/parse issues: {len(failures)}")
    for issue in failures[:20]:
        print("WARNING:", issue)

    if len(records) < TARGET_COUNT:
        print(
            f"INCOMPLETE: need {TARGET_COUNT - len(records)} more "
            "records. Do not promote this candidate yet."
        )
        return 2

    print("\nValidate this candidate before promoting it to the live dataset.")

    if failures:
        print(
            "INGESTION INCOMPLETE: one or more source pages failed to fetch "
            "or parse. Review the warnings before using this candidate."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
