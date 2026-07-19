#!/usr/bin/env python3
"""Unit tests for autonomous_batch.py (TST-04)."""

import json, os, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

# Ensure the project root is on sys.path so the standalone script
# (autonomous_batch.py) is importable as a top-level module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

# Create a temp directory with needed files and patch constants in the module
# We do this by patching sys.path + reloading or by using importlib to reload


class TestCheckVies(unittest.TestCase):
    """Tests for check_vies() function."""

    @patch("requests.post")
    def test_non_atu_returns_not_atu(self, mock_post):
        """UID not starting with ATU returns valid=False, error='not ATU'."""
        from autonomous_batch import check_vies
        result = check_vies("DE123456789")
        self.assertFalse(result["valid"])
        self.assertFalse(result["active"])
        self.assertEqual(result["error"], "not ATU")
        mock_post.assert_not_called()

    @patch("requests.post")
    def test_atu_valid_returns_true(self, mock_post):
        """ATU UID with valid=true response returns valid=True, active=True."""
        from autonomous_batch import check_vies
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<ns:valid>true</ns:valid><ns:name>Test Company</ns:name><ns:address>Test Address</ns:address>"
        mock_post.return_value = mock_resp

        result = check_vies("ATU12345678")
        self.assertTrue(result["valid"])
        self.assertTrue(result["active"])
        self.assertEqual(result["name"], "Test Company")
        self.assertEqual(result["address"], "Test Address")
        self.assertIsNone(result["error"])

    @patch("requests.post")
    def test_atu_invalid_returns_false(self, mock_post):
        """ATU UID with valid=false response returns valid=False, active=False."""
        from autonomous_batch import check_vies
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<ns:valid>false</ns:valid>"
        mock_post.return_value = mock_resp

        result = check_vies("ATU99999999")
        self.assertFalse(result["valid"])
        self.assertFalse(result["active"])

    @patch("requests.post")
    def test_http_error_returns_error_dict(self, mock_post):
        """Non-200 HTTP response returns error dict with HTTP status."""
        from autonomous_batch import check_vies
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_post.return_value = mock_resp

        result = check_vies("ATU12345678")
        self.assertFalse(result["valid"])
        self.assertFalse(result["active"])
        self.assertEqual(result["error"], "HTTP 500")

    @patch("requests.post")
    def test_exception_returns_error_dict(self, mock_post):
        """Exception during request returns error dict with exception message."""
        from autonomous_batch import check_vies
        mock_post.side_effect = Exception("connection timeout")

        result = check_vies("ATU12345678")
        self.assertFalse(result["valid"])
        self.assertFalse(result["active"])
        self.assertEqual(result["error"], "connection timeout")


class TestRetryQueueAndCheckpoint(unittest.TestCase):
    """Tests for retry queue and checkpoint file I/O."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def test_load_retry_queue_missing_file_returns_empty_list(self):
        """When retry queue file doesn't exist, load_retry_queue returns []."""
        import autonomous_batch
        original = autonomous_batch.RETRY_QUEUE_FILE
        fake_path = str(Path(self.temp_dir) / "nonexistent_queue.json")
        try:
            autonomous_batch.RETRY_QUEUE_FILE = fake_path
            result = autonomous_batch.load_retry_queue()
            self.assertEqual(result, [])
        finally:
            autonomous_batch.RETRY_QUEUE_FILE = original

    def test_save_and_load_retry_queue_round_trip(self):
        """save_retry_queue + load_retry_queue produces original data."""
        import autonomous_batch
        original = autonomous_batch.RETRY_QUEUE_FILE
        queue_file = str(Path(self.temp_dir) / "retry_queue.json")
        try:
            autonomous_batch.RETRY_QUEUE_FILE = queue_file
            test_queue = [{"idx": 1, "fb": "FN123", "name": "Test GmbH"}]
            autonomous_batch.save_retry_queue(test_queue)
            loaded = autonomous_batch.load_retry_queue()
            self.assertEqual(loaded, test_queue)
        finally:
            autonomous_batch.RETRY_QUEUE_FILE = original

    def test_get_checkpoint_missing_returns_defaults(self):
        """Missing checkpoint file returns default dict with -1 last_idx."""
        import autonomous_batch
        original = autonomous_batch.CHECKPOINT_FILE
        fake_path = str(Path(self.temp_dir) / "nonexistent_checkpoint.json")
        try:
            autonomous_batch.CHECKPOINT_FILE = fake_path
            result = autonomous_batch.get_checkpoint()
            self.assertEqual(result["last_idx"], -1)
            self.assertEqual(result["checked"], 0)
        finally:
            autonomous_batch.CHECKPOINT_FILE = original

    def test_save_and_get_checkpoint_round_trip(self):
        """save_checkpoint + get_checkpoint produces saved data."""
        import autonomous_batch
        original = autonomous_batch.CHECKPOINT_FILE
        ckpt_file = str(Path(self.temp_dir) / "checkpoint.json")
        try:
            autonomous_batch.CHECKPOINT_FILE = ckpt_file
            stats = {"checked": 10, "deleted": 2, "active": 5, "not_found": 3, "errors": 0}
            autonomous_batch.save_checkpoint(5, stats)
            result = autonomous_batch.get_checkpoint()
            self.assertEqual(result["last_idx"], 5)
            self.assertEqual(result["checked"], 10)
        finally:
            autonomous_batch.CHECKPOINT_FILE = original


class TestLoadEnv(unittest.TestCase):
    """Tests for load_env() function."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.env_file = Path(self.temp_dir) / ".env"

    def test_load_env_without_file_returns_current_env(self):
        """When env file doesn't exist, load_env returns current environment vars."""
        import autonomous_batch
        original_file = autonomous_batch.ENV_FILE
        try:
            autonomous_batch.ENV_FILE = "/nonexistent/.env"
            result = autonomous_batch.load_env()
            # Should include PATH at minimum
            self.assertIn("PATH", result)
        finally:
            autonomous_batch.ENV_FILE = original_file

    def test_load_env_with_file_returns_merged_env(self):
        """Env file values are merged into the returned environment dict."""
        import autonomous_batch
        original_file = autonomous_batch.ENV_FILE
        self.env_file.write_text("TEST_VAR=test_value\nANOTHER=123\n")
        try:
            autonomous_batch.ENV_FILE = str(self.env_file)
            result = autonomous_batch.load_env()
            self.assertEqual(result["TEST_VAR"], "test_value")
            self.assertEqual(result["ANOTHER"], "123")
        finally:
            autonomous_batch.ENV_FILE = original_file

    def test_load_env_skips_comments_and_empty_lines(self):
        """Lines starting with # or empty/whitespace-only lines are skipped."""
        import autonomous_batch
        original_file = autonomous_batch.ENV_FILE
        self.env_file.write_text("# comment\n\nKEY=value\n# another comment\n")
        try:
            autonomous_batch.ENV_FILE = str(self.env_file)
            result = autonomous_batch.load_env()
            self.assertEqual(result["KEY"], "value")
            self.assertNotIn("# comment", result)
        finally:
            autonomous_batch.ENV_FILE = original_file


class TestMergeToFinal(unittest.TestCase):
    """Tests for merge_to_final() function."""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def test_merge_updates_geloescht_from_batch(self):
        """merge_to_final updates GELÖSCHT=NaN/-1 rows using batch lookup."""
        import autonomous_batch

        original_merged = autonomous_batch.MERGED_FILE
        original_output = autonomous_batch.OUTPUT_FILE
        original_final = autonomous_batch.FINAL_OUTPUT

        merged_path = Path(self.temp_dir) / "merged.xlsx"
        batch_path = Path(self.temp_dir) / "batch.xlsx"
        output_path = Path(self.temp_dir) / "output.xlsx"

        try:
            autonomous_batch.MERGED_FILE = str(merged_path)
            autonomous_batch.OUTPUT_FILE = str(batch_path)
            autonomous_batch.FINAL_OUTPUT = str(output_path)

            # merged: FN 12345 has GELÖSCHT=NA, FN 67890 has GELÖSCHT=0
            merged_df = pd.DataFrame({
                "Firmenbuchnr": ["FN 12345", "FN 67890"],
                "Firmenname": ["Alpha GmbH", "Beta AG"],
                "GELÖSCHT": [pd.NA, 0],
            })
            merged_df.to_excel(merged_path, index=False)

            # batch: FN 12345 (→ 12345) has GELÖSCHT=1, FN 99999 has GELÖSCHT=0
            batch_df = pd.DataFrame({
                "Firmenbuchnr": ["FN 12345", "FN 99999"],
                "Firmenname": ["Alpha GmbH", "Gamma OG"],
                "GELÖSCHT": [1, 0],
            })
            batch_df.to_excel(batch_path, index=False)

            autonomous_batch.merge_to_final()

            result = pd.read_excel(output_path)
            alpha = result[result["Firmenbuchnr"] == "FN 12345"].iloc[0]["GELÖSCHT"]
            beta = result[result["Firmenbuchnr"] == "FN 67890"].iloc[0]["GELÖSCHT"]
            # Alpha: NA → 1 (updated from batch)
            self.assertEqual(alpha, 1)
            # Beta: 0 stays 0 (batch lookup matched but original was already set to valid 0)
            self.assertEqual(beta, 0)
        finally:
            autonomous_batch.MERGED_FILE = original_merged
            autonomous_batch.OUTPUT_FILE = original_output
            autonomous_batch.FINAL_OUTPUT = original_final


class TestPhase1RateLimitBackoff(unittest.TestCase):
    """Tests for the 429 backoff / circuit-breaker introduced in cycle-1
    (commit e6976a1). These tests prevent regressions of the backoff logic
    and document its expected behavior contract."""

    def setUp(self):
        import autonomous_batch
        self._mod = autonomous_batch
        self._original_input = autonomous_batch.INPUT_FILE
        self._original_output = autonomous_batch.OUTPUT_FILE
        self._original_merged = autonomous_batch.MERGED_FILE
        self._original_ckpt = autonomous_batch.CHECKPOINT_FILE
        self._temp_dir = tempfile.mkdtemp()
        input_path = Path(self._temp_dir) / "input.xlsx"
        output_path = Path(self._temp_dir) / "output.xlsx"
        merged_path = Path(self._temp_dir) / "merged.xlsx"
        ckpt_path = Path(self._temp_dir) / "checkpoint.json"
        autonomous_batch.INPUT_FILE = str(input_path)
        autonomous_batch.OUTPUT_FILE = str(output_path)
        autonomous_batch.MERGED_FILE = str(merged_path)
        autonomous_batch.CHECKPOINT_FILE = str(ckpt_path)
        # 5 firms, all with valid UIDs (use the real column name: UID_Nummer)
        df = pd.DataFrame({
            "Firmenname": [f"Firm{i}" for i in range(5)],
            "Firmenbuchnr": [f"FN{i}" for i in range(5)],
            "UID_Nummer": [f"ATU{i}" for i in range(5)],
            "GELÖSCHT": [None] * 5,
        })
        df.to_excel(input_path, index=False)

    def tearDown(self):
        self._mod.INPUT_FILE = self._original_input
        self._mod.OUTPUT_FILE = self._original_output
        self._mod.MERGED_FILE = self._original_merged
        self._mod.CHECKPOINT_FILE = self._original_ckpt

    def _mock_response(self, code, headers=None, body=None):
        m = MagicMock()
        m.status_code = code
        m.headers = headers or {}
        if body is None and code == 200:
            body = {"companies": [{"reg-status": "cancelled", "name": "Test"}]}
        m.json.return_value = body or {}
        return m

    @patch("autonomous_batch.time.sleep")
    @patch("requests.get")
    def test_single_429_uses_short_backoff(self, mock_get, mock_sleep):
        """Single 429 → exponential backoff ~2s (±20% jitter). NOT the old
        0.2-0.5s. Note: each firm also triggers an unconditional trailing
        sleep of 0.8-1.3s, so sleep[0] is the 429 backoff and sleep[-1] for
        each firm is the trailing pacing sleep."""
        mock_get.side_effect = [
            self._mock_response(429),  # firm 0
            self._mock_response(200),  # firm 1
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
        ]
        self._mod.run_phase1()
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        # 5 firms × 1 trailing sleep = 5 trailing sleeps. Plus 1 429 backoff.
        # First sleep is the 429 backoff (streak=1 → ~2s).
        first_sleep = sleeps[0]
        self.assertGreaterEqual(first_sleep, 1.6)  # 2.0 * 0.8
        self.assertLessEqual(first_sleep, 2.4)      # 2.0 * 1.2
        self.assertGreater(first_sleep, 1.0,
                           "regression: single 429 backoff fell back to 0.2-0.5s")

    @patch("autonomous_batch.time.sleep")
    @patch("requests.get")
    def test_three_consecutive_429s_triggers_circuit_breaker(self, mock_get, mock_sleep):
        """3 consecutive 429s → 60s circuit-breaker pause. Sleep pattern:
        [429-backoff-streak1, trailing, 429-backoff-streak2, trailing,
         429-storm-60s, trailing, trailing, trailing] for 5 firms."""
        mock_get.side_effect = [
            self._mock_response(429),  # firm 0 → streak=1
            self._mock_response(429),  # firm 1 → streak=2
            self._mock_response(429),  # firm 2 → streak=3 → 60s breaker
            self._mock_response(200),  # firm 3
            self._mock_response(200),  # firm 4
        ]
        self._mod.run_phase1()
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        # All 429 backoffs are at least > 1s. Find the breaker (≥ 48s).
        long_sleeps = [s for s in sleeps if s >= 48.0]
        self.assertEqual(len(long_sleeps), 1,
                         f"expected exactly one 60s circuit breaker, got sleeps={sleeps}")
        # And 3 backoffs total, each > 1s (proves we escalated, didn't return to 0.2-0.5s)
        backoffs = [s for s in sleeps if s > 1.5]
        self.assertGreaterEqual(len(backoffs), 3,
                                f"expected at least 3 429 backoffs, got {backoffs}")

    @patch("autonomous_batch.time.sleep")
    @patch("requests.get")
    def test_streak_resets_on_200(self, mock_get, mock_sleep):
        """After a 200, the consecutive_429 counter resets — a later 429
        should get the streak=1 backoff (~2s), not the streak=4 backoff (~16s)."""
        mock_get.side_effect = [
            self._mock_response(429),  # firm 0 → streak=1 → ~2s backoff
            self._mock_response(200),  # firm 1: success → streak resets
            self._mock_response(429),  # firm 2: 429 → streak=1 again → ~2s
            self._mock_response(200),
            self._mock_response(200),
        ]
        self._mod.run_phase1()
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        # Both 429 backoffs should be in the streak=1 range (~1.6-2.4s)
        backoffs = [s for s in sleeps if s > 1.5]
        self.assertEqual(len(backoffs), 2,
                         f"expected 2 429 backoffs (one for each 429), got {backoffs}")
        for b in backoffs:
            self.assertGreaterEqual(b, 1.6,
                f"regression: 429 backoff {b}s not in streak=1 range (1.6-2.4)")
            self.assertLessEqual(b, 2.4,
                f"regression: 429 backoff {b}s escalated past streak=1 range")
        # Critical: neither backoff should be in the streak=4 range (12.8-19.2s)
        for b in backoffs:
            self.assertLess(b, 5.0,
                "regression: streak did not reset on 200 — 2nd 429 got "
                "escalated backoff instead of streak=1 backoff")

    @patch("autonomous_batch.time.sleep")
    @patch("requests.get")
    def test_retry_after_header_respected(self, mock_get, mock_sleep):
        """If server sends Retry-After: 30, we honor it (±20% jitter),
        overriding our exponential backoff."""
        mock_get.side_effect = [
            self._mock_response(429, headers={"Retry-After": "30"}),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
        ]
        self._mod.run_phase1()
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        first_sleep = sleeps[0]
        self.assertGreaterEqual(first_sleep, 24.0)  # 30 * 0.8
        self.assertLessEqual(first_sleep, 36.0)     # 30 * 1.2

    @patch("autonomous_batch.time.sleep")
    @patch("requests.get")
    def test_429_queues_to_retry_queue(self, mock_get, mock_sleep):
        """429 firms are queued (GELÖSCHT=-1) for phase 2/3, not marked processed."""
        mock_get.side_effect = [
            self._mock_response(429),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
        ]
        self._mod.run_phase1()
        result = pd.read_excel(self._mod.OUTPUT_FILE)
        firm0 = result[result["Firmenbuchnr"] == "FN0"].iloc[0]
        self.assertEqual(firm0["GELÖSCHT"], -1,
                         "429 firm should be marked -1 (queued for retry)")

    @patch("autonomous_batch.time.sleep")
    @patch("requests.get")
    def test_retry_after_http_date_format(self, mock_get, mock_sleep):
        """RFC 7231 §7.1.3: Retry-After can be HTTP-date. Verify we parse it
        and compute the seconds-until delta correctly. (Cycle-3 review from
        DeepSeek V4 Pro + MiniMax-M3 + v4-flash identified this gap.)"""
        from datetime import datetime, timedelta, timezone
        # Set Retry-After to 30 seconds from now (HTTP-date format)
        future = datetime.now(timezone.utc) + timedelta(seconds=30)
        http_date = future.strftime("%a, %d %b %Y %H:%M:%S GMT")

        mock_get.side_effect = [
            self._mock_response(429, headers={"Retry-After": http_date}),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
        ]
        self._mod.run_phase1()
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        first_sleep = sleeps[0]
        # Should be ~30s (±20% jitter = 24-36), NOT the 5s fallback
        self.assertGreaterEqual(first_sleep, 24.0,
            f"HTTP-date parsing failed: sleep={first_sleep}, expected ~30s")
        self.assertLessEqual(first_sleep, 36.0,
            f"HTTP-date parsing failed: sleep={first_sleep}, expected ~30s")

    @patch("autonomous_batch.time.sleep")
    @patch("requests.get")
    def test_retry_after_huge_value_capped_at_3600(self, mock_get, mock_sleep):
        """Defensive cap: Retry-After=99999 should be capped at 3600s. Cycle-3
        review from DeepSeek v4-pro + v4-flash flagged missing upper bound."""
        mock_get.side_effect = [
            self._mock_response(429, headers={"Retry-After": "99999"}),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
        ]
        self._mod.run_phase1()
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        first_sleep = sleeps[0]
        # Capped at 3600 * 1.2 = 4320
        self.assertLessEqual(first_sleep, 4320.0,
            f"Retry-After cap failed: sleep={first_sleep} should be <= 4320 (3600*1.2)")

    @patch("autonomous_batch.time.sleep")
    @patch("requests.get")
    def test_retry_after_malformed_falls_through_to_exponential(self, mock_get, mock_sleep):
        """If Retry-After is neither a number nor a valid HTTP-date, fall
        through to the exponential backoff (NOT a static 5s fallback).
        Cycle-3 review from MiniMax-M3 identified this regression risk."""
        mock_get.side_effect = [
            self._mock_response(429, headers={"Retry-After": "garbage-not-a-date"}),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
        ]
        self._mod.run_phase1()
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        first_sleep = sleeps[0]
        # Streak=1 → exponential wait = 2s, ±20% jitter (1.6-2.4s)
        self.assertGreaterEqual(first_sleep, 1.6,
            f"malformed Retry-After should fall through to exponential (streak=1 → ~2s), got {first_sleep}")
        self.assertLessEqual(first_sleep, 2.4)

    @patch("autonomous_batch.time.sleep")
    @patch("requests.get")
    def test_retry_after_negative_pinned_to_zero(self, mock_get, mock_sleep):
        """Negative Retry-After must not crash (regression guard for 913b809).
        Pinned to 0 → uniform(0, 0) = 0, so the sleep is essentially 0."""
        mock_get.side_effect = [
            self._mock_response(429, headers={"Retry-After": "-5"}),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
            self._mock_response(200),
        ]
        # Should NOT raise — that was the bug 913b809 fixed
        try:
            self._mod.run_phase1()
        except ValueError as e:
            self.fail(f"negative Retry-After crashed: {e}")
        sleeps = [c.args[0] for c in mock_sleep.call_args_list]
        first_sleep = sleeps[0]
        # Pinned to 0 → sleep(0)
        self.assertGreaterEqual(first_sleep, 0.0)
        self.assertLessEqual(first_sleep, 0.0)


if __name__ == "__main__":
    unittest.main()
