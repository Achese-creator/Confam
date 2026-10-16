# CONFAM

**NAFDAC verifies the number. CONFAM checks what the number represents.**

CONFAM is an AI-assisted Nigerian medicine-package and regulatory evidence checker. It compares information supplied from a medicine package against a curated local snapshot of NAFDAC Greenbook registration records and published medicine safety alerts.

Its purpose is to identify discrepancies and surface relevant evidence for further review—not to certify that a medicine is genuine or safe.

## How it works

- Compare a supplied NAFDAC registration number with the local registry snapshot.
- Compare available package claims with the corresponding registration record.
- Identify potentially relevant safety alerts using the product name and an additional identifier when available.
- Return evidence, source links, and limitations to support further review.

## Interpreting results

The backend uses these status labels:

- `no_significant_discrepancy_detected`
- `verification_concern_detected`
- `high_risk_discrepancy_detected`

These are screening outcomes, not authenticity verdicts. A result with no significant discrepancy does not prove that a product is genuine or safe. A concern or high-risk discrepancy is not, by itself, proof that a product is counterfeit.

A copied registration number may belong to a different product. An alert may apply only to a specific batch or scope. Consult the cited source and contact NAFDAC or a qualified pharmacist when further verification is needed.

## Current evidence snapshots

The current curated datasets contain:

- **50** registration records sourced from NAFDAC Greenbook.
- **23** medicine safety-alert records sourced from NAFDAC notices.

These are limited snapshots, not comprehensive coverage of medicines marketed in Nigeria. The dataset audit checks structure and selected integrity properties; it does not independently prove source authenticity, transcription accuracy, completeness, current regulatory status, or the authenticity of an individual package.

## Run the backend locally

From the repository root:

```bash
cd backend
source .venv/bin/activate
python -m uvicorn app:app --reload
```

Create the virtual environment and install the project's declared dependencies first if necessary.

## Tests and dataset audit

Run these commands from `backend` with the virtual environment active:

```bash
python -m pytest -q
python scripts/audit_datasets.py --as-of 2026-10-16
```

The audit date reproduces the current milestone's audit. Use the appropriate date for a later audit.

## Registry snapshot expansion

`scripts/expand_registry_candidate.py` is a network-based ingestion utility. It reads the current registry, attempts to fetch additional product pages from NAFDAC Greenbook, and writes a candidate snapshot under `data/candidates/`. It does not automatically replace the live registry.

Use `--help` to inspect its options. `--dry-run` still fetches source pages; it only prevents writing the candidate file. Review and validate a candidate before promoting it to the live dataset.

Generated candidate JSON files are local working artifacts and are not committed by default.

## Limitations

- Registry and alert coverage is incomplete.
- A reachable source URL does not prove that a record was transcribed correctly.
- A registration-number match does not establish that the physical package is authentic.
- Images alone cannot establish chemical composition, ingredient concentration, contamination status, or supply-chain history.
- An absent alert match does not establish safety.

CONFAM is an independent evidence-assistance project, not an official NAFDAC verification service. It does not replace regulatory advice, laboratory testing, or professional judgement.