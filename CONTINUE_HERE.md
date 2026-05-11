# company-quickcheck — CONTINUE_HERE (May 12, 2026)

## Status: Correlation Feature Implemented ✓

All 48 tests passing. Feature complete and pushed to GitHub.

## What Was Built

Cross-repo transfer from `ether-btc/openclaw-correlation-plugin` into `company-quickcheck` Python skill.

### New Files
- `company_quickcheck/correlation.py` (732 lines) — CorrelationRules, NameSimilarity, AddressNormalizer, CompositeConfidence, CorrelationMatcher, MatchResult, LruRegexCache, passes_confidence_gate()
- `company_quickcheck/correlation_rules.json` (133 lines) — 4 AT-specific rules (all proposal), city aliases (mnk→münchendorf), street abbreviations, field weights

### Modified Files
- `company_quickcheck/api.py` — added `search_with_correlation()`, `build_address_fields()`
- `company_quickcheck/core.py` — multi-result branch uses `CorrelationMatcher`; new params: `correlation_mode`, `correlation_min_confidence`
- `company_quickcheck/cli.py` — `--correlation-mode` (auto/strict/lenient), `--correlation-min-confidence` float

### CLI Usage
```bash
python -m company_quickcheck.cli process \
  ~/Unternehmen_sanitized.xlsx \
  --output ~/companies_checked.xlsx \
  --correlation-mode auto \
  --correlation-min-confidence 0.70
```

## Bugs Fixed During Audit

1. **MatchResult.__repr__ trailing whitespace** — `rule=` slot was present even when `matched_rule_id=None`, producing malformed output like `conf=N/A  company=None`. Fixed: conditional with comma prefix.

2. **passes_confidence_gate rejecting None** — rules in `correlation_rules.json` have no explicit `confidence` field (they rely on `confidence_threshold` per rule). `passes_confidence_gate(None, 0.0)` was returning `False`, silently excluding all 4 rules from active rules. Fixed: `None` passes through (no gate). Rules now load: 4/4 active.

3. **`_normalize_legal_form`: umlaut mismatch** — `ÖBB` stayed as `öbb` not `oebb`, causing token_overlap(ÖBB, OEBB) = 0. Fixed: add umlaut normalization before legal-form stripping.

4. **`_normalize_legal_form`: trailing space after stripping** — 'Hubert Häusle ... & Co. KG.' became 'hubert häusle gesellschaft m.b.h. & co. .' with trailing space before dot. Fixed: add `rstrip('.,()-')` after strip.

5. **`_tokenize`: hyphen not split** — 'alcatel-lucent' stayed as single token, giving token_overlap = 0 vs 'Alcatel Lucent'. Fixed: split on `[\s-]+`.

6. **`_tokenize`: parenthetical not stripped** — 'Algorithmics (AUT) GmbH' tokenized to ['algorithmics (aut)', 'gmbh'], reducing overlap with 'Algorithmics GmbH'. Fixed: strip `\([^)]*\)`.

7. **`normalize_street`: str. double-expansion** — 'Hauptstr.' → 'strasse' then 'str' in 'strasse' → 'strasse' giving 'hauptstrasseasse'. Original `\bstr\.\b` regex never matched (no word boundary between 't' and 's'). Fixed: use negative-lookahead regex `r'str\.(?![a-zA-Z])'` — matches 'str.' only when NOT followed by a letter.

## Remaining Work

### 1. Threshold Calibration (HIGH PRIORITY)
Validate confidence thresholds against `Unternehmen_sanitized.xlsx` (1,711 rows):
- `auto` mode: 0.70 threshold — measure precision/recall
- `lenient` mode: 0.60 threshold — measure improvement in recall with acceptable precision tradeoff
- All rules currently `proposal` state — calibrate before promoting to `testing`

### 2. Rule Lifecycle Promotion
After calibration:
- Rules with precision > 0.90 → promote to `validated`
- Rules used in ≥ 10 matches with low error rate → promote to `promoted`
- Update `correlation_rules.json` with `lifecycle.state` changes

### 3. Full End-to-End Test
```bash
python -m company_quickcheck.cli process \
  /srv/sync/Unternehmen_sanitized.xlsx \
  --output /srv/sync/companies_checked.xlsx \
  --correlation-mode auto \
  --correlation-min-confidence 0.70
```

### 4. FB Backfill Threshold
Currently set to 0.80 — verify against dataset. Is 0.80 too high/low for AT company data?

## Git Log
- `2c15daf` feat: add correlation-enhanced matching for multi-candidate disambiguation
- `3b1a5fe` fix: MatchResult.__repr__ trailing whitespace, pass_confidence_gate accepts None

## Key Design Decisions
- `strict` mode: exact FB/UID only, no fuzzy correlation (legacy behavior preserved)
- `lenient`: relaxed threshold -0.10, postal ±5 proximity
- mtime-cached rules: no parse overhead on repeated calls
- LRU regex cache (500 entries): prevents ReDoS, shared across NameSimilarity + AddressNormalizer
- `passes_confidence_gate()`: filters NaN/zero/negative; `None` passes (no gate — for rules without explicit confidence field)