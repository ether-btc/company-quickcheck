# Correlation-Enhanced Matching — Technical Documentation

**Inspired by:** openclaw-correlation-plugin (ether-btc/openclaw-correlation-plugin)
**Module:** `company_quickcheck/correlation.py`
**Date:** 2026-05-11

---

## Overview

The correlation module adds multi-field confidence aggregation to company-quickcheck's API lookup. When opendata.host returns multiple candidates or fuzzy matches, the `CorrelationMatcher` scores all candidates using weighted name + address components and selects the best match above threshold.

---

## Architecture

```
search_with_correlation(name, fb, uid, address_fields, candidates)
  → CorrelationMatcher.match()
      → Step 1: exact FB match → return (confidence=1.0)
      → Step 2: exact UID match → return (confidence=1.0)
      → Step 3: Correlation scoring (if mode != strict)
          → NameSimilarity.token_overlap() — legal form stripping, token Jaccard
          → AddressNormalizer.street_match() / city_match() / plz_match()
          → CompositeConfidence.compute_with_name() — weighted sum
      → Step 4: Return best above threshold, or no_match
```

---

## Core Classes

### `CorrelationRules` — mtime-cached rule management

```python
from company_quickcheck.correlation import CorrelationRules

rules = CorrelationRules("/path/to/correlation_rules.json")
active = rules.get_active_rules(min_confidence=0.0)  # Filters lifecycle + confidence
```

**Features:**
- mtime-cached — reloaded only when file modified
- Lifecycle filter: `promoted`, `active`, `testing`, `validated`, `proposal` (active); `retired` (excluded)
- Confidence gate: NaN/zero/negative/undefined excluded
- `get_active_rules()` returns only rules that should fire

**Rules file format** (`correlation_rules.json`):
```json
{
  "rules": [{
    "id": "cr-at-001",
    "created": "2026-05-11T00:00:00Z",
    "trigger_context": "at-company-fuzzy-match",
    "trigger_keywords": ["ag", "gmbh", "gesmbh"],
    "field_weights": {"name": 0.40, "street": 0.30, "city": 0.20, "plz": 0.10},
    "city_aliases": {"mnk": "münchendorf"},
    "street_abbreviations": {"str.": "strasse"},
    "confidence_threshold": 0.70,
    "lifecycle": {"state": "proposal"},
    "learned_from": "general-company-lookup-accuracy-improvement",
    "usage_count": 0,
    "notes": "..."
  }]
}
```

---

### `NameSimilarity` — token overlap scoring

```python
from company_quickcheck.correlation import NameSimilarity

ns = NameSimilarity()
score = ns.score("Wienerberger AG", "Wienerberger Aktiengesellschaft")
# Strips legal forms: "wienerberger" vs "wienerberger" → token overlap = 1.0
```

**Features:**
- Austrian legal form stripping (AG, GmbH, GesmbH, Ges.m.b.H., OG, KG, e.U.)
- Token overlap: `shared_tokens / max(tokens_a, tokens_b)`
- ReDoS-safe: simple O(n*m) string includes for alphanumeric; regex only for special chars
- LRU regex cache (max 500 entries, shared with AddressNormalizer)

**Legal forms stripped:**
```python
DEFAULT_LEGAL_FORMS = {
    "ag", "aktiengesellschaft",
    "gmbh", "gesellschaft mit beschränkter haftung",
    "gesmbh", "ges.m.b.h.", "gesmbh",
    "og", "offene gesellschaft",
    "kg", "kommanditgesellschaft",
    "eu", "eingetragener unternehmer",
}
```

---

### `AddressNormalizer` — city aliases, street abbreviations, postal proximity

```python
from company_quickcheck.correlation import AddressNormalizer

an = AddressNormalizer(
    city_aliases={"mnk": "münchendorf"},
    street_abbrevs={"str.": "strasse"},
)

street_score = an.street_match("Klamm 12", "Klammstraße 12")
# → 0.80 (partial: "klamm" in "klammstraße")

city_score = an.city_match("Mnk", "Münchendorf")
# → 1.0 (via alias lookup + normalization)

plz_score = an.plz_match("2344", "2345", lenient=True)
# → 0.9 (within ±5, decay from 1.0)
```

**Street match strategies:**
| Score | Condition |
|---|---|
| 1.0 | Exact match (normalized) |
| 0.80 | Partial (one contains the other, both >= 5 chars) |
| 0.75 | Street name without number matches |
| 0.0 | No match |

**PLZ match:**
| Mode | Condition |
|---|---|
| Strict | Exact match only |
| Lenient | Within ±5, decay: ±0 → 1.0, ±5 → 0.5 |

---

### `CompositeConfidence` — weighted multi-field aggregator

```python
from company_quickcheck.correlation import CompositeConfidence

cc = CompositeConfidence(field_weights={"name": 0.40, "street": 0.30, "city": 0.20, "plz": 0.10})

composite = cc.compute_with_name(
    name_score=0.80,
    street_score=0.75,
    city_score=1.0,
    plz_score=1.0,
)
# = 0.40*0.80 + 0.30*0.75 + 0.20*1.0 + 0.10*1.0 = 0.32 + 0.225 + 0.20 + 0.10 = 0.845
```

Weights are normalized to sum to 1.0 on init.

---

### `CorrelationMatcher` — orchestrator

```python
from company_quickcheck.correlation import build_matcher

matcher = build_matcher(mode="auto", min_confidence=0.70)

result = matcher.match(
    candidates=[...],      # API results
    fb_input="FN123456",   # from spreadsheet
    uid_input="ATU12345678",
    address_fields={"name": "...", "street": "...", "plz": "...", "city": "..."},
    mode="auto",           # override
    min_confidence=0.70,   # override
)
```

**Three matching modes:**

| Mode | Behavior |
|---|---|
| `auto` (default) | FB → UID → correlation scoring → best above threshold |
| `strict` | Exact match only (FB/UID), no correlation scoring |
| `lenient` | Correlation with relaxed threshold (-0.10) and postal code ±5 |

**`MatchResult` attributes:**
```python
result.company                  # matched API dict (or None)
result.composite_confidence    # 0.0–1.0
result.name_confidence         # name similarity component
result.address_confidence      # address composite component
result.matched_rule_id         # which rule fired (or None)
result.fallback_reason         # "exact_fb" | "exact_uid" | "correlation_match" | "no_match"
result.matched_candidates      # all scored candidates (for audit)
```

---

## Caching Strategy

### mtime cache on rules file
```python
cached_rules = None
cached_mtime = 0

def load_rules(path):
    stat = os.stat(path)
    if cached_rules and stat.st_mtime_ns == cached_mtime:
        return cached_rules  # hit
    # ... load and filter ...
    cached_rules = filtered
    cached_mtime = stat.st_mtime_ns
    return cached_rules
```

### LRU regex cache
```python
class LruRegexCache:
    def __init__(self, max_size=500):
        self._cache = OrderedDict()

    def get(self, pattern):
        if pattern in self._cache:
            self._cache.move_to_end(pattern)
            return self._cache[pattern]
        return None

    def put(self, pattern, compiled):
        if len(self._cache) >= self._max_size:
            self._cache.popitem(last=False)  # evict oldest
        self._cache[pattern] = compiled
```

### ReDoS protection
- `MAX_KEYWORD_LEN = 100`
- Simple alphanumeric keywords → O(n*m) string includes (no regex)
- Only compile regex for keywords with special characters
- Keyword too long → warning log, skip

---

## Lifecycle States

```
proposal → testing → validated → promoted → retired
```

| State | Meaning | Fires? |
|---|---|---|
| `proposal` | New idea, lower confidence | Yes |
| `testing` | Live, being evaluated | Yes |
| `validated` | Correctly firing, signal-to-noise acceptable | Yes |
| `promoted` | Rock-solid, high confidence (0.90+) | Yes |
| `retired` | Obsolete, kept for history | **No** |

---

## Confidence Calibration

### Threshold strategy (from correlation plugin PRODUCTION.md)

| Confidence | When to use |
|---|---|
| 0.95–0.99 | Catastrophic cost if wrong — FB backfill |
| 0.85–0.90 | High-value reliable — name + address both strong |
| 0.70–0.80 | Useful but some false-positive risk — lenient mode only |
| < 0.70 | Exploratory — never accept automatically |

### Calibration procedure

1. Deploy all rules as `proposal`, `confidence: 0.70`
2. Run against existing 1,711-row dataset
3. Measure precision/recall at thresholds 0.60 / 0.70 / 0.80 / 0.90
4. Adjust field weights based on which component drives correct matches
5. When stable → move to `validated`
6. After 30+ firings with no issues → consider `promoted`

### Common mistakes

- Setting everything to `0.95` → signal drowning, high-confidence rules dominate everything
- Not being specific enough with keywords → fires on every query
- No `learned_from` on rules → impossible to audit why rule exists

---

## Integration Points

### `api.py` — new function

```python
from company_quickcheck.api import search_with_correlation, build_address_fields

# In batch loop:
addr_fields = build_address_fields(row)
matched_company, match_result = search_with_correlation(
    name=firmenname,
    fb_input=fb_input,
    uid_input=uid_input,
    address_fields=addr_fields,
    candidates=companies,
    mode=correlation_mode,
    min_confidence=correlation_min_confidence,
)
```

### `core.py` — process_batch signature

```python
def process_batch(..., 
                 correlation_mode: str = "auto",
                 correlation_min_confidence: float = 0.70) -> dict:
```

### `cli.py` — new flags

```bash
company-quickcheck batch input.xlsx output.xlsx \
  --correlation-mode auto \
  --correlation-min-confidence 0.70
```

---

## AT-Specific Rules

### Initial rules (all proposal, confidence 0.70)

**`cr-at-001`** — Austrian legal form handling
- Strip AG/GmbH/etc. before comparison, focus on distinctive name
- Field weights: name=0.40, street=0.30, city=0.20, plz=0.10

**`cr-at-002`** — Address normalization
- Str./Strasse expansion, city alias for Mnk→Münchendorf
- Field weights favor street (0.40)

**`cr-at-003`** — Multi-candidate disambiguation
- When API returns multiple results, use name as primary (0.50 weight)
- Higher threshold (0.80) — wrong selection = wrong status

**`cr-at-004`** — Lenient mode fallback
- Reduced threshold (0.60), postal code ±5 proximity
- Use only when strict mode fails

### City aliases
```json
{"mnk": "münchendorf", "wien": "vienna"}
```

### Street abbreviations
```json
{"str.": "strasse", "str": "strasse", "straße": "strasse", "gasse": "gasse", "wg": "weg", "pl.": "platz", "av.": "allee"}
```

---

## Not in Scope

- VIES or web scrape fallback changes
- Checkpointing or batch architecture changes
- Rate limiter changes
- stealth-core integration changes
- Multi-country support (frozen)

---

## Files

| File | Description |
|---|---|
| `company_quickcheck/correlation.py` | New module — all classes |
| `company_quickcheck/correlation_rules.json` | AT-specific rules |
| `references/CORRELATION_PLAN.md` | Implementation specification |
| `references/CORRELATION-ENHANCEMENT.md` | This documentation |
| Updated `company_quickcheck/api.py` | `search_with_correlation()` |
| Updated `company_quickcheck/core.py` | Multi-result disambiguation |
| Updated `company_quickcheck/cli.py` | `--correlation-mode`, `--correlation-min-confidence` |