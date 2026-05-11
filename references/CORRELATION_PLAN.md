# Correlation-Enhanced Matching — Component Implementation Plan

**Cross-repo inspiration:** `openclaw-correlation-plugin` (ether-btc/openclaw-correlation-plugin)
**Target:** `company-quickcheck` (ether-btc/company-quickcheck)
**Date:** 2026-05-11
**Status:** Draft → Implementation

---

## 1. Problem Statement

Current `company-quickcheck` handles exact-match lookups well. When the opendata.host API returns fuzzy/noisy matches (name variants, partial addresses), the system lacks a structured confidence aggregation layer.

**Existing gap in `api.py`:**
- `address_confidence()` is single-result only — no multi-candidate disambiguation
- No name similarity scoring (token overlap, Levenshtein)
- No city alias / street abbreviation normalization beyond `normalize_address()`
- No correlation rule lifecycle management
- No mtime-cached rules reload
- No LRU-cached compiled regexes (regex compiled on every call)

**What's needed:** A multi-field confidence aggregation layer that can disambiguate multiple API candidates using weighted name + address match components.

---

## 2. Transfer Map — openclaw-correlation-plugin → company-quickcheck

| Correlation plugin concept | Applied to company-quickcheck |
|---|---|
| `trigger_keywords` | Fuzzy company name tokens that activate correlation |
| `must_also_fetch` | When name match fires, corroborate with address fields |
| `confidence` (0.0–1.0) | Composite match confidence threshold |
| `lifecycle.state` | Rule quality states: `proposal` → `validated` → `promoted` |
| `relationship_type` | `name_match`, `address_corroborates`, `fb_backfill` |
| `learned_from` | Empirical incident that validated the rule |
| `mtime cache` | Correlation rules file reloaded only when modified |
| `LRU regex cache` | Bounded 500-entry cache for compiled address normalization |
| `passesConfidenceGate()` | Filter rules with NaN/zero/negative/undefined confidence |
| Three matching modes | `strict` (FB/UID exact), `lenient` (fuzzy address), `auto` (both) |
| Word-boundary matching | Simple string includes for alphanumeric; regex for special chars |
| ReDoS protection | MAX_KEYWORD_LEN = 100, O(n*m) string match for simple keywords |
| `correlation_mode` | Per-request override: auto / strict / lenient |

---

## 3. New Module: `company_quickcheck/correlation.py`

### 3.1 Core Classes

```python
# ── Rule Schema ────────────────────────────────────────────────

class CorrelationRule:
    """AT-specific correlation rule for company matching."""
    id: str                           # Unique identifier (e.g., "cr-at-001")
    created: str                      # ISO-8601 timestamp
    trigger_context: str             # Semantic domain (e.g., "at-company-fuzzy-match")
    trigger_keywords: list[str]       # Name tokens that activate this rule
    field_weights: dict               # {"name": 0.4, "street": 0.3, "city": 0.2, "plz": 0.1}
    city_aliases: dict                # {"Mnk": "Münchendorf", "Wien": "Vienna"}
    street_abbreviations: dict        # {"Str.": "Strasse", "gasse": "gasse"}
    confidence_threshold: float       # Minimum composite confidence to accept match
    lifecycle: dict                   # {"state": "proposal"|"testing"|"validated"|"promoted"|"retired"}
    learned_from: str                 # Incident/pattern that validated this rule
    usage_count: int                  # Auto-incremented, diagnostics only
    notes: str                        # Human-readable explanation

# ── Result Type ────────────────────────────────────────────────

class MatchResult:
    """Outcome of correlation-enhanced matching."""
    company: dict                      # Matched API result (or None)
    composite_confidence: float       # 0.0–1.0
    name_confidence: float            # Name similarity component score
    address_confidence: float         # Address similarity component score
    matched_rule_id: str              # Which rule fired (or None)
    fallback_reason: str             # "exact_fb" | "exact_uid" | "correlation_match" | "single_result" | "no_match"
    matched_candidates: list[dict]    # All scored candidates (for audit)
```

### 3.2 Sub-Components

**`CorrelationRules`** — loads, caches, manages AT-specific rules
- mtime-cached (reload only when file modified)
- Filters to active lifecycle states: `promoted`, `validated`, `testing`, `proposal`
- Filters out: NaN/zero/negative/undefined confidence, `retired` state

**`NameSimilarity`** — scores company name match quality
- Token overlap: shared tokens / max(tokens_a, tokens_b)
- Handles Austrian legal forms: AG, GmbH, GesmbH, Ges.m.b.H., GesmbH, OG, KG, e.U.
- Strips legal forms before comparing (focus on distinctive name part)
- ReDoS-safe: simple O(n*m) string includes for alphanumeric; regex only for special chars

**`AddressNormalizer`** — city aliases, street abbreviations, postal proximity
- City alias lookup from rules (e.g., "Mnk" → "Münchendorf")
- Street abbreviation expansion (Str. → Strasse, etc.)
- Postal code proximity: exact required for plz; within ±5 for lenient mode

**`CompositeConfidence`** — weighted multi-field aggregation
```
composite = (name_weight * name_score) + (street_weight * street_score) +
            (city_weight * city_score) + (plz_weight * plz_score)
```
Where name_score = NameSimilarity, street/city/plz from AddressNormalizer.

**`CorrelationMatcher`** — orchestrates all components, implements modes
- `auto` (default): try exact FB/UID first → correlation scoring → best above threshold
- `strict`: exact match only, no fuzzy correlation
- `lenient`: correlation scoring with relaxed thresholds (±5 postal code, partial street)

### 3.3 Rules File

**Path:** `company_quickcheck/correlation_rules.json`

**Lifecycle states:** `proposal` → `testing` → `validated` → `promoted` → `retired`

Active states (rules that fire): `promoted`, `active`, `testing`, `validated`, `proposal`.
`retired` rules are excluded.

**Initial rules (all proposal, confidence 0.70):**
1. Austrian legal form stripping (AG, GmbH, GesmbH variants)
2. City alias mapping (common AT municipality abbreviations)
3. Street abbreviation expansion (Str., Gasse, Weg, etc.)
4. Field weights calibrated for AT address structure

---

## 4. Integration Points

### 4.1 `api.py` — new function

```python
def search_with_correlation(name: str, fb_input: str, uid_input: str,
                             address_fields: dict,
                             rules: CorrelationRules,
                             mode: str = "auto",
                             min_confidence: float = 0.70,
                             candidates: list[dict] = None) -> MatchResult:
    """
    Enhanced search with correlation-based disambiguation.

    Called when:
    - Primary name search returns 0 results (try fuzzy)
    - Primary name search returns multiple candidates (disambiguate)

    Returns MatchResult with composite confidence and matched company.
    """
```

**Flow:**
1. Try exact FB number match across candidates → `fallback_reason: "exact_fb"`
2. Try exact UID match across candidates → `fallback_reason: "exact_uid"`
3. If mode != "strict": run correlation scoring on all candidates
4. Take highest composite confidence above threshold → `fallback_reason: "correlation_match"`
5. If mode == "lenient": relax threshold by 0.10, allow ±5 postal code
6. If no match above threshold: return no_match

### 4.2 `core.py` — update process_batch

In the loop where `n_results > 1`:
- Replace manual first-result selection with `search_with_correlation()`
- Log which rule fired and confidence score
- If correlation_match and confidence >= 0.80: backfill FB number

### 4.3 `cli.py` — new flags

```python
--correlation-mode [auto|strict|lenient]  # default: auto
--correlation-min-confidence FLOAT          # default: 0.70
```

---

## 5. Caching Strategy (from correlation plugin)

**mtime cache on rules file:**
```python
cached_rules: list[CorrelationRule] | None = None
cached_mtime: int = 0

def load_rules(rules_path):
    stat = os.stat(rules_path)
    if cached_rules and stat.st_mtime_ns == cached_mtime:
        return cached_rules  # cache hit
    # ... parse and filter rules ...
    cached_rules = filtered
    cached_mtime = stat.st_mtime_ns
    return cached_rules
```

**LRU regex cache for AddressNormalizer:**
- Max 500 entries (same bound as correlation plugin)
- Evict oldest (first in insertion order) when full
- Only compile regex for keywords with special characters
- Simple alphanumeric keywords use O(n*m) string includes

**ReDoS protection:**
- MAX_KEYWORD_LEN = 100
- Reject keywords longer than limit with warning log
- Simple alphanumeric keywords → string includes (no regex)

---

## 6. Confidence Calibration

**Threshold strategy (from correlation plugin PRODUCTION.md):**

| Confidence | When to use |
|---|---|
| 0.95–0.99 | Catastrophic cost if wrong — FB backfill |
| 0.85–0.90 | High-value reliable — name + address both strong |
| 0.70–0.80 | Useful but some false-positive risk — lenient mode only |
| < 0.70 | Exploratory — never accept automatically |

**Calibration procedure:**
1. Deploy all rules as `proposal`, `confidence: 0.70`
2. Run against existing 1,711-row dataset
3. Measure precision/recall at thresholds 0.60 / 0.70 / 0.80 / 0.90
4. Adjust field weights based on which component drives correct matches
5. When stable → move to `validated`
6. After 30+ firings with no issues → consider `promoted`

---

## 7. Phase 1 Deliverables

| # | File | Description |
|---|---|---|
| 1 | `company_quickcheck/correlation.py` | New module — all classes |
| 2 | `company_quickcheck/correlation_rules.json` | AT-specific rules with initial city aliases, field weights |
| 3 | `company_quickcheck/references/CORRELATION-ENHANCEMENT.md` | Full documentation |
| 4 | Updated `company_quickcheck/api.py` | `search_with_correlation()` integrated |
| 5 | Updated `company_quickcheck/core.py` | Multi-result disambiguation via correlation |
| 6 | Updated `company_quickcheck/cli.py` | `--correlation-mode` and `--correlation-min-confidence` flags |

---

## 8. Not in Scope (Phase 1)

- Changes to VIES or web scrape fallback (already working)
- Changes to checkpointing or batch architecture
- Changes to rate limiter
- Changes to stealth-core integration
- Multi-country support (frozen)

---

## 9. Risks & Mitigations

| Risk | Mitigation |
|---|---|
| Wrong company match = wrong status | Conservative threshold (0.80+ to accept), explicit logging |
| ReDoS from pathological regex in rules | MAX_KEYWORD_LEN=100, simple string match for alphanumeric |
| Rule file corruption | Try/except on load, fallback to empty rules (no correlation) |
| Over-correlation (noise) | `learned_from` required per rule, lifecycle gates |
| Confidence threshold too high | `lenient` mode drops threshold by 0.10 |

---

## 10. Design Principles (from correlation plugin LESSONS)

1. **Zero external runtime dependencies** — correlation.py imports only stdlib + existing company_quickcheck deps
2. **Conservative by default** — lenient mode is opt-in, thresholds default to high confidence
3. **Transparent** — every correlation match logs rule_id, confidence components, fallback_reason
4. **No silent failures** — if rules file can't be loaded, system continues without correlation (not crash)
5. **Empirical calibration** — all thresholds and weights validated against real data, not guessed