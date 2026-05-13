# firmen-quickcheck — CONTINUE_HERE.md

## Session: May 13, 2026 — Core Bug Fixes (v1.2)

### What Changed

**3 critical bugs fixed in `core.py`**, all with tests and GitHub pushed.

| Priority | Bug | Fix | Status |
|----------|-----|-----|--------|
| 1 | `--force-start N --limit M` = 0 rows (NOOP) | `df.iloc[force_start:force_start+limit]` + reindex | ✅ Fixed + tested |
| 2 | `--resume` unaware of completed output reads `GELÖSCHT`, resumes from first NaN gap | Reads existing output, resumes from first NaN gap | ✅ Fixed + tested |
| 3 | Disk-full → corrupt Excel output | `shutil.disk_usage()` pre-check, aborts if <1GB | ✅ Fixed + tested |

**New test file**: `tests/test_fixes.py` — 7 unit tests covering all 3 fixes.
**Test suite**: 55/55 pass (48 existing + 7 new).
**`.gitignore`**: Added (was missing — pycache, venv, dist files no longer tracked).

### Commits

| SHA | Message |
|-----|---------|
| `cef7a70` | fix(core): 3 critical bug fixes — force-start/limit NOOP, smart resume, disk space check |
| `b6da76b` | chore: add `.gitignore` for pycache, venv, dist, pytest, IDE files |

**Branch**: `master` → pushed to `origin/master` (`ether-btc/company-quickcheck`).
**Tag**: None yet — consider `git tag v1.2.0` when ready for release.

### Remaining Work (Lower Priority)

| Issue | Description | Effort |
|-------|-------------|--------|
| Rate limiter state loss on resume | `AdaptiveRateLimiter` resets to 1.1s on each `process_batch()` call. See `autonomous_batch.py` two-phase retry queue pattern. | Medium |
| Output file overwrite on fresh runs | `--force-start 0 --limit 100` on file with rows 100-149 filled still overwrites rows 100-149. Use `scripts/merge_batches.py` as workaround. | Low |
| `pyproject.toml` version | Still at `0.1.0` — should bump to `0.2.0` or `1.2.0` to match skill. | Trivial |

### How to Resume

```bash
cd /home/hermes-pi/company-quickcheck
source venv/bin/activate

# Verify state
git pull origin master
python -m pytest tests/ -v

# Quick smoke test
python -m company_quickcheck check "Wienerberger AG"
```

### Files Modified

- `company_quickcheck/core.py` — row slicing fix, smart resume, disk check
- `tests/test_fixes.py` — 7 new tests (new file)
- `.gitignore` — new file
- `~/.hermes/skills/data-science/firmen-quickcheck/SKILL.md` — version 1.2, docs updated

### Key Artifacts

- Input: `/tmp/companies.xlsx` (1,711 rows)
- Output: `/srv/sync/companies_checked.xlsx` (150 rows processed: 56 active, 43 deleted, 51 not found)
- API key: `OPENDATA_API_KEY` in `~/.hermes/.env`
