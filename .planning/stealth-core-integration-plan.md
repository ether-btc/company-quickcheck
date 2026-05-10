# company-quickcheck ↔ stealth-core Integration Plan

## Goal

Connect company-quickcheck to stealth-core so it can pull fresh stealth headers (user-agent, TLS fingerprints, per-domain rate limiting) when querying opendata.host. company-quickcheck makes the actual authenticated API call directly — stealth-core doesn't need to know about the API key.

## Architecture: Two-Phase Fetch

```
company-quickcheck                  stealth-core (HTTP server)
      │                                    │
      │  1. GET /headers                  │
      │─────────────────────────────────>│
      │     ← { user_agent, headers }    │
      │                                    │
      │  2. requests.get(                 │
      │       url,                        │
      │       auth=(api_key,""),          │  ← opendata.host API call
      │       headers=stealth_headers    │     (direct, not through stealth-core)
      │     )                             │
      │─────────────────────────────────────────────────────────> opendata.host
      │                                    │
```

Stealth-core runs as HTTP server. company-quickcheck calls `GET /headers` to get fresh stealth headers, then makes the direct authenticated API call using those headers + the API key. Rate limiting for opendata.host is still handled by company-quickcheck's `AdaptiveRateLimiter`.

---

## Phase 1: stealth-core — Add `GET /headers` endpoint

**File:** `src/api/mod.rs`

Add a new route that returns the current stealth headers from config:

```rust
// ── Stealth headers route ──────────────────────────────────────────
let stealth_headers_route = warp::path!("headers")
    .and(warp::get())
    .and(with_correlation_id())
    .map(move |correlation_id: String| {
        let headers = engine.get_stealth_headers();
        warp::reply::json(&serde_json::json!({
            "headers": headers,
            "correlation_id": correlation_id
        }))
    });
```

Add `get_stealth_headers()` to `StealthEngine` in `src/engine/mod.rs`:

```rust
/// Returns the configured stealth headers as a Map.
pub fn get_stealth_headers(&self) -> std::collections::HashMap<String, String> {
    let mut h = std::collections::HashMap::new();
    h.insert("user-agent".to_string(), self.config.stealth.user_agent.clone());
    for (k, v) in self.config.stealth.headers.as_object().unwrap() {
        if let Some(v_str) = v.as_str() {
            h.insert(k.clone(), v_str.to_string());
        }
    }
    h
}
```

Register the route in the routes combination:
```rust
let routes = health_route
    .or(metrics_route)
    .or(proxy_health_route)
    .or(fetch_route)
    .or(stealth_headers_route);  // add this
```

**Verification:**
```bash
cd /home/hermes-pi/.hermes/projects/stealth-core
STEALTH_CORE_CONFIG=config/config.yaml ./target/release/stealth-core serve &
sleep 2
curl http://localhost:8000/headers
# Expected: {"headers":{"user-agent":"...","accept":"...","accept-language":"...","accept-encoding":"...","dnt":"...","upgrade-insecure-requests":"..."},"correlation_id":"..."}
```

---

## Phase 2: company-quickcheck — Use two-phase fetch

**File:** `company_quickcheck/api.py`

Add a new function that calls the stealth-core server's `/headers` endpoint when running in server mode:

```python
STEALTH_CORE_SERVER = "http://localhost:8000"  # default, overridable via env

def get_stealth_headers_from_server() -> Optional[Dict[str, str]]:
    """Fetch fresh stealth headers from a running stealth-core server."""
    try:
        resp = requests.get(f"{STEALTH_CORE_SERVER}/headers", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return data.get("headers", {})
        return None
    except Exception as e:
        logger.warning(f"Failed to get stealth headers from server: {e}")
        return None

def search_opendata_with_stealth(name: str, limit: int = 5,
                                  rate_limiter=None) -> Optional[Dict]:
    """Two-phase: get headers from stealth-core, then make direct authenticated call."""
    headers = get_stealth_headers_from_server()
    if not headers:
        logger.warning("Could not get stealth headers, falling back to default")
        headers = {}

    if rate_limiter is not None:
        rate_limiter.wait()

    resp = requests.get(
        f"{BASE_URL}/registered-companies/find",
        params={"company-name": name, "limit": limit},
        auth=(API_KEY, ""),
        headers=headers,  # apply stealth headers
        timeout=20,
    )
    if rate_limiter is not None:
        rate_limiter.record_response(resp.status_code, dict(resp.headers))

    if resp.status_code == 429:
        logger.warning("Rate limited (429)")
        return None
    if resp.status_code == 401:
        raise PermissionError("Invalid API key (401 Unauthorized)")
    resp.raise_for_status()
    return resp.json()
```

**Environment variables:**
- `STEALTH_CORE_SERVER` — URL of stealth-core HTTP server (default: `http://localhost:8000`)
- `USE_STEALTH_CORE=server"` (or `use_stealth="server"`) — new mode that uses two-phase fetch
- `STEALTH_CORE_SERVER_PORT` — optional, to override default port

**CLI change:** `--stealth` flag now accepts optional value: `--stealth` (subprocess mode), `--stealth=server` (HTTP server mode), `--stealth=subprocess` (explicit subprocess)

**Config change:** `config.yaml` gains:
```yaml
stealth_core:
  server_url: "http://localhost:8000"  # for two-phase fetch
  mode: "server"  # or "subprocess", default: "subprocess" (backward-compatible)
```

---

## Files to modify

| File | Change |
|------|--------|
| `stealth-core/src/api/mod.rs` | Add `GET /headers` route |
| `stealth-core/src/engine/mod.rs` | Add `get_stealth_headers()` method |
| `stealth-core/src/api/mod.rs` | Register new route |
| `company_quickcheck/api.py` | Add `get_stealth_headers_from_server()`, `search_opendata_with_stealth()` |
| `company_quickcheck/config.py` | Add `stealth_core` config section |
| `company_quickcheck/cli.py` | Support `--stealth=server` / `--stealth=subprocess` |
| `company_quickcheck/core.py` | Pass `use_stealth="server"` or `"subprocess"` through |

---

## Backward compatibility

- `USE_STEALTH_CORE=1` without `STEALTH_CORE_SERVER` → uses existing subprocess mode
- Existing `--stealth` flag → continues to work as before (subprocess mode)
- `search_company()` signature unchanged — the `use_stealth` bool is replaced with a string enum: `"subprocess"` | `"server"` | `False`

---

## Testing plan

1. Start stealth-core server: `STEALTH_CORE_CONFIG=config/config.yaml ./target/release/stealth-core serve &`
2. Verify `/headers` endpoint: `curl http://localhost:8000/headers`
3. Run company-quickcheck with `USE_STEALTH_CORE=server company-quickcheck check "Wienerberger AG"`
4. Compare output with direct opendata call — should be identical data, different headers

---

## Disk note (RPi 5)

stealth-core is Rust binary, ~12MB on disk. No new dependencies. company-quickcheck gains `requests` call to localhost — negligible overhead.