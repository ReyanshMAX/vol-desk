"""In-process scheduler. Single-threaded, jobs run to completion
(docs/ARCHITECTURE.md notes) -- no concurrent SQLite writers.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

import pandas_market_calendars as mcal

logger = logging.getLogger("vol_desk.scheduler")

_NYSE = mcal.get_calendar("NYSE")

# Poll granularity. Small enough that the tightest cadence (risk_monitor,
# 60s) doesn't drift meaningfully; large enough not to busy-loop.
_TICK_S = 1.0


@dataclass
class _Job:
    name: str
    interval_s: int
    fn: Callable[[], None]
    market_hours_only: bool
    next_run: float = field(default_factory=time.monotonic)


class Scheduler:
    def __init__(self) -> None:
        self._jobs: list[_Job] = []
        self._running = False

    def register(self, name: str, interval_s: int, fn: Callable[[], None],
                 market_hours_only: bool) -> None:
        self._jobs.append(_Job(name=name, interval_s=interval_s, fn=fn,
                                market_hours_only=market_hours_only))
        logger.info("registered job %s interval=%ss market_hours_only=%s",
                    name, interval_s, market_hours_only)

    def start(self) -> None:
        """Enter the scheduling loop. Blocks until interrupted."""
        self._running = True
        logger.info("scheduler starting with %d jobs", len(self._jobs))
        while self._running:
            now = time.monotonic()
            market_open = is_market_open()
            for job in self._jobs:
                if now < job.next_run:
                    continue
                if job.market_hours_only and not market_open:
                    # reschedule without running; do not let a closed-market
                    # skip pile up into a burst at the open
                    job.next_run = now + job.interval_s
                    continue
                job.next_run = now + job.interval_s
                _run_job(job)
            time.sleep(_TICK_S)

    def stop(self) -> None:
        self._running = False


def _run_job(job: _Job) -> None:
    started = time.monotonic()
    try:
        job.fn()
    except Exception:
        logger.exception("job %s raised", job.name)
    else:
        elapsed_ms = (time.monotonic() - started) * 1000
        logger.debug("job %s completed in %.0fms", job.name, elapsed_ms)


def is_market_open(when: datetime | None = None) -> bool:
    """True during the regular NYSE session (09:30-16:00 ET) on a trading day."""
    when = when or datetime.now(tz=_NYSE.tz)
    if when.tzinfo is None:
        when = when.astimezone(_NYSE.tz)
    schedule = _NYSE.schedule(start_date=when.date(), end_date=when.date())
    if schedule.empty:
        return False
    market_open = schedule.iloc[0]["market_open"].tz_convert(_NYSE.tz)
    market_close = schedule.iloc[0]["market_close"].tz_convert(_NYSE.tz)
    return market_open <= when <= market_close


def in_trading_window(open_str: str, close_str: str, when: datetime | None = None) -> bool:
    """True inside the configured entry window (params.yaml: trading_window),
    which is narrower than the full session -- no entries in the first/last
    30 minutes (docs/ARCHITECTURE.md). Distinct from is_market_open, which
    gates the scheduler's market_hours_only jobs generally.
    """
    if not is_market_open(when):
        return False
    when = when or datetime.now(tz=_NYSE.tz)
    if when.tzinfo is None:
        when = when.astimezone(_NYSE.tz)
    open_t = datetime.strptime(open_str, "%H:%M").time()
    close_t = datetime.strptime(close_str, "%H:%M").time()
    return open_t <= when.time() <= close_t
