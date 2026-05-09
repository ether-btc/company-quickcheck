# Company QuickCheck — SPEC

**Owner:** ether-btc
**Repo:** `github.com/ether-btc/company-quickcheck`
**Language:** Python 3 (standalone CLI)
**Core:** Batch Austrian company status checker via opendata.host API
**Phase:** 1 — Project foundation

---

## 1. Overview

Company QuickCheck validates Austrian company registration status using the opendata.host API and adds a `GELÖSCHT` column to input spreadsheets.

It complements the existing Hermes Agent skill `firmen-quickcheck` but as a standalone project with proper versioning, planning docs, and a clean separation from the agent codebase.

---

## 2. Functional Requirements

### FR-001: Single Company Check
```
python -m company_quickcheck check "Alcatel Austria AG"
python -m company_quickcheck check --uid ATU14713005
```
Output: JSON with company name, reg-no, reg-status, address, legal form.

### FR-002: Batch Spreadsheet Processing
```
python -m company_quickcheck batch input.csv output.csv
python -m company_quickcheck batch input.xlsx output.xlsx
```
Input columns: Firmenname, Firmenbuchnr, UID_Nummer, Hauptadr_Strasse, Hauptadr_PLZ, Hauptadr_Ort
Output: same columns + GELÖSCHT (1=cancelled/deleted, 0=registered/active, -1=not found)

### FR-003: Checkpoint + Resume
- Checkpoint every 25-50 rows to output file + `.checkpoint.json`
- `--resume` flag to continue from last checkpoint
- `--force-start N` to skip to row N

### FR-004: stealth-core HTTP Integration
- Use stealth-core HTTP client for API calls (fingerprint, rate limiting)
- Fallback to direct requests if stealth-core unavailable
- Via subprocess: `stealth-core fetch <url>` or HTTP client library

---

## 3. Data Sources

### Primary: opendata.host
- **Base URL**: `https://api.opendata.host/1.0`
- **Endpoint**: `/registered-companies/find`
- **Auth**: HTTP Basic — API key as username, empty password
- **Rate limit**: ~1 request/second
- **Status field**: `reg-status` → `registered` (active) | `cancelled` (deleted)

### Fallback: Ediktsdatei
- Austrian insolvency register (official public registry)
- For insolvency checks when opendata.host returns no data

---

## 4. Output Format

| Column | Description |
|--------|-------------|
| Firmenname | Original company name (never updated) |
| Firmenbuchnr | Firmenbuch number (FB-Nr) |
| UID_Nummer | VAT/tax ID |
| Hauptadr_Strasse | Street address |
| Hauptadr_PLZ | Postal code |
| Hauptadr_Ort | City |
| GELÖSCHT | 1=deleted, 0=active, -1=not found |

Firmenname is NEVER updated — only GELÖSCHT is written.

---

## 5. File Layout

```
company-quickcheck/
├── scripts/
│   ├── firmen_quickcheck.py    # main script (standalone, no agent imports)
│   └── merge_batches.py        # batch merge utility
├── .planning/
│   ├── SPEC.md                  # this file
│   ├── ROADMAP.md              # phases and milestones
│   └── STATE.md                # current progress
├── requirements.txt            # pandas, openpyxl, requests, pyyaml
└── README.md                   # quick start
```

---

## 6. Dependencies

```
pandas
openpyxl
requests
pyyaml
```

---

## 7. Phases

| Phase | Description | Status |
|-------|-------------|--------|
| 1 | Project scaffold, planning docs, git repo | TODO |
| 2 | Enhance script: CLI args, proper module structure, stealth-core integration | TODO |
| 3 | Add UK Companies House + US SEC EDGAR support | TODO |
| 4 | README, release, versioning | TODO |

---

## 8. Key Decisions

- **Python over Go**: The existing firmen_quickcheck.py works. No value in rewriting. Python ecosystem (pandas, openpyxl) handles spreadsheet I/O natively.
- **Subprocess over library**: `stealth-core fetch` as a subprocess keeps the projects cleanly decoupled.
- **Standalone**: Does NOT import from hermes-agent or other agent modules. Invoked directly.
- **Checkpoint-first**: Every batch run saves progress incrementally to survive interruptions.