# Company-Quickcheck — COMPLETED 2026-05-11 02:31 UTC

## Final Result: 1,711 Austrian Companies Verified

**Output:** `/srv/sync/Unternehmen_checked.xlsx`

| GELÖSCHT | Count | Meaning |
|-----------|-------|---------|
| 0.0 | 1,474 | Active |
| 1.0 | 236 | Deleted |
| -1.0 | 1 | Unknown (WTE Wassertechnik GmbH — VIES invalid, web not found) |

**Processing Pipeline:**
1. Phase 1: opendata.host API — fast pass, 429s skipped to retry queue
2. Phase 2: VIES VAT validation for ~400 retry firms
3. Phase 3: Web scrape (firmenbuch.at) for remaining -1
4. Phase 4: Merge to final output

**429 Rate:** ~75% — handled by skip-immediately + retry queue strategy

**Git Commits:**
- `53b05fd` — feat: add autonomous multi-layer batch processor with VIES/web fallback
- `30d44b4` — docs: update CONTINUE_HERE.md - batch in progress, checkpoint 374/1473

## Quick Stats
- Total processed: 1,711
- Active (0): 1,474 (86.1%)
- Deleted (1): 236 (13.8%)
- Unknown (-1): 1 (0.1%)

## Verification Command
```bash
cd /home/hermes-pi/company-quickcheck && source venv/bin/activate && python -c "
import pandas as pd
df = pd.read_excel('/srv/sync/Unternehmen_checked.xlsx')
print(df['GELÖSCHT'].value_counts(dropna=False).sort_index())
"