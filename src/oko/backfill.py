"""One-off historical catch-up so a fresh install doesn't wait ~2 weeks.

``oko.pipeline`` deliberately accumulates training history a little at a
time (see its module docstring) — a rolling ``HISTORY_FETCH_WINDOW_HOURS``
window fetched fresh every hourly run. That's the right steady-state
design, but it means a brand-new deployment can't produce a real forecast
until ``MIN_TRAINING_ROWS`` (~2 weeks) of hourly runs have accumulated.

ENTSO-E's Transparency Platform will answer a single production/exchange/
load query spanning weeks in one request (verified directly: a 21-day
window returns in one call, no pagination needed), so this script calls
``oko.pipeline.fetch_and_trace_window`` in ``_CHUNK_HOURS``-sized slices
(comfortably under that per-call limit) and upserts each slice's result
into the exact same SQLite history store — after which a normal
``oko.pipeline`` run has enough rows to train and forecast immediately.
Not wired into the regular hourly run; run this by hand (or once from
Jenkins) after a fresh install.

Because the upsert is idempotent on ``(zone, timestamp)`` (see
``oko.pipeline.upsert_zone_history``), re-running this with a large
enough ``--hours`` is also the way to backfill a *new history column*
(e.g. ``breakdown_percent_json``) into hours that were already
accumulated by an older version of the pipeline that didn't compute it
yet -- each hour still in ENTSO-E's retention window gets recomputed and
overwritten with the new column populated, older accumulated hours are
left untouched.
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt

import httpx
import structlog

from oko.config import FLOW_TRACING_ZONES, Settings, get_settings
from oko.history import init_db, load_training_rows
from oko.pipeline import MIN_TRAINING_ROWS, fetch_and_trace_window, upsert_zone_history

logger = structlog.get_logger(__name__)

#: 3 weeks — comfortably above ``MIN_TRAINING_ROWS`` (2 weeks) even for a
#: zone whose ENTSO-E data has some gaps, or is only hourly (not every
#: zone reports at DE-LU's 15-minute resolution).
DEFAULT_BACKFILL_HOURS = 24 * 21

#: Per-request window size -- comfortably under the ~21-day (504h) limit
#: a single ENTSO-E query reliably answers without pagination (see module
#: docstring). ``--hours`` beyond this is split into this many chunks,
#: fetched sequentially (not concurrently -- this is a one-off catch-up
#: script, not the latency-sensitive hourly path, so there's no reason to
#: burst more load at ENTSO-E than one chunk's worth at a time).
_CHUNK_HOURS = 24 * 20


def _chunk_windows(start: dt.datetime, end: dt.datetime) -> list[tuple[dt.datetime, dt.datetime]]:
    """Split ``[start, end)`` into ``_CHUNK_HOURS``-sized (or smaller final) windows."""
    windows = []
    chunk_start = start
    step = dt.timedelta(hours=_CHUNK_HOURS)
    while chunk_start < end:
        chunk_end = min(chunk_start + step, end)
        windows.append((chunk_start, chunk_end))
        chunk_start = chunk_end
    return windows


async def backfill(hours: int, *, settings: Settings | None = None) -> dict[str, int]:
    """Fetch ``hours`` of history for every zone and upsert it, chunked per ``_CHUNK_HOURS``.

    Args:
        hours: how far back from now to fetch, e.g. ``24 * 21`` for 3 weeks.
            Values larger than ``_CHUNK_HOURS`` are split into multiple
            sequential ENTSO-E queries rather than one oversized request.
        settings: application settings; defaults to ``get_settings()`` (see
            ``oko.pipeline.run_pipeline``, same override pattern for tests).

    Returns:
        zone -> total rows upserted across every chunk this run (see
        ``upsert_zone_history``).
    """
    settings = settings or get_settings()
    init_db(settings.sqlite_path)
    now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    start = now - dt.timedelta(hours=hours)
    windows = _chunk_windows(start, now)

    logger.info(
        "backfill.starting",
        start=start.isoformat(),
        end=now.isoformat(),
        hours=hours,
        chunks=len(windows),
    )
    upserted = dict.fromkeys(FLOW_TRACING_ZONES, 0)
    async with httpx.AsyncClient() as client:
        for i, (chunk_start, chunk_end) in enumerate(windows, start=1):
            logger.info(
                "backfill.chunk_starting",
                chunk=f"{i}/{len(windows)}",
                start=chunk_start.isoformat(),
                end=chunk_end.isoformat(),
            )
            window = await fetch_and_trace_window(
                chunk_start, chunk_end, client=client, settings=settings
            )
            for zone in FLOW_TRACING_ZONES:
                upserted[zone] += upsert_zone_history(
                    zone,
                    production_by_hour=window.production.get(zone),
                    load_by_hour=window.load_by_zone.get(zone),
                    traced=window.traced_series.get(zone, []),
                    settings=settings,
                    price_by_hour=window.price_by_zone.get(zone),
                )

    for zone in FLOW_TRACING_ZONES:
        total_rows, _ = load_training_rows(settings.sqlite_path, zone)
        logger.info(
            "backfill.zone_status",
            zone=zone,
            upserted_this_run=upserted[zone],
            total_accumulated=len(total_rows),
            ready_to_forecast=len(total_rows) >= MIN_TRAINING_ROWS,
        )
    return upserted


def main() -> None:
    """CLI entry point: ``python -m oko.backfill [--hours N]``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours",
        type=int,
        default=DEFAULT_BACKFILL_HOURS,
        help=(
            f"Hours of history to backfill (default: {DEFAULT_BACKFILL_HOURS}, i.e. 3 weeks). "
            f"Automatically split into {_CHUNK_HOURS}-hour ENTSO-E requests, so values covering "
            "months are safe to pass in one run."
        ),
    )
    args = parser.parse_args()
    asyncio.run(backfill(args.hours))


if __name__ == "__main__":
    main()
