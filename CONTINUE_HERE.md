# Company-Quickcheck — CONTINUE_HERE
## Session: 2026-05-11 ~01:15 UTC

## Running Process
`proc_c02f766826ba` (PID 1058746)
```bash
cd /home/hermes-pi/company-quickcheck && source venv/bin/activate && set -a && source /home/hermes-pi/.hermes/.env && set +a && python autonomous_batch.py
```

## Checkpoint at ~01:14 UTC
```
last_idx: 374, checked: 50, deleted: 29, active: 21, not_found: 22, errors: 0, skipped_429: 278
Retry queue: ~300 firms in /srv/sync/retry_queue.json
```

## Processing State
- **Phase 1:** Running — opendata.host API, skips 429 immediately (no wait), queues for retry
- **Phase 2:** VIES — pending (waits for Phase 1 completion)
- **Phase 3:** Web scrape — pending
- **Phase 4:** Merge to final — pending

## Key Files
| File | Purpose |
|------|---------|
| `/srv/sync/batch_input_1.xlsx` | 1,473 unchecked firms |
| `/srv/sync/batch_output_1.xlsx` | Phase 1 partial output (374 rows processed) |
| `/srv/sync/batch_output_1.xlsx.checkpoint.json` | Checkpoint state (auto-resume) |
| `/srv/sync/retry_queue.json` | ~300 firms for VIES Phase 2 |
| `/srv/sync/Unternehmen_merged.xlsx` | 1,711 rows (110 known + 1,473 unchecked) |
| `/srv/sync/Unternehmen_sanitized.xlsx` | Source file, 1,711 rows |
| `/srv/sync/Unternehmen_checked.xlsx` | Final output (created on merge) |

## 429 Rate: ~75-80%
Very high. autonomous_batch.py handles by skipping immediately, collecting in retry_queue.

## Resume Instructions
1. Check if running: `ps aux | grep autonomous_batch | grep -v grep`
2. If not running, restart (auto-resumes from checkpoint):
   ```bash
   cd /home/hermes-pi/company-quickcheck && source venv/bin/activate && set -a && source /home/hermes-pi/.hermes/.env && set +a && python autonomous_batch.py
   ```
3. Check progress:
   ```bash
   cat /srv/sync/batch_output_1.xlsx.checkpoint.json
   cat /srv/sync/retry_queue.json | python -c "import json,sys; print(len(json.load(sys.stdin)))"
   ```

## Git Commits This Session
- `53b05fd` — feat: add autonomous multi-layer batch processor with VIES/web fallback

## Time Projection (from checkpoint)
- Phase 1 remaining: ~1,099 rows @ ~40/min = ~27 min
- Phase 2 (VIES): ~5 min
- Phase 3 (Web): ~3 min
- **Total: ~35 min from checkpoint** → done by ~01:50 UTC