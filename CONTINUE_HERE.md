# Company QuickCheck - Session Continuation

## **NEW: Session 2026-07-18 — Day-1 Recheck (1,711 canonical recovered)** → see `SESSION-2026-07-18.md`

Canonical 1,711-row source recovered from `/srv/sync/completed/companies_checked_test10.7z`.
50 rows processed (9 classified), 1,661 rows pending resumable recheck.
Wiki: `~/wiki/audits/company-quickcheck-day1-recheck-2026-07-18.md`.
Pipeline lives in `/srv/sync/company-recheck-2026-07/`. **No code in this repo was modified.**

## Last Session: 2026-05-13

### Commit: 535387f (master pushed to origin)
**URL:** https://github.com/ether-btc/company-quickcheck/commit/535387f

### What was done:
- OCR audit scan using `@opencodereview/cli` v2.1.1 (raye-deng) — score 75/100 (C PASSED)
- Batch of 8 critical/medium bug fixes applied and tested
- All 55 unit tests pass

### Fixes applied:
1. **BUG-03 (CRITICAL):** correlation.py `.test()` → `.search()`
2. **BUG-01 (HIGH):** api.py 401 PermissionError re-raise
3. **SEC-02 (HIGH):** stealth-core API key via temp file (0o600)
4. **BUG-06 (MEDIUM):** autonomous_batch.py row_idx UnboundLocalError
5. **BUG-07 (MEDIUM):** merge_batches.py robust column detection
6. **BUG-08 (MEDIUM):** city_aliases wrong direction (vienna→wien)
7. **BUG-02 (MEDIUM):** normalize_address no-op regex → g. abbreviation
8. **CQ-03 (MEDIUM):** Version mismatch fixed via importlib.metadata

## Session: 2026-06-07

### Commit: 6589c2c — pushed to origin/master
**URL:** https://github.com/ether-btc/company-quickcheck/commit/6589c2c
**Working directory:** ~/company-quickcheck
**GH auth:** working (ether-btc account via SSH, ghp_ token)

### Implemented: Parallel batch mode (PERF-02)

**Goal:** Add `--workers N` option to batch command for concurrent API calls via ThreadPoolExecutor, with shared AdaptiveRateLimiter for automatic 429 backoff.

### Changes:

**`company_quickcheck/rate_limiter.py` — thread safety**
- Added `threading.Lock` to `AdaptiveRateLimiter`
- `wait()` reads delay under lock, sleeps outside (so workers don't serialise on each other)
- `record_response()` and `reset()` wrap state mutations in lock
- Extracted `_record_response_locked()` for internal use

**`company_quickcheck/core.py` — process_batch refactor**
- New `RowResult` dataclass decouples worker computation from main-thread df/stats updates
- New `_process_row()` pure function: side-effect-free except for logging + API call
- New `_apply_result()` and `_write_checkpoint()` helpers
- `process_batch()` accepts new `workers` parameter (default 1 = unchanged behaviour)
- workers=1: byte-for-byte identical sequential path
- workers>1: ThreadPoolExecutor + as_completed, main thread handles df writes + checkpoint
- KeyboardInterrupt cancels in-flight futures cleanly

**`company_quickcheck/cli.py` — --workers flag**
- New `--workers N` option on `batch` subcommand (default 1)
- Updated epilog with parallel example

**Tests added (8 new tests, all 164 pass)**
- `test_rate_limiter.py`: `test_concurrent_record_response_keeps_delay_in_bounds`, `test_concurrent_wait_does_not_serialize_workers`
- `test_core.py`: `test_workers_zero_or_negative_raises`, `test_process_batch_parallel_matches_sequential`, `test_process_batch_parallel_handles_worker_exception`, `test_process_batch_parallel_actually_concurrent` (proves 4x speedup with timing assertion), `test_process_batch_parallel_no_adaptive_shares_fixed_delay`
- `test_cli.py`: `test_batch_process_workers_passed_through`

**Documentation**
- README: new "Parallel Mode" section with throughput table and caveats
- SKILL.md updated in both `data-science/company-quickcheck/` and `data-science/firmen-quickcheck/`

### Verified
- All 164 tests pass
- Parallel test proves 4x speedup (4 workers × 0.3s latency = 0.3-0.5s wall, not 1.2s)
- Existing audit fixes preserved (REL-04, REL-06, BUG-05 work in both paths)
- Sequential path (workers=1) produces identical output to pre-change code
- End-to-end smoke test: sequential and parallel produce identical GELÖSCHT for same input
- Pushed to origin/master (commit 6589c2c)

### Note re: 2026-06-03 entry
The earlier "no GH auth token" / "git push fails with No such device or address" claim
was incorrect. `gh auth status` shows the ether-btc account is logged in and has been
working since at least 2026-05-22. The push failure was a transient network issue,
not an auth issue. Auth is fine; no need to "configure GH auth" as the 2026-06-03
entry suggested.

### Next session candidates
- CI/CD pipeline (no GitHub Actions workflow yet)
- VIES VAT validation as a third data source
- More TST-07 edge case tests (NaN strings, Unicode, malformed responses)
- Type checking with mypy (the @dataclass RowResult would benefit)

## Session: 2026-06-03

### Commit: (local, not yet pushed — no GH auth token)
**Working directory:** ~/company-quickcheck

### Fixed in this session (all tests passing):
- **REL-06:** Same-file check added to `process_batch()` in core.py — raises `ValueError` if input==output
- **REL-05:** autonomous_batch.py now has `_signal_handler` / `_install_signal_handlers()` for SIGTERM/SIGINT graceful shutdown
- **REL-04:** Checkpoint write failure in core.py now raises `RuntimeError` instead of silently continuing
- **CQ-07:** Removed redundant `mode` and `min_confidence` params from `matcher.match()` call in `search_with_correlation()`
- **BUG-08:** Fixed city_aliases in cr-at-002 rule: `"wien": "vienna"` → `"vienna": "wien"` (was backwards)
- **MAINT-07:** `scripts/firmen_quickcheck.py` — `dict | None` → `Optional[Dict]` (Python 3.8 compatible), added `from typing import Dict, List, Optional`
- **BUG-02:** Added `g.` → `gasse`, `pl.` → `platz`, `av.` → `allee` abbreviations in `normalize_address()`
- **CQ-06:** Removed unused `Any` import from `correlation.py` typing line
- **MAINT-04:** `scripts/firmen_quickcheck.py` re-sync'd with latest API (same search logic)
- Added test for autonomous_batch.py signal handler (graceful shutdown)
- Updated pyproject.toml version to 1.1.2

### GH Auth Status:
- `gh auth status` → blank output (no token configured)
- `git push` fails with "No such device or address"
- RTK repos (stealth-core, octocode-mcp-pilot) were listed but no action taken (no auth)
- All RTK issues: stealth-core #6 open, octocode-mcp-pilot has no issues

### Remaining from audit (not yet addressed):
- **CQ-01:** Duplicate normalize_address in legacy scripts
- **CQ-02:** Duplicate logging.basicConfig across modules
- **CQ-04:** Missing JSONDecodeError handling (partially done)
- **CQ-05:** setup.py legacy file should be removed
- **REL-01:** No retry logic for transient network failures
- **MAINT-06:** Mixed print() and logging usage
- **TST-02:** Zero test coverage for correlation.py (757 lines)
- **TST-03:** No tests for config.py
- **TST-04:** No tests for autonomous_batch.py (411 lines) — partially addressed (signal handler test added)
- **TST-05:** No tests for merge_batches.py
- **TST-06:** Test fixtures use hardcoded temp files in CWD

### Tooling installed:
- OCR v2.1.1 (raye-deng/open-code-review) at `~/.local/bin/ocr`
- Spencermarx's @open-code-review/cli removed

### How to resume:
1. `cd ~/company-quickcheck`
2. `./venv/bin/python -m pytest tests/ -v` (55+ tests should pass)
3. Pick remaining items from the list above
4. Configure GH auth token to enable `git push` and issue management
