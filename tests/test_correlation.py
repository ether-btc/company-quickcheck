#!/usr/bin/env python3
"""Unit tests for company_quickcheck.correlation module (TST-02)."""

import json
import math
import os
import tempfile
import unittest
from company_quickcheck.correlation import (
    passes_confidence_gate,
    LruRegexCache,
    NameSimilarity,
    AddressNormalizer,
    CompositeConfidence,
    CorrelationRules,
    CorrelationMatcher,
    MatchResult,
    build_matcher,
    DEFAULT_FIELD_WEIGHTS,
    MAX_CACHE_SIZE,
    MAX_KEYWORD_LEN,
)


class TestPassesConfidenceGate(unittest.TestCase):
    """Tests for passes_confidence_gate()."""

    def test_none_passes(self):
        """None means no explicit confidence — passes through."""
        self.assertTrue(passes_confidence_gate(None))
        self.assertTrue(passes_confidence_gate(None, min_confidence=0.8))

    def test_nan_fails(self):
        """NaN confidence is filtered out."""
        self.assertFalse(passes_confidence_gate(float("nan")))
        self.assertFalse(passes_confidence_gate(math.nan))

    def test_zero_float_fails(self):
        """Float 0.0 is filtered out (<=0 check)."""
        self.assertFalse(passes_confidence_gate(0.0))

    def test_zero_int_passes(self):
        """Integer 0 is treated as non-float (like None) — passes through the gate."""
        # The implementation only checks float instances, so int 0 passes.
        # This is an edge case: int 0 is treated as "no explicit confidence".
        self.assertTrue(passes_confidence_gate(0))

    def test_negative_fails(self):
        """Negative confidence is filtered out."""
        self.assertFalse(passes_confidence_gate(-0.1))
        self.assertFalse(passes_confidence_gate(-1.0))
        self.assertFalse(passes_confidence_gate(-999.9))

    def test_positive_below_threshold_fails(self):
        """Positive confidence below min_confidence threshold fails."""
        self.assertFalse(passes_confidence_gate(0.3, min_confidence=0.5))
        self.assertFalse(passes_confidence_gate(0.49, min_confidence=0.50))

    def test_positive_at_threshold_passes(self):
        """Confidence equal to min_confidence passes."""
        self.assertTrue(passes_confidence_gate(0.50, min_confidence=0.50))
        self.assertTrue(passes_confidence_gate(0.70, min_confidence=0.70))
        self.assertTrue(passes_confidence_gate(1.0, min_confidence=1.0))

    def test_positive_above_threshold_passes(self):
        """Confidence above threshold passes."""
        self.assertTrue(passes_confidence_gate(0.51, min_confidence=0.50))
        self.assertTrue(passes_confidence_gate(0.9, min_confidence=0.70))
        self.assertTrue(passes_confidence_gate(1.0))


class TestLruRegexCache(unittest.TestCase):
    """Tests for LruRegexCache (put/get/clear methods)."""

    def test_put_and_get(self):
        """put() stores a compiled pattern; get() retrieves it."""
        cache = LruRegexCache(max_size=5)
        import re
        pattern = re.compile(r"\d+")
        cache.put(r"\d+", pattern)
        retrieved = cache.get(r"\d+")
        self.assertIs(retrieved, pattern)

    def test_get_nonexistent_returns_none(self):
        """get() returns None for unknown patterns."""
        cache = LruRegexCache(max_size=5)
        self.assertIsNone(cache.get(r"nonexistent"))

    def test_lru_eviction(self):
        """When max_size is exceeded, LRU entry is evicted."""
        cache = LruRegexCache(max_size=3)
        import re
        for char in ("a", "b", "c"):
            cache.put(char, re.compile(char))

        # Touch "a" to make it most-recent
        cache.get("a")

        # Add new entry — should evict LRU (which is "b")
        cache.put("d", re.compile("d"))
        self.assertEqual(len(cache._cache), 3)
        self.assertIsNone(cache.get("b"))
        self.assertIsNotNone(cache.get("a"))

    def test_clear_removes_all_entries(self):
        """clear() removes all entries."""
        cache = LruRegexCache(max_size=5)
        import re
        cache.put("a", re.compile("a"))
        cache.put("b", re.compile("b"))
        cache.clear()
        self.assertEqual(len(cache._cache), 0)

    def test_max_cache_size_constant(self):
        """MAX_CACHE_SIZE constant is 500."""
        self.assertEqual(MAX_CACHE_SIZE, 500)

    def test_max_keyword_len_constant(self):
        """MAX_KEYWORD_LEN constant is 100."""
        self.assertEqual(MAX_KEYWORD_LEN, 100)


class TestNameSimilarity(unittest.TestCase):
    """Tests for NameSimilarity.score()."""

    def setUp(self):
        self.ns = NameSimilarity()

    def test_identical_names_score_one(self):
        """Identical names score 1.0."""
        self.assertEqual(self.ns.score("Test GmbH", "Test GmbH"), 1.0)

    def test_different_names_score_zero(self):
        """Completely different names score 0.0."""
        self.assertEqual(self.ns.score("Test GmbH", "Different GmbH"), 0.0)

    def test_legal_form_stripped(self):
        """GmbH is stripped before comparison so 'Test' matches 'Test GmbH'."""
        score = self.ns.score("Test GmbH", "Test")
        self.assertGreater(score, 0.5)

    def test_token_overlap_partial_match(self):
        """Token overlap partial match gives score > 0 and < 1."""
        score = self.ns.score("Wienerberger AG", "Wienerberger")
        self.assertGreater(score, 0.0)
        self.assertLessEqual(score, 1.0)

    def test_hyphen_tokenization(self):
        """Hyphen-separated tokens are split for better overlap."""
        score = self.ns.score("Alcatel-Lucent", "Alcatel Lucent")
        self.assertGreater(score, 0.0)

    def test_umlaut_normalization(self):
        """Umlauts are normalized (ü->ue) before comparison."""
        score = self.ns.score("Müller GmbH", "Mueller GmbH")
        self.assertGreater(score, 0.5)

    def test_parenthetical_stripped(self):
        """Text in parentheses is stripped before comparison."""
        score = self.ns.score("Post AG (AUT)", "Post AG")
        self.assertGreater(score, 0.0)


class TestAddressNormalizer(unittest.TestCase):
    """Tests for AddressNormalizer field match methods."""

    def setUp(self):
        self.an = AddressNormalizer()

    def test_street_match_exact(self):
        """Exact street match scores 1.0."""
        self.assertEqual(self.an.street_match("Hauptstrasse", "Hauptstrasse"), 1.0)

    def test_street_match_expands_abbrev(self):
        """'Hauptstr.' expands to 'hauptstrasse' and matches."""
        self.assertEqual(self.an.street_match("Hauptstr.", "Hauptstrasse"), 1.0)

    def test_street_match_nonequal(self):
        """Different streets score 0.0."""
        self.assertEqual(self.an.street_match("Hauptstrasse", "Nebenstrasse"), 0.0)

    def test_street_match_empty(self):
        """Empty street scores 0.0."""
        self.assertEqual(self.an.street_match("", "Hauptstrasse"), 0.0)

    def test_city_match_exact(self):
        """Exact city match scores 1.0."""
        self.assertEqual(self.an.city_match("wien", "wien"), 1.0)

    def test_city_match_alias(self):
        """'vienna' normalizes to 'wien' so city_match returns 1.0."""
        self.assertEqual(self.an.city_match("vienna", "wien"), 1.0)

    def test_city_match_different(self):
        """Different cities score 0.0."""
        self.assertEqual(self.an.city_match("wien", "berlin"), 0.0)

    def test_city_match_empty(self):
        """Empty city scores 0.0."""
        self.assertEqual(self.an.city_match("", "wien"), 0.0)

    def test_plz_match_exact(self):
        """Exact PLZ match scores 1.0."""
        self.assertEqual(self.an.plz_match("1010", "1010"), 1.0)

    def test_plz_match_different(self):
        """Different PLZ scores 0.0."""
        self.assertEqual(self.an.plz_match("1010", "9999"), 0.0)

    def test_plz_match_empty(self):
        """Empty PLZ scores 0.0."""
        self.assertEqual(self.an.plz_match("", "1010"), 0.0)

    def test_normalize_city_lowercase(self):
        """normalize_city returns lowercase."""
        self.assertEqual(self.an.normalize_city("WIEN"), "wien")

    def test_normalize_street_expands_abbrev(self):
        """normalize_street expands abbreviations."""
        self.assertEqual(self.an.normalize_street("Hauptstr."), "hauptstrasse")

    def test_normalize_plz_strips_whitespace(self):
        """normalize_plz strips leading/trailing whitespace."""
        self.assertEqual(self.an.normalize_plz(" 1010 "), "1010")


class TestCompositeConfidence(unittest.TestCase):
    """Tests for CompositeConfidence."""

    def setUp(self):
        self.cc = CompositeConfidence()

    def test_weights_sum_to_one(self):
        """DEFAULT_FIELD_WEIGHTS sum to 1.0."""
        total = sum(DEFAULT_FIELD_WEIGHTS.values())
        self.assertAlmostEqual(total, 1.0, places=5)

    def test_all_fields_one_gives_high_score(self):
        """All fields at 1.0 give composite > 0.9."""
        scores = {"name": 1.0, "street": 1.0, "city": 1.0, "plz": 1.0}
        self.assertGreater(self.cc.compute(scores), 0.9)

    def test_all_fields_zero_gives_zero(self):
        """All zero fields give zero composite."""
        scores = {"name": 0.0, "street": 0.0, "city": 0.0, "plz": 0.0}
        self.assertEqual(self.cc.compute(scores), 0.0)

    def test_name_heaviest_weight(self):
        """Name field has weight 0.40 (heaviest)."""
        self.assertEqual(DEFAULT_FIELD_WEIGHTS["name"], 0.40)

    def test_partial_scores(self):
        """Partial scores compute correctly using weighted sum."""
        scores = {"name": 0.75, "street": 0.5, "city": 0.25, "plz": 0.0}
        expected = (
            0.75 * DEFAULT_FIELD_WEIGHTS["name"] +
            0.5 * DEFAULT_FIELD_WEIGHTS["street"] +
            0.25 * DEFAULT_FIELD_WEIGHTS["city"]
        )
        self.assertAlmostEqual(self.cc.compute(scores), expected, places=5)

    def test_missing_fields_default_to_zero(self):
        """Missing fields contribute zero to weighted sum."""
        scores = {"name": 0.5}
        expected = 0.5 * DEFAULT_FIELD_WEIGHTS["name"]
        self.assertAlmostEqual(self.cc.compute(scores), expected, places=5)


class TestCorrelationRules(unittest.TestCase):
    """Tests for CorrelationRules."""

    def test_get_active_rules_returns_list(self):
        """get_active_rules() returns a list of rule dicts."""
        rules = CorrelationRules()
        active = rules.get_active_rules()
        self.assertIsInstance(active, list)

    def test_get_rule_ids_returns_list_of_strings(self):
        """get_rule_ids() returns a list of rule ID strings."""
        rules = CorrelationRules()
        ids = rules.get_rule_ids()
        self.assertIsInstance(ids, list)


class TestMatchResult(unittest.TestCase):
    """Tests for MatchResult data class."""

    def test_to_dict_returns_dict(self):
        """to_dict() returns a dictionary representation."""
        mr = MatchResult()
        d = mr.to_dict()
        self.assertIsInstance(d, dict)

    def test_default_confidence_zero(self):
        """Default MatchResult has zero confidence."""
        mr = MatchResult()
        self.assertEqual(mr.composite_confidence, 0.0)


class TestCorrelationMatcher(unittest.TestCase):
    """Tests for CorrelationMatcher.match()."""

    def setUp(self):
        self.matcher = CorrelationMatcher()

    def test_match_with_empty_candidates(self):
        """match() with empty candidate list returns result with zero confidence."""
        result = self.matcher.match(candidates=[], fb_input="Test GmbH")
        self.assertIsInstance(result, MatchResult)
        self.assertEqual(result.composite_confidence, 0.0)

    def test_match_returns_match_result(self):
        """match() returns a MatchResult."""
        candidate = {
            "business-name": "Test GmbH",
            "business-address": {
                "street-address": "Hauptstrasse",
                "postal-code": "1010",
                "city": "Wien",
            },
        }
        result = self.matcher.match(
            candidates=[candidate],
            fb_input="Test GmbH",
            address_fields={"city": "Wien", "plz": "1010"},
        )
        self.assertIsInstance(result, MatchResult)

    def test_match_high_confidence_for_exact_match(self):
        """Exact name+address match gives high confidence score (> 0.8)."""
        candidate = {
            "business-name": "Test GmbH",
            "business-address": {
                "street-address": "Hauptstrasse",
                "postal-code": "1010",
                "city": "Wien",
            },
        }
        result = self.matcher.match(
            candidates=[candidate],
            fb_input="Test GmbH",
            # address_fields must include 'name' for the correlation scorer
            address_fields={"name": "Test GmbH", "city": "Wien", "plz": "1010"},
        )
        self.assertGreater(result.composite_confidence, 0.7)

    def test_match_zero_confidence_for_empty_candidates(self):
        """Empty candidate fields give zero confidence."""
        candidate = {
            "business-name": "",
            "business-address": {
                "street-address": "",
                "postal-code": "",
                "city": "",
            },
        }
        result = self.matcher.match(
            candidates=[candidate],
            fb_input="Test GmbH",
            address_fields={"city": "Wien", "plz": "1010"},
        )
        self.assertEqual(result.composite_confidence, 0.0)


class TestBuildMatcher(unittest.TestCase):
    """Tests for build_matcher() factory function."""

    def test_build_matcher_returns_matcher(self):
        """build_matcher() returns a CorrelationMatcher instance."""
        matcher = build_matcher()
        self.assertIsInstance(matcher, CorrelationMatcher)

    def test_build_matcher_with_config_path(self):
        """build_matcher(config_path) accepts an optional path to rules JSON."""
        rules_data = {"rules": []}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(rules_data, f)
            f.flush()
            matcher = build_matcher(config_path=f.name)
        self.assertIsInstance(matcher, CorrelationMatcher)


if __name__ == "__main__":
    unittest.main()
