"""Tests for the one-off historical catch-up script.

Mirrors ``tests/test_pipeline.py``'s mocking style (respx for "no data",
monkeypatched fetchers for a real flow-traced hour) -- no live network
calls.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Coroutine
from pathlib import Path

import httpx
import pytest
import respx

from oko.backfill import _CHUNK_HOURS, _chunk_windows, backfill
from oko.config import FLOW_TRACING_ZONES, Settings
from oko.emissions.factors import factors_for_zone
from oko.fetchers import entsoe
from oko.history import load_training_rows

FIXTURES = Path(__file__).parent / "fixtures"


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _settings(tmp_path: Path) -> Settings:
    return Settings(  # type: ignore[call-arg]
        entsoe_token="dummy-token",
        sqlite_path=tmp_path / "history.sqlite3",
        model_dir=tmp_path / "models",
        _env_file=None,
    )


@respx.mock
def test_backfill_with_no_data_upserts_nothing(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    no_data_xml = (FIXTURES / "entsoe_no_data.xml").read_text()
    respx.get(settings.entsoe_base_url).mock(return_value=httpx.Response(200, text=no_data_xml))

    upserted = _run(backfill(48, settings=settings))

    assert set(upserted) == set(FLOW_TRACING_ZONES)
    assert all(count == 0 for count in upserted.values())
    rows, _ = load_training_rows(settings.sqlite_path, "DE-LU")
    assert rows == []


def test_backfill_upserts_real_zone_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same hand-computed scenario as
    ``test_pipeline.test_run_pipeline_computes_flow_traced_intensity_for_fresh_data``:
    DE-LU (1000 MW own coal) imports 1000 MW of FR's clean nuclear."""
    settings = _settings(tmp_path)
    fresh_hour = dt.datetime(2026, 8, 1, 12, tzinfo=dt.UTC)

    async def fake_fetch_production(
        zone: str, start: dt.datetime, end: dt.datetime, *, client: object, settings: object
    ) -> list[entsoe.ProductionRecord]:
        if zone == "DE-LU":
            return [
                entsoe.ProductionRecord(
                    zone="DE-LU", timestamp=fresh_hour, by_category={"coal": 1000.0}
                )
            ]
        if zone == "FR":
            return [
                entsoe.ProductionRecord(
                    zone="FR", timestamp=fresh_hour, by_category={"nuclear": 1000.0}
                )
            ]
        raise entsoe.EntsoeNoDataError(f"no data for {zone}")

    async def fake_fetch_load(
        zone: str, start: dt.datetime, end: dt.datetime, *, client: object, settings: object
    ) -> list[entsoe.LoadRecord]:
        if zone in ("DE-LU", "FR"):
            return [entsoe.LoadRecord(zone=zone, timestamp=fresh_hour, load_mw=2000.0)]
        raise entsoe.EntsoeError(f"no load for {zone}")

    async def fake_fetch_exchange(
        zone1: str,
        zone2: str,
        start: dt.datetime,
        end: dt.datetime,
        *,
        client: object,
        settings: object,
    ) -> list[entsoe.ExchangeRecord]:
        if {zone1, zone2} == {"DE-LU", "FR"}:
            return [
                entsoe.ExchangeRecord(
                    zone_from="DE-LU", zone_to="FR", timestamp=fresh_hour, net_flow_mw=-1000.0
                )
            ]
        raise entsoe.EntsoeError(f"no data for {zone1}-{zone2}")

    monkeypatch.setattr(entsoe, "fetch_production", fake_fetch_production)
    monkeypatch.setattr(entsoe, "fetch_load", fake_fetch_load)
    monkeypatch.setattr(entsoe, "fetch_exchange", fake_fetch_exchange)

    upserted = _run(backfill(48, settings=settings))

    assert upserted["DE-LU"] == 1
    assert upserted["FR"] == 1
    assert all(count == 0 for zone, count in upserted.items() if zone not in ("DE-LU", "FR"))

    rows, targets = load_training_rows(settings.sqlite_path, "DE-LU")
    assert [row.timestamp for row in rows] == [fresh_hour]
    de_lu_coal_factor = factors_for_zone("DE-LU")["coal"]
    expected = (1000.0 * de_lu_coal_factor + 1000.0 * 0.0) / 2000.0
    assert targets[0] == pytest.approx(expected)


def test_default_backfill_hours_comfortably_exceeds_min_training_rows() -> None:
    from oko.backfill import DEFAULT_BACKFILL_HOURS
    from oko.pipeline import MIN_TRAINING_ROWS

    assert DEFAULT_BACKFILL_HOURS > MIN_TRAINING_ROWS


def test_chunk_windows_splits_into_chunk_sized_pieces_covering_the_whole_span() -> None:
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    end = start + dt.timedelta(hours=_CHUNK_HOURS * 2 + 5)

    windows = _chunk_windows(start, end)

    assert windows[0] == (start, start + dt.timedelta(hours=_CHUNK_HOURS))
    assert windows[-1][1] == end
    # Contiguous, no gaps or overlaps.
    import itertools

    for (_, prev_end), (next_start, _) in itertools.pairwise(windows):
        assert prev_end == next_start
    assert sum((w[1] - w[0] for w in windows), dt.timedelta()) == end - start


def test_chunk_windows_single_chunk_when_span_fits() -> None:
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    end = start + dt.timedelta(hours=48)
    assert _chunk_windows(start, end) == [(start, end)]


def test_backfill_fetches_each_chunk_window_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A window wider than ``_CHUNK_HOURS`` must drive multiple sequential
    ``fetch_and_trace_window`` calls, one per chunk, rather than one
    oversized request spanning the whole span."""
    import oko.backfill as backfill_module
    from oko.pipeline import WindowData

    settings = _settings(tmp_path)
    calls: list[tuple[dt.datetime, dt.datetime]] = []

    async def fake_fetch_and_trace_window(
        start: dt.datetime, end: dt.datetime, *, client: object, settings: object
    ) -> WindowData:
        calls.append((start, end))
        return WindowData(
            production={},
            exchanges=[],
            load_by_zone={},
            traced_series={},
            direct_factor_tables={},
            price_by_zone={},
        )

    monkeypatch.setattr(backfill_module, "fetch_and_trace_window", fake_fetch_and_trace_window)

    total_hours = _CHUNK_HOURS * 2 + 5
    _run(backfill(total_hours, settings=settings))

    assert len(calls) == 3
    assert calls[0][1] == calls[1][0]  # contiguous
    assert calls[1][1] == calls[2][0]
    assert calls[-1][1] - calls[0][0] == dt.timedelta(hours=total_hours)
