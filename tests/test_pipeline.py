"""Integration-style tests for the pipeline orchestration.

All HTTP interaction is mocked (respx); the LightGBM training inside
``run_pipeline`` is real (fast on the tiny synthetic datasets used here).
No test hits a live network endpoint.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from collections.abc import Coroutine
from pathlib import Path

import eccodes
import httpx
import pytest
import respx

from oko.config import ALL_ZONES, Settings
from oko.emissions.factors import factors_for_zone
from oko.fetchers import entsoe
from oko.forecast.features import FeatureRow
from oko.history import HistoryRow, load_breakdown_training_rows, load_training_rows, upsert_rows
from oko.isoformat import format_iso_z
from oko.pipeline import (
    HISTORY_FETCH_WINDOW_HOURS,
    MIN_TRAINING_ROWS,
    InsufficientHistoryError,
    PipelineError,
    run_pipeline,
)

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


def _seed_history(db_path: Path, hours: int, *, include_breakdown: bool = False) -> None:
    """Seed enough synthetic accumulated history to clear MIN_TRAINING_ROWS."""
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    rows = [
        HistoryRow(
            zone="DE-LU",
            features=FeatureRow(
                timestamp=start + dt.timedelta(hours=i),
                hour_sin=0.0,
                hour_cos=0.0,
                dow_sin=0.0,
                dow_cos=0.0,
                month_sin=0.0,
                month_cos=0.0,
                residual_load_share=(i % 10) / 10.0,
                horizon_hours=0,
            ),
            target_g_per_kwh=100.0 + (i % 10) / 10.0 * 500.0,
            breakdown_percent=(
                {"wind": 10.0 + (i % 10), "coal": 90.0 - (i % 10)} if include_breakdown else None
            ),
        )
        for i in range(hours)
    ]
    upsert_rows(db_path, rows)


def _mock_all_entsoe_no_data(settings: Settings) -> None:
    """Mock every ENTSO-E call this run makes to return 'no matching data'."""
    no_data_xml = (FIXTURES / "entsoe_no_data.xml").read_text()
    respx.get(settings.entsoe_base_url).mock(return_value=httpx.Response(200, text=no_data_xml))


def _split_grib_messages(data: bytes) -> dict[str, bytes]:
    """Split a multi-message GRIB2 blob into ``{shortName: message_bytes}``."""
    offset = 0
    out: dict[str, bytes] = {}
    while offset < len(data):
        gid = eccodes.codes_new_from_message(data[offset:])
        try:
            length = eccodes.codes_get(gid, "totalLength")
            name = eccodes.codes_get(gid, "shortName")
        finally:
            eccodes.codes_release(gid)
        out[name] = data[offset : offset + length]
        offset += length
    return out


def _build_synthetic_gfs_object() -> tuple[bytes, str]:
    """One concatenated GFS object blob + matching ``.idx`` text, built from
    the real small fixture's 3 messages (10u/10v/sdswrf)."""
    msgs = _split_grib_messages((FIXTURES / "gfs_sample.grib2").read_bytes())
    order = [
        ("10u", "UGRD", "10 m above ground"),
        ("10v", "VGRD", "10 m above ground"),
        ("sdswrf", "DSWRF", "surface"),
    ]
    blob = b""
    idx_lines = []
    for i, (short_name, varname, level) in enumerate(order, start=1):
        chunk = msgs[short_name]
        idx_lines.append(f"{i}:{len(blob)}:d=2026090100:{varname}:{level}:1 hour fcst:")
        blob += chunk
    return blob, "\n".join(idx_lines) + "\n"


def _mock_noaa_success(base_url: str) -> None:
    """Mock every forecast hour's idx + range GETs as one successful shared
    global fetch (see ``noaa_gfs.fetch_forecast_for_zones`` -- one fetch
    serves every zone, not one per zone). Mocking only a subset of hours
    would make the retry/backoff loop run for real against every unmocked
    hour — correct behaviour, but far too slow for a test."""
    blob, idx_text = _build_synthetic_gfs_object()
    escaped = re.escape(base_url)
    respx.get(url__regex=rf"{escaped}/.*\.idx$").mock(
        return_value=httpx.Response(200, text=idx_text)
    )

    def _range_side_effect(request: httpx.Request) -> httpx.Response:
        range_header = request.headers["Range"]
        start_s, _, end_s = range_header.removeprefix("bytes=").partition("-")
        start = int(start_s)
        end = int(end_s) if end_s else None
        content = blob[start : end + 1] if end is not None else blob[start:]
        return httpx.Response(206, content=content)

    respx.get(url__regex=rf"{escaped}/(?!.*\.idx$).+$").mock(side_effect=_range_side_effect)


@respx.mock
def test_run_pipeline_raises_insufficient_history_on_fresh_install(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _mock_all_entsoe_no_data(settings)

    with pytest.raises(InsufficientHistoryError, match=f"0/{MIN_TRAINING_ROWS}"):
        _run(run_pipeline(settings=settings))


@respx.mock
def test_run_pipeline_produces_forecast_once_history_is_seeded(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_history(settings.sqlite_path, MIN_TRAINING_ROWS)
    _mock_all_entsoe_no_data(settings)
    _mock_noaa_success(settings.noaa_gfs_base_url)

    result = _run(run_pipeline(settings=settings))
    payload = result.zones["DE-LU"]

    assert payload["zone"] == "DE"
    assert payload["model_version"] == settings.model_version
    assert payload["unit"] == "gCO2eq/kWh"
    forecast = payload["forecast"]
    assert isinstance(forecast, list)
    assert len(forecast) == 120
    first = forecast[0]
    assert set(first.keys()) == {
        "timestamp",
        "value",
        "value_lifecycle",
        "confidence",
        "power_breakdown_percent",
    }
    assert first["confidence"] == "high"
    assert isinstance(first["value"], int)
    assert first["power_breakdown_percent"] is None

    assert (settings.model_dir / "DE-LU" / "direct.txt").exists()


@respx.mock
def test_run_pipeline_forecasts_power_breakdown_once_bootstrapped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    _seed_history(settings.sqlite_path, MIN_TRAINING_ROWS, include_breakdown=True)
    _mock_all_entsoe_no_data(settings)
    _mock_noaa_success(settings.noaa_gfs_base_url)

    result = _run(run_pipeline(settings=settings))
    forecast = result.zones["DE-LU"]["forecast"]

    breakdown = forecast[0]["power_breakdown_percent"]  # type: ignore[index]
    assert breakdown is not None
    # A booster is trained for every tracked category (see
    # oko.emissions.factors.CATEGORIES), not just the ones present in the
    # seeded history -- absent categories simply predict ~0 share.
    assert {"wind", "coal"} <= set(breakdown)
    assert sum(breakdown.values()) == pytest.approx(100.0)
    assert (settings.model_dir / "DE-LU" / "breakdown" / "breakdown_wind.txt").exists()


@respx.mock
def test_run_pipeline_raises_when_noaa_totally_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Every one of the 120 forecast hours will fail and retry 3x with
    # backoff -- skip the real sleeps so this test doesn't take minutes.
    async def _instant_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(asyncio, "sleep", _instant_sleep)

    settings = _settings(tmp_path)
    _seed_history(settings.sqlite_path, MIN_TRAINING_ROWS)
    _mock_all_entsoe_no_data(settings)
    respx.get(url__regex=rf"{re.escape(settings.noaa_gfs_base_url)}/.*\.idx$").mock(
        return_value=httpx.Response(500)
    )

    with pytest.raises(PipelineError, match="NOAA"):
        _run(run_pipeline(settings=settings))


@respx.mock
def test_run_pipeline_tolerates_all_zone_fetch_failures_when_history_already_sufficient(
    tmp_path: Path,
) -> None:
    """A totally offline ENTSO-E this run must not block forecasting once
    enough history already exists from previous runs — only the *new*
    hour's data is skipped, not the whole pipeline."""
    settings = _settings(tmp_path)
    _seed_history(settings.sqlite_path, MIN_TRAINING_ROWS + 24)
    respx.get(settings.entsoe_base_url).mock(return_value=httpx.Response(500))
    _mock_noaa_success(settings.noaa_gfs_base_url)

    result = _run(run_pipeline(settings=settings))
    assert len(result.zones["DE-LU"]["forecast"]) == 120  # type: ignore[arg-type]


def test_history_fetch_window_covers_at_least_two_days() -> None:
    # Sanity: the rolling fetch window must comfortably exceed 24h so a
    # single missed hourly run doesn't leave a permanent gap.
    assert HISTORY_FETCH_WINDOW_HOURS > 24


def test_all_zones_have_a_load_and_production_path() -> None:
    # Basic config sanity relevant to the pipeline's zone fan-out.
    assert "DE-LU" in ALL_ZONES
    assert len(ALL_ZONES) == 10


@respx.mock
def test_run_pipeline_computes_flow_traced_intensity_for_fresh_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Exercise the *real* flow_tracing call inside run_pipeline.

    None of the other pipeline tests reach that branch: they all hit
    either "no data at all" or "every fetch failed" before
    `TARGET_ZONE in production and load_by_hour` is ever true. Here,
    DE-LU and FR both succeed with one fresh hour and one border between
    them, so the freshly-computed history row can be checked against a
    hand-computed expected value (DE-LU imports 1000 MW of FR's clean
    nuclear generation alongside 1000 MW of its own coal).
    """
    settings = _settings(tmp_path)
    # One hour short of the threshold -- the freshly fetched hour below
    # must be exactly what tips it over.
    _seed_history(settings.sqlite_path, MIN_TRAINING_ROWS - 1)

    fresh_hour = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)

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
        return [entsoe.LoadRecord(zone="DE-LU", timestamp=fresh_hour, load_mw=2000.0)]

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
            # Negative DE-LU->FR net flow = FR exports 1000 MW to DE-LU.
            return [
                entsoe.ExchangeRecord(
                    zone_from="DE-LU", zone_to="FR", timestamp=fresh_hour, net_flow_mw=-1000.0
                )
            ]
        raise entsoe.EntsoeError(f"no data for {zone1}-{zone2}")

    monkeypatch.setattr(entsoe, "fetch_production", fake_fetch_production)
    monkeypatch.setattr(entsoe, "fetch_load", fake_fetch_load)
    monkeypatch.setattr(entsoe, "fetch_exchange", fake_fetch_exchange)
    _mock_noaa_success(settings.noaa_gfs_base_url)

    result = _run(run_pipeline(settings=settings))
    assert len(result.zones["DE-LU"]["forecast"]) == 120  # type: ignore[arg-type]

    assert result.exchanges is not None
    assert result.exchanges["exchanges"] == [
        {
            "zone_from": "DE-LU",
            "zone_to": "FR",
            "timestamp": format_iso_z(fresh_hour),
            "net_flow_mw": -1000,
        }
    ]

    rows, targets = load_training_rows(settings.sqlite_path, "DE-LU")
    fresh_indices = [i for i, row in enumerate(rows) if row.timestamp == fresh_hour]
    assert fresh_indices, "the freshly computed hour must have been persisted"

    de_lu_coal_factor = factors_for_zone("DE-LU")["coal"]
    # DE-LU: 1000 MW own coal + 1000 MW imported (0 g/kWh) nuclear from FR.
    expected = (1000.0 * de_lu_coal_factor + 1000.0 * 0.0) / 2000.0
    assert targets[fresh_indices[0]] == pytest.approx(expected)

    # The same fresh hour's power breakdown (100% coal -- DE-LU's own
    # production this hour, not import-corrected) was persisted alongside
    # the intensity target.
    breakdown_rows, breakdowns = load_breakdown_training_rows(settings.sqlite_path, "DE-LU")
    breakdown_index = next(i for i, row in enumerate(breakdown_rows) if row.timestamp == fresh_hour)
    assert breakdowns[breakdown_index] == {"coal": 100.0}
