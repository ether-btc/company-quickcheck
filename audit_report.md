# Company-Quickcheck Code Audit Report

**Date:** 2026-05-13
**Scope:** All source files in `/home/hermes-pi/company-quickcheck/`
**Files Reviewed:** 17 files (10 source, 4 test, 1 config JSON, 2 build files)

---

## 1. SECURITY

### SEC-01: API Key Loaded at Module Import Time as Global
**Severity:** MEDIUM  
**Files:** `api.py` line 29, `config.py` line 89  
**Issue:** `API_KEY = config.get_api_key()` is evaluated at import time in `api.py:29`. If the key is missing, it raises `ValueError` before any code runs. More critically, `config.py:89` creates a global singleton `config = Config()` at module import, meaning the API key is loaded once and baked into a module-level constant. This makes it accessible via `from company_quickcheck.api import API_KEY` (test_api.py line 17 actually does this). The key is also stored in plaintext in config file and environment variable.  
**Fix:** Implement lazy loading for the API key. Replace the module-level `API_KEY` with a function `def _get_api_key(): return config.get_api_key()` called at request time. Do not export `API_KEY` in `__all__`.

### SEC-02: API Key Passed to Subprocess via Command-Line Argument
**Severity:** HIGH  
**File:** `api.py` lines 172-181  
**Issue:** In `search_stealth_core()`, the API key is base64-encoded and passed as part of a `--headers` JSON argument to the `stealth-core` subprocess. Command-line arguments are visible in `/proc/<pid>/cmdline` to any user on the same system, and may be logged in shell history, audit logs, or process monitors.  
**Fix:** Pass the API key via an environment variable to the subprocess using `subprocess.run(..., env={...})` instead of via command-line arguments. Or write to a temporary config file readable only by the calling process.

### SEC-03: Subprocess Call with Unvalidated URL Construction
**Severity:** MEDIUM  
**File:** `api.py` line 169  
**Issue:** `full_url = f"{BASE_URL}/registered-companies/find?company-name={encoded_name}&limit={limit}"` — while `urllib.parse.quote()` is used, the full URL is then passed as a command-line argument to an external binary (`stealth-core`). If `BASE_URL` is ever configured to something user-controlled, this could lead to injection. The `name` parameter, though URL-encoded, could still carry malicious intent into the external tool.  
**Fix:** Validate `BASE_URL` against a whitelist of known hosts. Add input validation on the `name` parameter length and character set before passing to subprocess.

### SEC-04: Hardcoded Absolute Paths
**Severity:** MEDIUM  
**Files:** `autonomous_batch.py` lines 18-27, `config.py` line 85  
**Issue:** `autonomous_batch.py` hardcodes `/home/hermes-pi/company-quickcheck`, `/home/hermes-pi/.hermes/.env`, and `/srv/sync/...` paths. `config.py` line 85 hardcodes `/home/hermes-pi/.hermes/projects/stealth-core/config/config.yaml`. These paths leak the deployment environment and will break on any other machine. The `.env` file path may contain credentials (API keys).  
**Fix:** Make all paths configurable via environment variables or config.yaml. Use `pathlib.Path` with defaults relative to the user's home directory. Never hardcode `.env` file locations.

### SEC-05: Plaintext Environment File Parser
**Severity:** MEDIUM  
**File:** `autonomous_batch.py` lines 33-44  
**Issue:** The `load_env()` function manually parses a `.env` file using simple `line.split("=", 1)`. There is no validation, no quoting support, and no protection against injection. If the file contains malicious entries like `PATH=/tmp/evil`, it could override critical environment variables. The parsed values are placed directly into the environment dict passed to subprocess calls.  
**Fix:** Use the `python-dotenv` library which handles quoting and edge cases properly. Validate each key against a whitelist of expected environment variable names before loading.

### SEC-06: YAML Configuration Loaded Without Safe Loading Validation
**Severity:** LOW  
**File:** `config.py` line 24  
**Issue:** While `yaml.safe_load()` is used (good), there's no validation of the YAML content structure. A malformed YAML file could cause silent failures or overwrite expected config keys with unexpected types.  
**Fix:** Add schema validation after loading, using e.g. `jsonschema` or explicit type checks for each expected key.

### SEC-07: Web Scraping Phase Has No Input Sanitization
**Severity:** MEDIUM  
**File:** `autonomous_batch.py` lines 316-324  
**Issue:** In `run_phase3()`, the Firmenbuchnummer is directly interpolated into a URL: `url = "https://www.firmenbuch.at/firma/" + fb_clean`. While `fb_clean` is sanitized (`lstrip("fn").lower()`), it is not validated to contain only URL-safe characters. A malformed Firmenbuchnummer from a corrupted input file could produce an invalid or malicious URL. The `curl` subprocess receives this URL directly.  
**Fix:** Validate `fb_clean` against a strict regex (e.g., `^[a-z0-9]+$`) before constructing the URL. Use `urllib.parse.quote()` on the FB number.

---

## 2. BUGS

### BUG-01: `search_opendata` 401 Exception Is Caught and Swallowed
**Severity:** HIGH  
**File:** `api.py` lines 140-147  
**Issue:** On line 140, a `PermissionError` is raised for 401 responses. However, the `except Exception as e:` block on line 145 catches ALL exceptions (including `PermissionError`) and returns `None`, suppressing the authorization failure. The caller receives the same `None` as for a network error and cannot distinguish between "wrong API key" and "network timeout."  
**Fix:** Re-raise `PermissionError` outside the catch block, or add a specific `except PermissionError:` clause that re-raises it.

```python
    except PermissionError:
        raise
    except Exception as e:
        logger.error(f"Error in opendata search: {e}")
        return None
```

### BUG-02: `normalize_address` No-op Regex Pattern
**Severity:** LOW  
**File:** `api.py` line 49, `scripts/firmen_quickcheck.py` line 45  
**Issue:** `addr = re.sub(r"gasse(?=\b)", "gasse", addr)` replaces "gasse" with "gasse" — this is a no-op and wastes CPU cycles. Same pattern in the legacy script.  
**Fix:** Remove this line entirely, or change it to actually do something useful (e.g., expand "g." to "gasse").

### BUG-03: `NameSimilarity._simple_word_match` Uses `.test()` (JavaScript Method)
**Severity:** CRITICAL  
**File:** `correlation.py` line 270  
**Issue:** `cached_re.test(text)` — Python's `re.Pattern` does NOT have a `.test()` method. This method exists in JavaScript. The correct Python method is `.search(text)` or `.match(text)`. This will raise `AttributeError` every time the regex cache path is taken (non-alphanumeric keywords). The only reason this hasn't been caught is that most keywords are alphanumeric and use the fast `in` path on line 258. When a keyword with special characters is processed, this crashes with an unhandled exception inside `search_with_correlation`.  
**Fix:** Change `cached_re.test(text)` to `cached_re.search(text) is not None`.

### BUG-04: `limit=0` Treated as "No Limit" Instead of Zero Rows
**Severity:** LOW  
**File:** `core.py` lines 73, 80, `scripts/firmen_quickcheck.py` line 148, `cli.py` line 36  
**Issue:** `if limit is not None` passes for `limit=0`. Then `df.head(0)` returns an empty DataFrame. But in `cli.py`, `args.limit` defaults to `None`, so this only triggers if the user explicitly passes `--limit 0`. Still, this could be confusing. The real issue is that `limit=""` or `limit=False` (boolean) would also slip through.  
**Fix:** Use `if limit:` instead of `if limit is not None`, or explicitly validate `isinstance(limit, int) and limit > 0`.

### BUG-05: Checkpoint Index Mismatch Between `df.index` and DataFrame Position
**Severity:** MEDIUM  
**File:** `core.py` line 255, `autonomous_batch.py` lines 148, 216  
**Issue:** In `core.py`, the checkpoint uses `(idx + 1) % checkpoint_every`. When `force_start` is used (lines 74-79), the DataFrame is reindexed to start from `force_start`. So for `force_start=100`, the first row has `idx=100`, and `(100 + 1) % 25 = 1` — checkpoint fires after only the first row. Conversely, for `force_start=5`, `(5+1) % 25 = 6` — the first checkpoint fires after row 18 (the 19th row from the original index). This is inconsistent behavior.  
**Fix:** Use the position within the sliced DataFrame for checkpoint calculation instead of the absolute row index. Track `row_number = df.index.get_loc(idx)` if using original indices, or use `enumerate(df.iterrows())` and count position.

### BUG-06: `autonomous_batch.py` Final Checkpoint Uses Undefined `row_idx`
**Severity:** MEDIUM  
**File:** `autonomous_batch.py` line 226  
**Issue:** `save_checkpoint(row_idx, stats)` on line 226 uses `row_idx` after the `for` loop. If the DataFrame is empty (0 rows), `row_idx` was never defined, causing `UnboundLocalError`.  
**Fix:** Initialize `row_idx = -1` before the loop, or use a separate variable like `last_processed_idx` that is updated inside the loop.

### BUG-07: `merge_batches.py` Column Name Detection Is Fragile
**Severity:** MEDIUM  
**File:** `scripts/merge_batches.py` line 21  
**Issue:** `gelo_col = [c for c in new_batch.columns if 'GEL' in c][0]` uses a substring match that could match an unexpected column. If there's a column like "GELESEN" or "ANGELDET", it would be matched instead of "GELÖSCHT". If no column contains "GEL", this raises `IndexError`.  
**Fix:** Use exact column matching: `gelo_col = "GELÖSCHT" if "GELÖSCHT" in new_batch.columns else ...` with a clear error message.

### BUG-08: `correlation.py` `city_aliases` Mapping Has Wrong Direction
**Severity:** LOW  
**File:** `correlation.py` line 59, `correlation_rules.json` line 22  
**Issue:** `"wien": "vienna"` maps "wien" to "vienna" (English). But the API returns German addresses, so the city would be "Wien" not "Vienna". This alias will never match in practice and is functionally meaningless for AT company lookups.  
**Fix:** Remove the "wien" → "vienna" alias, or add the reverse if needed for some input variation.

---

## 3. CODE QUALITY

### CQ-01: Duplicate `normalize_address` and `address_confidence` Functions
**Severity:** MEDIUM  
**Files:** `api.py` (lines 34-103) vs `scripts/firmen_quickcheck.py` (lines 36-97)  
**Issue:** These two functions are duplicated almost verbatim across the package module and the legacy script. Any bug fix or improvement must be applied in two places. They already diverge in small ways (e.g., the version in `api.py` has a `country` parameter the legacy version lacks).  
**Fix:** The legacy script should import from the package: `from company_quickcheck.api import normalize_address, address_confidence`. Or make the legacy script use `process_batch` from `core.py` directly.

### CQ-02: Duplicate Logging Configuration
**Severity:** LOW  
**Files:** `api.py` lines 21-24, `core.py` lines 20-23, `autonomous_batch.py` lines 11-15  
**Issue:** `logging.basicConfig()` is called in three different modules. This is redundant and may produce duplicate log lines or override each other's settings depending on import order.  
**Fix:** Configure logging once in `__init__.py` or at the CLI entry point. Remove `basicConfig` calls from individual modules.

### CQ-03: Version Mismatch Between `__init__.py` and `pyproject.toml`
**Severity:** MEDIUM  
**Files:** `__init__.py` line 5 (`"0.1.0"`), `pyproject.toml` line 7 (`"1.2.0"`), `setup.py` line 6 (`"0.1.0"`)  
**Issue:** Three different version numbers exist: `0.1.0` in `__init__.py`, `1.2.0` in `pyproject.toml`, and `0.1.0` in `setup.py`. The `--version` CLI flag will report `0.1.0` (from `__init__.py`) because `cli.py` imports `__version__` from `__init__.py`.  
**Fix:** Use a single source of truth. Either use `importlib.metadata.version('company-quickcheck')` for runtime version, or have `__init__.py` be the canonical version. Remove `setup.py` if `pyproject.toml` is the build config.

### CQ-04: Missing Error Handling for `requests.get()` JSON Parsing
**Severity:** LOW  
**File:** `api.py` line 142  
**Issue:** `resp.json()` will raise `requests.exceptions.JSONDecodeError` if the response is not valid JSON. This is caught by the blanket `except Exception` on line 145, but the error message is logged as "Error in opendata search: Expecting value: line 1 column 1..." which is not user-friendly.  
**Fix:** Add explicit `except requests.exceptions.JSONDecodeError` with a more informative error message.

### CQ-05: `setup.py` Should Be Removed
**Severity:** LOW  
**File:** `setup.py` (entire file)  
**Issue:** The project uses `pyproject.toml` with setuptools build backend. `setup.py` is a legacy file that serves no purpose and can cause confusion about which file controls the build. It also contains a different version number.  
**Fix:** Delete `setup.py` entirely. The entry points and build config are in `pyproject.toml`.

### CQ-06: `correlation.py` Has Unused Import
**Severity:** LOW  
**File:** `correlation.py` line 21 (`from typing import Dict, List, Optional, Any`) - `Any` is never used  
**Issue:** `Any` is imported but never used in the file. This is a minor linting issue but indicates incomplete code review.  
**Fix:** Remove the unused import.

### CQ-07: `search_with_correlation` Passes Redundant Parameters
**Severity:** LOW  
**File:** `api.py` lines 279-288  
**Issue:** `search_with_correlation` accepts `mode` and `min_confidence` parameters, passes them to `build_matcher()`, then passes them AGAIN to `matcher.match()`. The `match()` method already accepts these as optional overrides. This is redundant and confusing.  
**Fix:** Either let `build_matcher` set the defaults and pass `None` to `match()` for overrides, or accept them only once.

---

## 4. TESTING

### TST-01: `test_api.py` Expects `PermissionError` to Be Caught (Wrong Behavior)
**Severity:** MEDIUM  
**File:** `tests/test_api.py` lines 145-153  
**Issue:** `test_401_invalid_key` expects `search_opendata()` to return `None` on 401. But the actual code raises `PermissionError`, which is then caught by the blanket `except` (BUG-01). The test passes "for the wrong reason" — the code path it expects is broken. If BUG-01 were fixed, this test would start failing because the exception propagates.  
**Fix:** Update the test to expect `PermissionError` to propagate: `with self.assertRaises(PermissionError): search_opendata(...)`. Fix BUG-01 simultaneously.

### TST-02: No Test Coverage for `correlation.py`
**Severity:** HIGH  
**Files:** `tests/` directory — no `test_correlation.py` exists  
**Issue:** The entire `correlation.py` (757 lines, 8 classes, 20+ methods) has zero test coverage. This includes `CorrelationMatcher`, `CorrelationRules`, `NameSimilarity`, `AddressNormalizer`, `CompositeConfidence`, `LruRegexCache`, and `MatchResult`. Given BUG-03 (the `.test()` bug), these are completely untested in CI.  
**Fix:** Add comprehensive tests for each class in `test_correlation.py`. At minimum: test `NameSimilarity.score()`, `AddressNormalizer` methods, `CorrelationMatcher.match()` with various candidate scenarios, and the `LruRegexCache` put/get/evict cycle.

### TST-03: No Tests for `config.py`
**Severity:** MEDIUM  
**Files:** `tests/` directory — no `test_config.py`  
**Issue:** The `Config` class has no dedicated tests. Its behavior with missing config files, invalid YAML, and nested key traversal is untested.  
**Fix:** Add `test_config.py` with tests for: loading from non-existent path, loading from valid YAML, `get()` with dotted keys, `get_api_key()` with env var fallback.

### TST-04: No Tests for `autonomous_batch.py`
**Severity:** HIGH  
**Files:** `autonomous_batch.py` — completely untested  
**Issue:** `autonomous_batch.py` (411 lines) has no tests at all, including the VIES check, retry queue, web scrape phase, and merge logic. This file has the most bugs (BUG-06, SEC-04, SEC-05, SEC-07) but zero test coverage.  
**Fix:** Add tests at minimum for `check_vies()`, retry queue save/load, and `merge_to_final()`. Use mock HTTP responses for VIES and web scraping.

### TST-05: No Tests for `scripts/merge_batches.py`
**Severity:** MEDIUM  
**Files:** `scripts/merge_batches.py` — completely untested  
**Issue:** The merge script is untested. Given BUG-07 (fragile column detection), this is risky.  
**Fix:** Add a simple integration test that creates two temporary Excel files and verifies the merge output.

### TST-06: Test Fixtures Use Hardcoded Temporary Files in CWD
**Severity:** MEDIUM  
**File:** `tests/test_core.py` lines 24, 85-87  
**Issue:** `test_core.py` creates `test_input.xlsx` in the current working directory and relies on `tearDown` to clean up. If tests are interrupted, these files remain. `test_fixes.py` properly uses `tempfile.mkdtemp()`.  
**Fix:** Use `tempfile.mkdtemp()` or `tmp_path` fixture (if pytest) for all test file I/O.

### TST-07: No Tests for Edge Cases: Empty Company Name, NaN Handling, Unicode
**Severity:** MEDIUM  
**Files:** Multiple test files  
**Issue:** There are no tests for:
- Empty company names in batch (should set GELÖSCHT=-1)
- Company name being "nan" string vs actual NaN
- Unicode characters in input (German umlauts, special chars)
- API returning malformed responses (missing keys, wrong types)
- Very large batches (memory, checkpoint stability)  
**Fix:** Add parametrized tests covering these edge cases.

### TST-08: `test_cli.py` Does Not Test `main()` or `sys.exit` Paths
**Severity:** LOW  
**File:** `tests/test_cli.py`  
**Issue:** Only `check_company()` and `batch_process()` are tested. `main()` is never tested, nor are the `sys.exit(1)` paths (no command provided, CLI errors). The epilog examples are not validated.  
**Fix:** Add tests for `main()` with various `sys.argv` combinations, including no subcommand and invalid arguments.

---

## 5. RELIABILITY

### REL-01: No Retry Logic for Transient Network Failures
**Severity:** HIGH  
**Files:** `api.py` lines 117-147, `autonomous_batch.py` lines 161-213  
**Issue:** A single failed request (timeout, connection reset, DNS failure) marks the company as error (-1) and moves on permanently. For a batch of 1000+ companies, even a 1% transient failure rate means 10+ companies get wrong results. There is no retry for transient errors.  
**Fix:** Implement retry logic with exponential backoff for recoverable errors (timeouts, 502/503/504, connection errors) — limit to 3 retries. `search_opendata` should accept a `max_retries` parameter.

### REL-02: No Handling for `resp.json()` When API Returns Non-JSON Error Page
**Severity:** MEDIUM  
**File:** `api.py` lines 142  
**Issue:** If the API returns an HTML error page (e.g., CloudFlare 502), `resp.json()` will fail. The blanket exception handler catches this, but the entire request is treated as a fatal error with no retry possibility.  
**Fix:** Check `resp.headers.get("Content-Type", "")` for `application/json` before calling `.json()`. Provide better error messages and retry logic.

### REL-03: Final Excel Save Has 60-Second Timeout That May Be Too Short
**Severity:** LOW  
**File:** `core.py` lines 267-269  
**Issue:** On large DataFrames (1700+ rows), `df.to_excel()` with openpyxl can take significant time. The 60-second ThreadPoolExecutor timeout may fire, leaving the file unwritten after processing all rows. The timeout is also applied to `pd.read_excel` on input (90 seconds), which may be too short for very large files.  
**Fix:** Make the timeout configurable, or increase to 300 seconds. Consider using `csv` output for very large batches.

### REL-04: Checkpoint Write Can Fail Silently on Full Disk
**Severity:** LOW  
**File:** `core.py` lines 257-261  
**Issue:** Checkpoint writes catch `OSError` and log the error, but then continue to try writing the Excel file (line 263). If the disk is full, both writes will fail, but the function continues processing rows thinking it can resume.  
**Fix:** If checkpoint write fails, raise a hard error and stop processing. Do not silently continue when the disk is full.

### REL-05: `autonomous_batch.py` Has No Signal Handling for Graceful Shutdown
**Severity:** MEDIUM  
**File:** `autonomous_batch.py` lines 1-411  
**Issue:** Import of `signal` on line 8 but never used. If the process is killed (SIGTERM, Ctrl-C), the current batch row, checkpoint, and retry queue may be in an inconsistent state. The comment "Designed for overnight autonomous run" makes this especially important.  
**Fix:** Register a SIGTERM/SIGINT handler that saves the current checkpoint, retry queue, and partial DataFrame before exiting.

### REL-06: No Validation That Output File Isn't the Same as Input File
**Severity:** LOW  
**File:** `core.py` lines 27-44  
**Issue:** If the user accidentally passes the same file as input and output, `df.to_excel(output_file)` will overwrite the input mid-processing, corrupting the data. The disk space check on line 49-59 would pass (since the file exists).  
**Fix:** Add `if Path(input_file).resolve() == Path(output_file).resolve(): raise ValueError("Input and output file cannot be the same")`.

---

## 6. PERFORMANCE

### PERF-01: Unnecessary ThreadPoolExecutor Wrapping for Single-Threaded I/O
**Severity:** LOW  
**Files:** `core.py` lines 63-65, 105-107, 267-269  
**Issue:** `ThreadPoolExecutor(max_workers=1)` is used to wrap `pd.read_excel` and `df.to_excel` calls. This adds thread creation/scheduling overhead for zero concurrency benefit. The 90/60 second timeouts are the only benefit, but they could be implemented more efficiently.  
**Fix:** Either use `max_workers=1` with the timeout for its timeout control (document this intent), or use a simple signal-based timeout or the `timeout` parameter from `concurrent.futures` without the executor overhead. Or accept that pandas I/O is blocking and remove the wrapper.

### PERF-02: Sequential API Calls with Fixed Sleep Between Each
**Severity:** MEDIUM  
**Files:** `core.py` line 252, `scripts/firmen_quickcheck.py` line 249  
**Issue:** When adaptive mode is disabled, each company lookup has a fixed `time.sleep(config.get_rate_limit_delay())` (default 1.1s). For 1000 companies, that's 1100 seconds (18+ minutes) of pure sleeping. No parallelism is used despite the API presumably handling multiple connections.  
**Fix:** Consider allowing controlled parallelism (e.g., 2-3 concurrent requests) while respecting rate limits. The adaptive rate limiter already exists and should be the default (it is, but the fixed fallback is wasteful).

### PERF-03: Entire DataFrame Loaded Into Memory Before Processing
**Severity:** LOW  
**Files:** `core.py` line 64-67  
**Issue:** The entire Excel file is loaded into memory at once with `pd.read_excel(input_file)`. For very large files (100K+ rows), this could consume significant memory.  
**Fix:** For memory-constrained environments, consider chunked processing using `pd.read_excel` in chunks (though this requires converting to CSV first, as Excel doesn't support chunking). Add a warning when loading files over a certain size.

### PERF-04: `address_confidence` Uses `normalize_address` Repeatedly
**Severity:** LOW  
**File:** `api.py` lines 71, 75, 76  
**Issue:** Inside `address_confidence`, `normalize_address()` is called multiple times on the same values within a single function call. The results are not cached. This is called once per company per API candidate match.  
**Fix:** Pre-normalize all input values once before the comparison logic, or memoize `normalize_address` with `@functools.lru_cache`.

### PERF-05: `name_similarity._normalize_legal_form` Calls `re.sub` Per Legal Form
**Severity:** LOW  
**File:** `correlation.py` lines 219-221  
**Issue:** For each of the 12+ legal forms, a separate `re.sub` call is made. This compiles a new regex pattern each time (except the `\s+` cleanup). For large batches, this is unnecessary overhead.  
**Fix:** Pre-compile all legal form patterns into a single regex pattern: `rf'\b({"|".join(legal_forms)})\b'` and use a single `re.sub` call.

---

## 7. MAINTAINABILITY

### MAINT-01: Version Number Inconsistency
**Severity:** MEDIUM  
**Files:** `__init__.py:5` (`0.1.0`), `pyproject.toml:7` (`1.2.0`), `setup.py:6` (`0.1.0`)  
**Issue:** Already noted in CQ-03. The version mismatch is confusing for deployment and debugging.  
**Fix:** Single source of truth. Use `importlib.metadata.version()` at runtime.

### MAINT-02: `api.py` Docstring Claims "stealth-core" Integration but API Module Is Named "opendata.host"
**Severity:** LOW  
**File:** `api.py` line 2  
**Issue:** Docstring says "API interactions for opendata.host and stealth-core" — this is accurate but `stealth-core` is a separate, external binary that may or may not exist. The docstring should clarify this is an optional integration.  
**Fix:** Update docstring to clarify optional nature of stealth-core.

### MAINT-03: Stale Comment in `core.py`
**Severity:** LOW  
**File:** `core.py` lines 69-72  
**Issue:** The comment "BUG FIX: Force-start + limit NOOP bug. Old code: df.head(limit) first..." describes a historical fix. The comment references "Old code" that no longer exists in the file. This adds cognitive load when reading.  
**Fix:** Replace with a brief description of the current behavior, or move the historical context to a changelog/commit message.

### MAINT-04: Legacy Script `firmen_quickcheck.py` Not in Sync with Package
**Severity:** MEDIUM  
**File:** `scripts/firmen_quickcheck.py` (284 lines)  
**Issue:** This legacy script is a standalone version of the package with significant differences: no adaptive rate limiting, no correlation matching, no stealth-core support, no disk space check, no ThreadPoolExecutor timeouts. It will increasingly diverge from the maintained package code.  
**Fix:** Either deprecate and remove the legacy script (recommended), or ensure it delegates entirely to the package: `from company_quickcheck.core import process_batch`.

### MAINT-05: `correlation_rules.json` Has `"usage_count": 0` and All Rules in "proposal" State
**Severity:** LOW  
**File:** `correlation_rules.json` lines 42, 77, 100, 129  
**Issue:** All four rules have `usage_count: 0` and `lifecycle.state: "proposal"`. However, the code in `get_active_rules()` includes "proposal" in `ACTIVE_STATES` (correlation.py line 32). This means the rules ARE active despite being in "proposal" state, which defeats the purpose of the lifecycle model. Additionally, `usage_count` is never incremented anywhere in the code.  
**Fix:** Either remove "proposal" from `ACTIVE_STATES`, or implement actual usage counting. Clarify the lifecycle semantics in documentation.

### MAINT-06: Mixed Use of `print()` and `logging`
**Severity:** LOW  
**Files:** Multiple  
**Issue:** The package modules (`api.py`, `core.py`, etc.) use `logging`, but the legacy script uses `print()`, `autonomous_batch.py` uses `logging` with `print`-style string concatenation (`logger.info("[ERR " + str(resp.status_code) + "] " + ...)`), and some modules mix both.  
**Fix:** Standardize on `logging` everywhere. Use f-string formatting for log messages (lazy evaluation): `logger.info("[ERR %d] %s", resp.status_code, name)`.

### MAINT-07: Python 3.8 Type Hint Inconsistency
**Severity:** LOW  
**Files:** `scripts/firmen_quickcheck.py` line 100 vs `api.py` line 106  
**Issue:** `firmen_quickcheck.py` uses `dict | None` (Python 3.10+ syntax) on line 100, while the code claims to support Python 3.8+ in both `pyproject.toml` and `setup.py`. This will cause a `SyntaxError` on Python 3.8/3.9. The package code correctly uses `Optional[Dict]`.  
**Fix:** Change `dict | None` to `Optional[dict]` in `firmen_quickcheck.py` line 100, or add `from __future__ import annotations` at the top of that file.

---

## Summary Statistics

| Category | Critical | High | Medium | Low | Total |
|----------|----------|------|--------|-----|-------|
| SECURITY | 0 | 1 | 5 | 1 | 7 |
| BUGS | 1 | 1 | 3 | 2 | 8 |
| CODE QUALITY | 0 | 0 | 4 | 3 | 7 |
| TESTING | 0 | 2 | 5 | 1 | 8 |
| RELIABILITY | 0 | 2 | 3 | 2 | 7 |
| PERFORMANCE | 0 | 0 | 1 | 4 | 5 |
| MAINTAINABILITY | 0 | 0 | 3 | 4 | 7 |
| **TOTAL** | **1** | **6** | **24** | **17** | **49** |

### Top Priority Fixes

1. **CRITICAL - BUG-03:** `cached_re.test(text)` on `correlation.py:270` — crashes every time a non-alphanumeric keyword is used in correlation matching. One-character fix: `.test` → `.search` is not None.

2. **HIGH - BUG-01:** 401 `PermissionError` silently swallowed in `api.py:145` — wrong API key appears as generic network error.

3. **HIGH - SEC-02:** API key visible in `/proc/*/cmdline` via `stealth-core` subprocess args.

4. **HIGH - REL-01:** No retry for transient failures — single network hiccup = permanent wrong answer.

5. **HIGH - TST-02:** Zero test coverage for 757-line `correlation.py` module.

6. **HIGH - TST-04:** Zero test coverage for 411-line `autonomous_batch.py`.

### Recommended Immediate Actions

1. Fix `cached_re.test(text)` → `cached_re.search(text) is not None` in `correlation.py:270`
2. Re-raise `PermissionError` in `api.py` search_opendata exception handler
3. Add `test_correlation.py` with critical path tests
4. Align version numbers across `__init__.py`, `pyproject.toml`, and `setup.py` (or remove `setup.py`)
5. Pass API key via env var to subprocess instead of CLI argument in `search_stealth_core`
