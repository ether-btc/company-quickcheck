# Stealth-Core Integration — CONTINUE_HERE

## What
Connect `company-quickcheck` to `stealth-core` so opendata.host API calls use stealth headers (browser UA, TLS fingerprints, rate limiting) via subprocess relay.

## Choice Made
**Option B (subprocess relay)** — company-quickcheck calls `stealth-core fetch <full-url>` with `--headers '{"Authorization":"Basic <api_key>"}'` and `--config <stealth-config-path>`. stealth-core adds its own stealth headers (UA, accept, etc.) to the request, making the API call look like a real browser.

## What's Done
All analysis complete. No code changed yet. Key findings:

### stealth-core details
- Binary: `/home/hermes-pi/.hermes/projects/stealth-core/target/release/stealth-core`
- Config: `/home/hermes-pi/.hermes/projects/stealth-core/config/config.yaml`
- `stealth-core fetch` accepts `--config <path>` and `--headers <json>` flags
- **reqwest APPENDS headers** (doesn't replace) — config headers + custom headers coexist
- **No stealth-core code changes needed** — already works as designed
- Output on stdout: JSON debug log lines + raw HTTP response (status + headers + body)

### opendata.host auth
- HTTP Basic Auth: `Authorization: Basic base64(api_key + ":")`
- Base URL: `https://api.opendata.host/1.0`
- Full endpoint: `https://api.opendata.host/1.0/registered-companies/find?company-name=<name>&limit=5`

### company-quickcheck files
- `company_quickcheck/api.py` — `search_stealth_core()` at line ~148: **modify this**
- `company_quickcheck/config.py` — add `get_stealth_core_config_path()` helper
- `company_quickcheck/core.py` — `process_batch()` accepts `use_stealth` flag, passes to `search_company()`
- `company_quickcheck/cli.py` — `--stealth` flag exists, no changes needed

## Implementation Plan

### Step 1 — config.py: add stealth_core config path helper
```python
def get_stealth_core_config_path(self) -> str:
    default = Path(__file__).parent.parent / "config" / "config.yaml"
    return self.data.get("stealth_core", {}).get("config_path", str(default))
```
Add to `~/.hermes/config.yaml`:
```yaml
stealth_core:
  config_path: "/home/hermes-pi/.hermes/projects/stealth-core/config/config.yaml"
```

### Step 2 — api.py: rewrite search_stealth_core()
Current (broken): builds `stealth-core fetch /registered-companies/find?...` with no headers/config
Target: full URL + Authorization header + config path
```python
def search_stealth_core(name: str, limit: int = 5) -> Optional[Dict]:
    stealth_config = Config().get_stealth_core_config_path()
    encoded_name = urllib.parse.quote(name, safe='')
    api_key = Config().get_api_key()
    auth_header = f"Basic {base64.b64encode(f'{api_key}:'.encode()).decode()}"

    cmd = [
        "stealth-core",
        "--config", stealth_config,
        "fetch",
        f"{BASE_URL}/registered-companies/find?company-name={encoded_name}&limit={limit}",
        "--headers", json.dumps({"Authorization": auth_header})
    ]
    # execute, parse JSON debug lines + HTTP body from stdout
```

### Step 3 — Parse stealth-core stdout
stealth-core prints JSON log lines then raw HTTP response. Need to:
- Skip lines until body starts (after blank line after headers)
- Parse JSON body

### Step 4 — Test
```bash
python -m company_quickcheck check "Alcatel Austria AG" --stealth
```

## Relevant Files
- `/home/hermes-pi/company-quickcheck/company_quickcheck/api.py` — main change
- `/home/hermes-pi/company-quickcheck/company_quickcheck/config.py` — add helper
- `/home/hermes-pi/.hermes/projects/stealth-core/config/config.yaml` — stealth config
- `/home/hermes-pi/company-quickcheck/.planning/stealth-core-integration-plan.md` — old plan (Option A, superseded)

## GitHub
- Repo: `ether-btc/company-quickcheck`
- Branch: master
- All done — nothing uncommitted except this file

## Next Action
Start with Step 1: read current `api.py` and `config.py` in full, then implement changes.