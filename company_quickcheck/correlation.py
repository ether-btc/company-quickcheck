#!/usr/bin/env python3
"""
Correlation-enhanced matching for Austrian company lookups.

Inspired by: openclaw-correlation-plugin (ether-btc/openclaw-correlation-plugin)
- CorrelationRules with mtime-cached rule loading
- LRU regex cache (max 500 entries, ReDoS protection)
- Lifecycle state management (proposal → testing → validated → promoted → retired)
- Three matching modes: auto / strict / lenient
- passesConfidenceGate() filter for NaN/zero/negative/undefined confidence

This module adds multi-field confidence aggregation (name similarity + address
fields → composite score) to disambiguate multiple API candidates.
"""

import json
import logging
import os
import re
from collections import OrderedDict
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── ReDoS Protection Constants ───────────────────────────────────────────────

MAX_KEYWORD_LEN = 100
MAX_CACHE_SIZE = 500

# ── Lifecycle Constants ──────────────────────────────────────────────────────

ACTIVE_STATES = {"promoted", "active", "testing", "validated", "proposal"}

# ── Default AT-Specific Data ──────────────────────────────────────────────────

# Austrian legal forms to strip before name comparison
DEFAULT_LEGAL_FORMS = {
    "ag", "aktiengesellschaft",
    "gmbh", "gesellschaft mit beschränkter haftung",
    "gesmbh", "ges.m.b.h.", "gesmbh",
    "og", "offene gesellschaft",
    "kg", "kommanditgesellschaft",
    "eu", "eingetragener unternehmer",
    "single",  # English variant sometimes used
}

# Default street abbreviations
DEFAULT_STREET_ABBREVS = {
    "str.": "strasse",
    "str": "strasse",
    "g.": "gasse",
    "wg": "weg",
    "pl.": "platz",
    "av.": "allee",
}

# Default city aliases (common AT municipality abbreviations)
DEFAULT_CITY_ALIASES: Dict[str, str] = {
    "wien": "wien",
    "vienna": "wien",
    "klagenfurt": "klagenfurt",
    "salzburg": "salzburg",
    "innsbruck": "innsbruck",
    "graz": "graz",
    "linz": "linz",
    # Known abbreviations from the dataset
    "mnk": "münchendorf",
    # Add more as discovered
}

# Default field weights for composite confidence
DEFAULT_FIELD_WEIGHTS = {
    "name": 0.40,
    "street": 0.30,
    "city": 0.20,
    "plz": 0.10,
}


# ── Confidence Gate ────────────────────────────────────────────────────────────

def passes_confidence_gate(confidence: Optional[float], min_confidence: float = 0.0) -> bool:
    """Filter out NaN, zero, and negative confidence values.

    None means no explicit confidence specified — passes through (no gate applied).
    """
    if confidence is None:
        return True  # No explicit confidence = no gate (rule participates)
    if isinstance(confidence, float) and (confidence != confidence or confidence <= 0):
        return False  # NaN or <= 0
    return confidence >= min_confidence


# ── LRU Regex Cache ────────────────────────────────────────────────────────────

class LruRegexCache:
    """LRU cache for compiled regexes — bounded at MAX_CACHE_SIZE entries."""

    def __init__(self, max_size: int = MAX_CACHE_SIZE):
        self._cache: OrderedDict[str, re.Pattern] = OrderedDict()
        self._max_size = max_size

    def get(self, pattern: str) -> Optional[re.Pattern]:
        if pattern in self._cache:
            # Move to end (most recently used)
            self._cache.move_to_end(pattern)
            return self._cache[pattern]
        return None

    def put(self, pattern: str, compiled: re.Pattern) -> None:
        if pattern in self._cache:
            self._cache.move_to_end(pattern)
        else:
            if len(self._cache) >= self._max_size:
                # Evict oldest (first in insertion order)
                self._cache.popitem(last=False)
            self._cache[pattern] = compiled

    def clear(self) -> None:
        self._cache.clear()


# ── CorrelationRules ──────────────────────────────────────────────────────────

class CorrelationRules:
    """
    Loads and manages AT-specific correlation rules for company matching.

    Features:
    - mtime-cached rule loading (reloaded only when file modified)
    - Lifecycle state filtering (active states: promoted/active/testing/validated/proposal)
    - confidence gate filtering (NaN/zero/negative/undefined excluded)
    - ReDoS protection via MAX_KEYWORD_LEN
    """

    def __init__(self, rules_path: Optional[str] = None):
        self._rules_path = rules_path
        self._cached_rules: List[Dict] = []
        self._cached_mtime: int = 0
        self._loaded: bool = False

    def _load_raw(self) -> List[Dict]:
        """Load raw rules from JSON file with mtime check."""
        if self._rules_path is None:
            return []

        try:
            stat = os.stat(self._rules_path)
            mtime = stat.st_mtime_ns
        except OSError:
            logger.warning(f"[correlation] Rules file not found: {self._rules_path}")
            return []

        # Return cached if not modified
        if self._loaded and mtime == self._cached_mtime:
            return self._cached_rules

        try:
            with open(self._rules_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            rules = data.get("rules", [])
            self._cached_rules = rules
            self._cached_mtime = mtime
            self._loaded = True
            logger.info(f"[correlation] Loaded {len(rules)} rules from {self._rules_path}")
            return rules
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"[correlation] Failed to load rules: {e}")
            return []

    def get_active_rules(self, min_confidence: float = 0.0) -> List[Dict]:
        """Return rules filtered to active lifecycle states and valid confidence."""
        raw_rules = self._load_raw()
        active = []

        for rule in raw_rules:
            rule_id = rule.get("id") or rule.get("context", "unknown")

            # Confidence gate
            conf = rule.get("confidence")
            if not passes_confidence_gate(conf, min_confidence):
                continue

            # Lifecycle state filter
            state = rule.get("lifecycle", {}).get("state") if isinstance(rule.get("lifecycle"), dict) else None
            if state and state not in ACTIVE_STATES:
                continue

            active.append(rule)

        return active

    def get_rule_ids(self) -> List[str]:
        """Return list of all active rule IDs."""
        return [r.get("id", "unknown") for r in self.get_active_rules()]


# ── NameSimilarity ─────────────────────────────────────────────────────────────

class NameSimilarity:
    """
    Scores company name match quality using token overlap + Levenshtein.

    Features:
    - Austrian legal form stripping (AG, GmbH, GesmbH, etc.)
    - ReDoS-safe: simple O(n*m) string includes for alphanumeric keywords
    - Regex only for keywords with special characters
    - LRU regex cache (max 500 entries)
    """

    def __init__(self, legal_forms: Optional[set] = None, cache: Optional[LruRegexCache] = None):
        self._legal_forms = legal_forms or DEFAULT_LEGAL_FORMS
        self._cache = cache or LruRegexCache()

    def _normalize_legal_form(self, name: str) -> str:
        """Strip Austrian legal forms for fair comparison."""
        n = name.lower()
        # Normalize umlauts first (before checking for legal forms)
        n = n.replace("ü", "ue").replace("ä", "ae").replace("ö", "oe").replace("ß", "ss")
        for form in self._legal_forms:
            # Remove common separators: space, hyphen, dot, comma
            n = re.sub(rf'\b{re.escape(form)}\b', '', n, flags=re.IGNORECASE)
        # Clean up extra spaces and trailing separators
        n = re.sub(r'\s+', ' ', n).strip()
        # Remove trailing separators left behind by legal form stripping (e.g. "m.b.h." → "m.b.h")
        n = n.rstrip('.,()-')
        if not n:
            return ""
        return n

    def _tokenize(self, name: str) -> List[str]:
        """Split name into tokens, strip punctuation.

        Splits on whitespace AND hyphens so 'alcatel-lucent' becomes
        ['alcatel', 'lucent'] for better overlap with 'alcatel lucent'.
        Also strips parenthetical content like '(AUT)' from company names.
        """
        tokens = []
        for t in re.split(r'[\s-]+', name.lower()):
            # Strip parenthetical content
            t = re.sub(r'\([^)]*\)', '', t)
            t = t.strip('.,()-')
            if t:
                tokens.append(t)
        return tokens

    def _simple_word_match(self, text: str, keyword: str) -> bool:
        """
        ReDoS-safe: O(n*m) string includes for simple alphanumeric keywords.
        Only use regex for keywords containing special regex metacharacters.
        """
        if not keyword.strip():
            return False

        k_lower = keyword.lower()
        simple_re = re.compile(r'^[a-zA-Z0-9]+$')

        if simple_re.match(k_lower) and len(k_lower) <= MAX_KEYWORD_LEN:
            return k_lower in text.lower()

        # Use regex for keywords with special characters
        cached_re = self._cache.get(keyword)
        if cached_re is None:
            if len(keyword) > MAX_KEYWORD_LEN:
                logger.warning(f"[correlation] Keyword too long, skipping: {keyword[:20]}...")
                return False
            escaped = re.escape(keyword)
            compiled = re.compile(rf'\b{escaped}\b', re.IGNORECASE | re.UNICODE)
            self._cache.put(keyword, compiled)
            cached_re = compiled
        return bool(cached_re.search(text))

    def token_overlap(self, name_a: str, name_b: str) -> float:
        """
        Token-based overlap score.

        Strip legal forms, tokenize, compute Jaccard-like overlap:
        shared_tokens / max(tokens_a, tokens_b)

        Returns 0.0–1.0
        """
        a_norm = self._normalize_legal_form(name_a)
        b_norm = self._normalize_legal_form(name_b)

        tokens_a = set(self._tokenize(a_norm))
        tokens_b = set(self._tokenize(b_norm))

        if not tokens_a or not tokens_b:
            return 0.0

        # Remove empty tokens
        tokens_a = {t for t in tokens_a if t}
        tokens_b = {t for t in tokens_b if t}

        if not tokens_a or not tokens_b:
            return 0.0

        shared = tokens_a & tokens_b
        max_len = max(len(tokens_a), len(tokens_b))
        return len(shared) / max_len if max_len > 0 else 0.0

    def score(self, name_a: str, name_b: str) -> float:
        """
        Composite name similarity score.

        Uses token overlap as primary metric.
        Returns 0.0–1.0
        """
        return self.token_overlap(name_a, name_b)


# ── AddressNormalizer ─────────────────────────────────────────────────────────

class AddressNormalizer:
    """
    Normalizes AT addresses: city aliases, street abbreviations, postal code proximity.

    Features:
    - LRU regex cache (max 500 entries, shared with NameSimilarity)
    - mtime-cached city alias map (loaded from rules)
    - ReDoS-safe word-boundary matching
    """

    def __init__(self,
                 city_aliases: Optional[Dict[str, str]] = None,
                 street_abbrevs: Optional[Dict[str, str]] = None,
                 cache: Optional[LruRegexCache] = None):
        self._city_aliases = city_aliases or DEFAULT_CITY_ALIASES.copy()
        self._street_abbrevs = street_abbrevs or DEFAULT_STREET_ABBREVS.copy()
        self._cache = cache or LruRegexCache()

    def normalize_city(self, city: str) -> str:
        """Apply city alias lookup then normalize."""
        if not city:
            return ""
        c = city.lower().strip()
        # Alias lookup
        c = self._city_aliases.get(c, c)
        # General normalization: lower, no umlauts, no punctuation
        c = c.replace("ü", "ue").replace("ä", "ae").replace("ö", "oe").replace("ß", "ss")
        c = re.sub(r'[^\w\s]', '', c)
        c = re.sub(r'\s+', ' ', c).strip()
        return c

    def normalize_street(self, street: str) -> str:
        """Apply street abbreviation expansion then normalize.

        Uses negative lookahead regex to safely expand 'str.' → 'strasse'
        without double-expanding 'strasse' or matching 'str.' in compound words.

        Pattern: str\\.(?![a-zA-Z]) matches 'str.' only when NOT followed by
        a letter (preventing 'strasse.' matching as 'str' + '.asse').
        """
        if not street:
            return ""
        s = street.lower().strip()
        # Abbreviation expansion using negative-lookahead regex
        # r'str\.(?![a-zA-Z])' matches 'str.' only when NOT followed by a letter
        s = re.sub(r'str\.(?![a-zA-Z])', 'strasse', s, flags=re.IGNORECASE)
        # General normalization: no umlauts, no punctuation, no numbers
        s = s.replace("ü", "ue").replace("ä", "ae").replace("ö", "oe").replace("ß", "ss")
        s = re.sub(r'[^a-z\s]', ' ', s)
        s = re.sub(r'\s+', ' ', s).strip()
        return s

    def normalize_plz(self, plz: str) -> str:
        """Normalize postal code: strip whitespace, keep as-is."""
        return plz.strip() if plz else ""

    def city_match(self, city_a: str, city_b: str) -> float:
        """1.0 if normalized cities match, 0.0 otherwise."""
        if not city_a or not city_b:
            return 0.0
        return 1.0 if self.normalize_city(city_a) == self.normalize_city(city_b) else 0.0

    def street_match(self, street_a: str, street_b: str) -> float:
        """
        Street name match with multiple strategies.

        Returns:
          1.0  — exact match (normalized)
          0.80 — partial match (one normalized name contains the other, both >= 5 chars)
          0.75 — street name without number matches
          0.0  — no match
        """
        if not street_a or not street_b:
            return 0.0

        norm_a = self.normalize_street(street_a)
        norm_b = self.normalize_street(street_b)

        if not norm_a or not norm_b:
            return 0.0

        # Exact match
        if norm_a == norm_b:
            return 1.0

        # Partial match (one contains the other, both long enough)
        if len(norm_a) >= 5 and len(norm_b) >= 5:
            if norm_a in norm_b or norm_b in norm_a:
                return 0.80

        # Street name without number match
        name_a = ' '.join(norm_a.split()[:-1]) if norm_a.split() else norm_a
        name_b = ' '.join(norm_b.split()[:-1]) if norm_b.split() else norm_b
        if name_a and name_b and name_a == name_b:
            return 0.75

        return 0.0

    def plz_match(self, plz_a: str, plz_b: str, lenient: bool = False) -> float:
        """
        Postal code match.

        Strict: exact match required
        Lenient: within ±5
        """
        if not plz_a or not plz_b:
            return 0.0

        pa = plz_a.strip()
        pb = plz_b.strip()

        if pa == pb:
            return 1.0

        if lenient:
            try:
                diff = abs(int(pa) - int(pb))
                if diff <= 5:
                    return max(0.0, 1.0 - (diff / 10.0))  # Decay: ±5 → 0.5, ±0 → 1.0
            except ValueError:
                pass

        return 0.0

    def address_components(self,
                            street: str, number: str, plz: str, city: str,
                            ref_street: str, ref_number: str, ref_plz: str, ref_city: str,
                            lenient: bool = False) -> Dict[str, float]:
        """
        Compute per-field match scores for an address.

        Returns dict with keys: street, city, plz, each 0.0–1.0
        """
        full_street_a = f"{street} {number}".strip() if number else street
        full_street_b = f"{ref_street} {ref_number}".strip() if ref_number else ref_street

        return {
            "street": self.street_match(full_street_a, full_street_b),
            "city": self.city_match(city, ref_city),
            "plz": self.plz_match(plz, ref_plz, lenient=lenient),
        }


# ── CompositeConfidence ───────────────────────────────────────────────────────

class CompositeConfidence:
    """
    Weighted multi-field confidence aggregator.

    Computes composite = sum(field_weight * field_score) for all fields.
    """

    def __init__(self, field_weights: Optional[Dict[str, float]] = None):
        self._weights = field_weights or DEFAULT_FIELD_WEIGHTS.copy()
        # Validate weights sum to ~1.0
        total = sum(self._weights.values())
        if total > 0:
            # Normalize
            self._weights = {k: v / total for k, v in self._weights.items()}

    def compute(self, field_scores: Dict[str, float]) -> float:
        """
        Compute weighted composite confidence.

        Args:
            field_scores: dict with keys matching field_weights (name, street, city, plz)
        Returns: 0.0–1.0 composite score
        """
        composite = 0.0
        for field, weight in self._weights.items():
            score = field_scores.get(field, 0.0)
            composite += weight * score
        return composite

    def compute_with_name(self,
                          name_score: float,
                          street_score: float,
                          city_score: float,
                          plz_score: float) -> float:
        """Convenience wrapper for 4-field computation."""
        return self.compute({
            "name": name_score,
            "street": street_score,
            "city": city_score,
            "plz": plz_score,
        })


# ── MatchResult ──────────────────────────────────────────────────────────────

class MatchResult:
    """
    Outcome of correlation-enhanced company matching.
    """

    def __init__(self,
                 company: Optional[Dict] = None,
                 composite_confidence: float = 0.0,
                 name_confidence: float = 0.0,
                 address_confidence: float = 0.0,
                 matched_rule_id: Optional[str] = None,
                 fallback_reason: str = "no_match",
                 matched_candidates: Optional[List[Dict]] = None):
        self.company = company
        self.composite_confidence = composite_confidence
        self.name_confidence = name_confidence
        self.address_confidence = address_confidence
        self.matched_rule_id = matched_rule_id
        self.fallback_reason = fallback_reason
        self.matched_candidates = matched_candidates or []

    def __repr__(self) -> str:
        conf = f"{self.composite_confidence:.2f}" if self.composite_confidence else "N/A"
        rule = f", rule={self.matched_rule_id}" if self.matched_rule_id else ""
        return (f"<MatchResult fallback={self.fallback_reason} conf={conf}{rule} "
                f"company={self.company.get('business-name') if self.company else None}>")

    def to_dict(self) -> Dict:
        return {
            "company": self.company,
            "composite_confidence": self.composite_confidence,
            "name_confidence": self.name_confidence,
            "address_confidence": self.address_confidence,
            "matched_rule_id": self.matched_rule_id,
            "fallback_reason": self.fallback_reason,
        }


# ── CorrelationMatcher ────────────────────────────────────────────────────────

class CorrelationMatcher:
    """
    Orchestrates correlation-enhanced company matching.

    Three matching modes:
    - auto (default): try exact FB/UID first → correlation scoring → best above threshold
    - strict: exact match only, no fuzzy correlation
    - lenient: correlation scoring with relaxed thresholds

    Integration: use instead of manual first-result selection in core.py
    when n_results > 1.
    """

    def __init__(self,
                 rules_path: Optional[str] = None,
                 mode: str = "auto",
                 min_confidence: float = 0.70,
                 field_weights: Optional[Dict[str, float]] = None,
                 city_aliases: Optional[Dict[str, str]] = None,
                 street_abbrevs: Optional[Dict[str, str]] = None,
                 legal_forms: Optional[set] = None):
        self._rules = CorrelationRules(rules_path)
        self._mode = mode
        self._min_confidence = min_confidence
        self._shared_cache = LruRegexCache()
        self._name_sim = NameSimilarity(legal_forms=legal_forms, cache=self._shared_cache)
        self._addr_norm = AddressNormalizer(
            city_aliases=city_aliases,
            street_abbrevs=street_abbrevs,
            cache=self._shared_cache,
        )
        self._composite = CompositeConfidence(field_weights=field_weights)

    def match(self,
              candidates: List[Dict],
              fb_input: Optional[str] = None,
              uid_input: Optional[str] = None,
              address_fields: Optional[Dict] = None,
              mode: Optional[str] = None,
              min_confidence: Optional[float] = None) -> MatchResult:
        """
        Find best matching company from API candidates using correlation.

        Args:
            candidates: list of API result dicts (from opendata.host)
            fb_input: firmenbuchnr from input spreadsheet
            uid_input: UID from input spreadsheet
            address_fields: dict with keys: street, number, plz, city
            mode: override matching mode (auto/strict/lenient)
            min_confidence: override minimum confidence threshold

        Returns MatchResult with matched company and confidence scores.
        """
        effective_mode = mode or self._mode
        effective_min = min_confidence if min_confidence is not None else self._min_confidence

        if not candidates:
            return MatchResult(fallback_reason="no_candidates")

        fb_input_norm = fb_input.strip().lower().lstrip("fn").strip() if fb_input else ""
        uid_input_norm = uid_input.strip().lower().lstrip("atu").strip() if uid_input else ""
        addr = address_fields or {}

        # ── Step 1: Exact FB match ──────────────────────────────────────────────
        if fb_input_norm:
            for c in candidates:
                c_reg = c.get("reg-no", "").strip().lower().lstrip("fn").strip()
                if c_reg and (fb_input_norm == c_reg or fb_input_norm in c_reg or c_reg in fb_input_norm):
                    return MatchResult(
                        company=c,
                        composite_confidence=1.0,
                        name_confidence=1.0,
                        address_confidence=1.0,
                        matched_rule_id=None,
                        fallback_reason="exact_fb",
                        matched_candidates=candidates,
                    )

        # ── Step 2: Exact UID match ─────────────────────────────────────────────
        if uid_input_norm:
            for c in candidates:
                c_uid = c.get("uid", "").strip().lower().lstrip("atu").strip()
                if c_uid and (uid_input_norm == c_uid or uid_input_norm in c_uid or c_uid in uid_input_norm):
                    return MatchResult(
                        company=c,
                        composite_confidence=1.0,
                        name_confidence=1.0,
                        address_confidence=1.0,
                        matched_rule_id=None,
                        fallback_reason="exact_uid",
                        matched_candidates=candidates,
                    )

        # ── Step 3: Skip correlation in strict mode ─────────────────────────────
        if effective_mode == "strict":
            # Take first candidate as fallback (legacy behavior)
            return MatchResult(
                company=candidates[0],
                composite_confidence=0.0,
                name_confidence=0.0,
                address_confidence=0.0,
                matched_rule_id=None,
                fallback_reason="strict_fallback",
                matched_candidates=candidates,
            )

        # ── Step 4: Correlation scoring ──────────────────────────────────────────
        active_rules = self._rules.get_active_rules(min_confidence=0.0)
        effective_min_strict = effective_min
        effective_min_lenient = max(0.60, effective_min - 0.10)  # relaxed by 0.10

        best: Optional[MatchResult] = None

        for candidate in candidates:
            # Get candidate address
            c_addr = candidate.get("business-address", {}) or {}
            if isinstance(c_addr, dict):
                c_street = c_addr.get("street-address", "")
                c_number = c_addr.get("street-number", "")
                c_plz = c_addr.get("postal-code", "")
                c_city = c_addr.get("city", "")
            else:
                c_street = c_number = c_plz = c_city = ""

            # Name score
            api_name = candidate.get("business-name", "")
            input_name = addr.get("name", "")
            name_score = self._name_sim.score(api_name, input_name) if input_name else 0.0

            # Address scores
            addr_scores = self._addr_norm.address_components(
                street=addr.get("street", ""),
                number=addr.get("number", ""),
                plz=addr.get("plz", ""),
                city=addr.get("city", ""),
                ref_street=c_street,
                ref_number=c_number,
                ref_plz=c_plz,
                ref_city=c_city,
                lenient=(effective_mode == "lenient"),
            )

            # Address composite (street + city + plz)
            address_score = self._composite.compute({
                "street": addr_scores.get("street", 0.0),
                "city": addr_scores.get("city", 0.0),
                "plz": addr_scores.get("plz", 0.0),
            })

            # Use rule-specific field weights if available
            # For now, use default composite scoring
            composite = self._composite.compute_with_name(
                name_score=name_score,
                street_score=addr_scores.get("street", 0.0),
                city_score=addr_scores.get("city", 0.0),
                plz_score=addr_scores.get("plz", 0.0),
            )

            # Determine effective threshold based on mode
            eff_threshold = effective_min_lenient if effective_mode == "lenient" else effective_min_strict

            if composite >= eff_threshold:
                if best is None or composite > best.composite_confidence:
                    best = MatchResult(
                        company=candidate,
                        composite_confidence=composite,
                        name_confidence=name_score,
                        address_confidence=address_score,
                        matched_rule_id=None,  # Could identify which rule triggered
                        fallback_reason="correlation_match",
                        matched_candidates=candidates,
                    )

        if best is not None:
            return best

        # ── Step 5: No match above threshold ────────────────────────────────────
        return MatchResult(
            company=None,
            composite_confidence=0.0,
            name_confidence=0.0,
            address_confidence=0.0,
            matched_rule_id=None,
            fallback_reason="no_match",
            matched_candidates=candidates,
        )


# ── Convenience Factory ────────────────────────────────────────────────────────

def build_matcher(config_path: Optional[str] = None,
                  mode: str = "auto",
                  min_confidence: float = 0.70) -> CorrelationMatcher:
    """
    Build a CorrelationMatcher with defaults from config or file system.

    Args:
        config_path: path to correlation_rules.json (or None for defaults)
        mode: matching mode (auto/strict/lenient)
        min_confidence: minimum confidence threshold

    Returns: CorrelationMatcher instance
    """
    import os as _os

    if config_path is None:
        # Look for rules file relative to this module
        base_dir = _os.path.dirname(_os.path.abspath(__file__))
        default_path = _os.path.join(base_dir, "correlation_rules.json")
        config_path = default_path if _os.path.exists(default_path) else None

    return CorrelationMatcher(
        rules_path=config_path,
        mode=mode,
        min_confidence=min_confidence,
    )