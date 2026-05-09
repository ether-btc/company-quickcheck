# Company QuickCheck — State

**Project:** github.com/ether-btc/company-quickcheck
**Last updated:** 2026-05-09

## Current Status

### Script: `scripts/firmen_quickcheck.py`
- Copied from `~/.hermes/skills/data-science/firmen-quickcheck/scripts/`
- Functional, handles batch Excel/CSV processing with checkpointing
- Uses opendata.host API (HTTP Basic Auth, `/registered-companies/find`)
- Outputs GELÖSCHT column (1=cancelled, 0=active, -1=not found)

### Git: Uncommitted
```
?? .
?? scripts/
```

## Phase 1 Progress (In Progress — 2026-05-09)

### Steps

| Step | Description | Status |
|------|-------------|--------|
| 1 | Copy firmen_quickcheck.py → scripts/ | ✓ Done |
| 2 | Create planning docs (SPEC, ROADMAP, STATE) | ✓ Done |
| 3 | requirements.txt | TODO |
| 4 | README.md | TODO |
| 5 | Git init + push to GitHub | TODO |

**Overall Progress:** 10%

## Phase Status

**Phase:** 1 (Foundation) — In Progress
**Overall:** 0%

## Key Decisions

- Python over Go (existing working script, pandas/openpyxl ecosystem)
- Subprocess for stealth-core integration (not library import)
- Standalone — no agent module imports
- opendata.host as primary, Ediktsdatei as fallback

## Dependencies (requirements.txt)

```
pandas
openpyxl
requests
pyyaml
```

## API: opendata.host

- **URL**: `https://api.opendata.host/1.0/registered-companies/find`
- **Auth**: HTTP Basic — key as username, empty password
- **Response**: `{companies: [{reg-no, reg-status, business-name, legal-form, business-address}]}`
- **Status**: `registered` = active (GELÖSCHT=0), `cancelled` = deleted (GELÖSCHT=1)

## Next Action

Phase 1 Step 3: Create requirements.txt and README.md, then git init + push.