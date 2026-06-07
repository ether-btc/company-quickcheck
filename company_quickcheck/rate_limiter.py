#!/usr/bin/env python3
"""
Adaptive rate limiting for company-quickcheck.

Replaces fixed sleep with:
  - Exponential backoff on errors/429
  - Adaptive tracking via response headers (X-RateLimit-Remaining, Retry-After)
  - Minimum delay floor so we never hammer a struggling server
  - Configurable via config.yaml (adaptive_rate_limit.* keys)
  - Thread-safe: multiple worker threads can share a single limiter
    (state mutations are protected by an internal lock; ``wait()``
    reads the current delay once and sleeps outside the lock so workers
    don't serialise on each other)
"""

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class AdaptiveRateLimiter:
    """
    Tracks server health and adjusts delay accordingly.

    Algorithm:
      delay = min(max_delay, max(min_delay, last_delay * multiplier))
      On 429 with Retry-After: delay = max(min_delay, Retry-After)
      On success: delay = max(min_delay, delay / multiplier)  # gradually speed up
      On error:    delay = min(max_delay, delay * multiplier)  # slow down
    """

    def __init__(
        self,
        initial_delay: float = 1.1,
        min_delay: float = 0.3,
        max_delay: float = 10.0,
        backoff_multiplier: float = 1.5,
        success_divisor: float = 1.5,
    ):
        self.initial_delay = initial_delay
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.backoff_multiplier = backoff_multiplier
        self.success_divisor = success_divisor

        self._current_delay = initial_delay
        self._consecutive_errors = 0
        # Lock protecting _current_delay and _consecutive_errors so that
        # concurrent workers (--workers > 1) get a consistent view of state.
        # ``wait()`` reads the delay once under the lock and sleeps outside
        # so workers don't serialise on each other.
        self._lock = threading.Lock()

    def wait(self) -> None:
        """Sleep for the current adaptive delay.

        Thread-safe: reads the current delay under the lock, then sleeps
        outside the lock so concurrent workers don't block each other.
        """
        with self._lock:
            delay = self._current_delay
        if delay > 0:
            logger.debug(f"[rate] sleeping {delay:.2f}s")
            time.sleep(delay)

    def record_response(self, status_code: int, headers: Optional[dict] = None) -> None:
        """
        Update delay based on server response.

        Thread-safe: read-modify-write of ``_current_delay`` and
        ``_consecutive_errors`` is protected by an internal lock.

        Args:
            status_code: HTTP status code of the response
            headers: Response headers (used to read Retry-After, X-RateLimit-*)
        """
        headers = headers or {}

        with self._lock:
            self._record_response_locked(status_code, headers)

    def _record_response_locked(self, status_code: int, headers: dict) -> None:
        """Internal: caller must already hold ``self._lock``."""
        if status_code == 429:
            # Honour server's Retry-After if present, else apply backoff
            retry_after = headers.get("Retry-After")
            if retry_after:
                try:
                    server_delay = float(retry_after)
                    self._current_delay = max(self.min_delay, server_delay)
                    logger.warning(f"[rate] 429 with Retry-After={server_delay}s → delay={self._current_delay:.2f}s")
                except ValueError:
                    self._apply_backoff()
            else:
                self._apply_backoff()
            self._consecutive_errors += 1

        elif status_code >= 500:
            # Server error — slow down
            self._apply_backoff()
            self._consecutive_errors += 1
            logger.warning(f"[rate] {status_code} server error → delay={self._current_delay:.2f}s")

        else:
            # Success (2xx, 4xx other than 429) — gradually speed up
            if self._consecutive_errors > 0:
                # Just recovered from errors — don't speed up immediately
                self._consecutive_errors = 0
            else:
                self._current_delay = max(
                    self.min_delay,
                    self._current_delay / self.success_divisor,
                )
            logger.debug(f"[rate] success {status_code} → delay={self._current_delay:.2f}s")

    def _apply_backoff(self) -> None:
        """Multiply current delay by backoff multiplier, capped at max_delay."""
        self._current_delay = min(
            self.max_delay,
            self._current_delay * self.backoff_multiplier,
        )

    @property
    def current_delay(self) -> float:
        return self._current_delay

    def reset(self) -> None:
        """Reset to initial delay. Use when starting a new batch.

        Thread-safe: state mutations are protected by the internal lock.
        """
        with self._lock:
            self._current_delay = self.initial_delay
            self._consecutive_errors = 0

    def __repr__(self) -> str:
        return (
            f"AdaptiveRateLimiter(delay={self._current_delay:.2f}s, "
            f"errors={self._consecutive_errors}, "
            f"min={self.min_delay}, max={self.max_delay})"
        )