#!/usr/bin/env python3
"""Unit tests for company_quickcheck.rate_limiter module."""

import unittest
from company_quickcheck.rate_limiter import AdaptiveRateLimiter


class TestAdaptiveRateLimiter(unittest.TestCase):
    """Tests for AdaptiveRateLimiter class."""

    def setUp(self):
        """Create a fresh limiter for each test."""
        self.limiter = AdaptiveRateLimiter(
            initial_delay=1.0,
            min_delay=0.2,
            max_delay=8.0,
            backoff_multiplier=2.0,
            success_divisor=2.0,
        )

    # --- init / reset ---

    def test_initial_delay(self):
        """Starts at initial_delay."""
        self.assertAlmostEqual(self.limiter.current_delay, 1.0)

    def test_reset(self):
        """reset() restores initial_delay and clears error count."""
        self.limiter._current_delay = 5.0
        self.limiter._consecutive_errors = 3
        self.limiter.reset()
        self.assertAlmostEqual(self.limiter.current_delay, 1.0)
        self.assertEqual(self.limiter._consecutive_errors, 0)

    # --- wait (no-op, just confirm it doesn't throw) ---

    def test_wait_does_not_raise(self):
        """wait() must not raise."""
        limiter = AdaptiveRateLimiter(initial_delay=0.01, min_delay=0.01, max_delay=1.0)
        limiter.wait()  # should be instant since delay is tiny but > 0

    # --- success responses (2xx) ---

    def test_success_lowers_delay(self):
        """2xx responses reduce delay toward min_delay."""
        self.limiter._current_delay = 4.0
        self.limiter.record_response(200)
        self.assertAlmostEqual(self.limiter.current_delay, 2.0)  # 4.0 / 2.0

    def test_success_hits_min_delay_floor(self):
        """Delay never goes below min_delay."""
        self.limiter._current_delay = 0.3
        self.limiter.record_response(200)
        self.assertAlmostEqual(self.limiter.current_delay, 0.2)

    def test_success_after_error_does_not_speed_up_immediately(self):
        """Recovering from errors — delay stays flat for one success cycle."""
        self.limiter._current_delay = 4.0
        self.limiter._consecutive_errors = 2
        self.limiter.record_response(200)
        # Should NOT divide — stays at 4.0 because we were in error recovery
        self.assertAlmostEqual(self.limiter.current_delay, 4.0)
        # Second success: now speed up kicks in
        self.limiter.record_response(200)
        self.assertAlmostEqual(self.limiter.current_delay, 2.0)

    def test_success_4xx_non_429(self):
        """4xx other than 429 also treated as success (server is alive)."""
        self.limiter._current_delay = 4.0
        self.limiter.record_response(404)
        self.assertAlmostEqual(self.limiter.current_delay, 2.0)

    # --- rate limit (429) ---

    def test_429_applies_backoff(self):
        """429 doubles the current delay."""
        self.limiter._current_delay = 2.0
        self.limiter.record_response(429)
        self.assertAlmostEqual(self.limiter.current_delay, 4.0)

    def test_429_capped_at_max_delay(self):
        """Backoff respects max_delay."""
        self.limiter._current_delay = 6.0
        self.limiter.record_response(429)
        # 6.0 * 2.0 = 12.0 → capped at 8.0
        self.assertAlmostEqual(self.limiter.current_delay, 8.0)

    def test_429_with_retry_after_header(self):
        """429 with Retry-After header uses server value."""
        self.limiter._current_delay = 1.0
        self.limiter.record_response(429, headers={"Retry-After": "5"})
        self.assertAlmostEqual(self.limiter.current_delay, 5.0)

    def test_429_with_retry_after_below_min_delay(self):
        """Retry-After less than min_delay is lifted to min_delay."""
        self.limiter._current_delay = 1.0
        self.limiter.record_response(429, headers={"Retry-After": "0.1"})
        self.assertAlmostEqual(self.limiter.current_delay, 0.2)  # min_delay floor

    def test_429_non_numeric_retry_after_falls_back_to_backoff(self):
        """Non-numeric Retry-After → fallback to multiplicative backoff."""
        self.limiter._current_delay = 2.0
        self.limiter.record_response(429, headers={"Retry-After": "not-a-number"})
        self.assertAlmostEqual(self.limiter.current_delay, 4.0)  # backoff applied

    # --- server errors (5xx) ---

    def test_500_applies_backoff(self):
        """5xx server errors apply backoff."""
        self.limiter._current_delay = 2.0
        self.limiter.record_response(500)
        self.assertAlmostEqual(self.limiter.current_delay, 4.0)

    def test_500_hits_max_delay_cap(self):
        """5xx backoff respects max_delay."""
        self.limiter._current_delay = 5.0
        self.limiter.record_response(503)
        self.assertAlmostEqual(self.limiter.current_delay, 8.0)

    # --- repr ---

    def test_repr(self):
        r = repr(self.limiter)
        self.assertIn("AdaptiveRateLimiter", r)
        self.assertIn("delay=", r)


class TestAdaptiveRateLimiterIntegration(unittest.TestCase):
    """Test the wait() + record_response cycle."""

    def test_wait_then_success_flow(self):
        """Simulate a full request cycle: wait → request → record."""
        limiter = AdaptiveRateLimiter(
            initial_delay=1.0,
            min_delay=0.1,
            max_delay=8.0,
            backoff_multiplier=2.0,
            success_divisor=2.0,
        )
        # Start at 1.0s
        self.assertAlmostEqual(limiter.current_delay, 1.0)
        # Simulate 3 successful requests
        for _ in range(3):
            limiter.wait()
            limiter.record_response(200)
        # Should have divided by 2 three times: 1.0 → 0.5 → 0.25 → 0.125
        self.assertAlmostEqual(limiter.current_delay, 0.125)

    def test_backoff_recovery_cycle(self):
        """429 → backoff → success (no speed-up) → success (speed-up)."""
        limiter = AdaptiveRateLimiter(
            initial_delay=1.0,
            min_delay=0.1,
            max_delay=8.0,
            backoff_multiplier=2.0,
            success_divisor=2.0,
        )
        limiter._current_delay = 1.0
        # 429: backoff to 2.0
        limiter.record_response(429)
        self.assertAlmostEqual(limiter.current_delay, 2.0)
        # First success after error: no speed-up, stays at 2.0
        limiter.record_response(200)
        self.assertAlmostEqual(limiter.current_delay, 2.0)
        # Second success: speed-up kicks in → 1.0
        limiter.record_response(200)
        self.assertAlmostEqual(limiter.current_delay, 1.0)


if __name__ == "__main__":
    unittest.main()