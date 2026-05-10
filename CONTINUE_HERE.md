# company-quickcheck — Session Report

## Batch Run Completed (May 11, 2026)

### Final Status
- **Total companies**: 150
- **Active (0)**: 65 ✅
- **Deleted (1)**: 48 ❌
- **Not found (-1)**: 37 ⚠️

### What was done
1. Ran full batch on `companies_checked.xlsx` (150 Austrian companies)
2. Re-checked 23 rows that got rate-limited (429) in the initial runs
3. Fixed 23 rows with correct status (active/deleted)
4. 37 companies still have `-1` status — they either:
   - Are genuinely not found in the opendata registry
   - Hit 429 rate limits repeatedly (16 companies got "Could not fetch data" errors)

### Rate Limiting Observations
- opendata.host is aggressive — ~30% of requests hit 429
- Retry-After values up to 57s observed
- The adaptive rate limiter correctly backs off but the API is still quite hostile
- Companies that errored: Cigma, Compuware, MLINE, Novell, British Airways, Equant, GEFCO, Schenker, Saudi Arabian, Morawa, Anton Unterwurzacher + 6 others

### Remaining -1 Companies (37)
These companies could not be definitively classified as active or deleted:
- Alcatel-Lucent AG, Sagemcom Austria GmbH
- InterXion Österreich GmbH, OnTec Software Solutions AG
- Novell Österreich, British Airways Österreich
- And 30 more (see `companies_checked.xlsx` rows with GELÖSCHT=-1)

### Git
- Commit `1c7b69b` pushed: "data: update companies_checked.xlsx - 150 Austrian companies batch verified"
- Data file tracked: `data/companies_checked.xlsx`

### Skill: company-quickcheck
Current capabilities:
- `company-quickcheck check <name>` — single company lookup
- `company-quickcheck batch <input.xlsx> <output.xlsx>` — batch processing
- `company-quickcheck stats <file.xlsx>` — show statistics
- Direct API mode works reliably (stealth-core integration still has issues)
- Adaptive rate limiter with 429-aware backoff
- Checkpoint/resume via `--force-start N`

### Next Steps (for next session)
1. Retry the 16 "Could not fetch" companies with longer delays (30-60s)
2. Fix stealth-core JSON parsing (debug log lines mixed with HTTP body in stdout)
3. Consider: are these 37 -1 companies genuinely delisted/non-existent in Austria?
4. Could cross-reference with Firmenwortkürzel (company number) if available