"""Connectivity backoff shared by mcp_client and alpaca_data.

Every job already retries on its own cadence, but with no backoff a dead
network (laptop asleep, wifi dropped, VPN down) means every tick still pays
the full DNS/TCP timeout and logs a full traceback -- every 60s for
risk_monitor. After enough consecutive failures, `check()` raises
immediately instead of attempting the network call, until the cooldown
elapses; the cooldown doubles on repeated failure and resets on success.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger("vol_desk.net_guard")


class ConnectivityUnavailable(RuntimeError):
    """Raised in place of attempting a call while a guard is in cooldown."""


class ConnectivityGuard:
    def __init__(self, name: str, *, failure_threshold: int = 3,
                 initial_cooldown_s: float = 60.0, max_cooldown_s: float = 900.0) -> None:
        self._name = name
        self._failure_threshold = failure_threshold
        self._initial_cooldown_s = initial_cooldown_s
        self._max_cooldown_s = max_cooldown_s
        self._consecutive_failures = 0
        self._cooldown_s = initial_cooldown_s
        self._blocked_until = 0.0

    def check(self) -> None:
        now = time.monotonic()
        if now < self._blocked_until:
            raise ConnectivityUnavailable(
                f"{self._name}: in backoff after {self._consecutive_failures} "
                f"consecutive failures, {self._blocked_until - now:.0f}s remaining"
            )

    def record_success(self) -> None:
        if self._consecutive_failures:
            logger.info("%s: connectivity restored after %d consecutive failures",
                        self._name, self._consecutive_failures)
        self._consecutive_failures = 0
        self._cooldown_s = self._initial_cooldown_s
        self._blocked_until = 0.0

    def record_failure(self) -> None:
        self._consecutive_failures += 1
        if self._consecutive_failures >= self._failure_threshold:
            self._blocked_until = time.monotonic() + self._cooldown_s
            logger.warning("%s: %d consecutive failures, backing off %.0fs",
                           self._name, self._consecutive_failures, self._cooldown_s)
            self._cooldown_s = min(self._cooldown_s * 2, self._max_cooldown_s)
