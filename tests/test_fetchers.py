"""Fetcher tests: all HTTP interaction is mocked (respx) or a real, captured fixture.

No test in this module makes a live network call — the ENTSO-E XML and
energy-charts.info JSON fixtures were captured from real responses (schema
verified against the live services during development), and the GFS
GRIB2 fixtures are byte-for-byte real NOMADS responses.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Coroutine
from pathlib import Path

import httpx
import numpy as np
import pytest
import respx

from oko.config import ENTSOE_DOMAIN_MAPPINGS, ZONE_BBOXES, Settings
from oko.fetchers import energy_charts, entsoe, noaa_gfs

FIXTURES = Path(__file__).parent / "fixtures"


def _settings() -> Settings:
    return Settings(entsoe_token="dummy-token", _env_file=None)  # type: ignore[call-arg]


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


# --------------------------------------------------------------------------
# ENTSO-E: production
# --------------------------------------------------------------------------


@respx.mock
def test_fetch_production_groups_categories_and_excludes_storage() -> None:
    settings = _settings()
    fixture_xml = (FIXTURES / "entsoe_production_de.xml").read_text()
    respx.get(settings.entsoe_base_url, params={"documentType": "A75"}).mock(
        return_value=httpx.Response(200, text=fixture_xml)
    )

    async def go() -> list[entsoe.ProductionRecord]:
        async with httpx.AsyncClient() as client:
            return await entsoe.fetch_production(
                "DE-LU",
                dt.datetime(2026, 8, 31, 0, tzinfo=dt.UTC),
                dt.datetime(2026, 8, 31, 2, tzinfo=dt.UTC),
                client=client,
                settings=settings,
            )

    records = _run(go())

    assert [r.timestamp for r in records] == [
        dt.datetime(2026, 8, 31, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 8, 31, 1, tzinfo=dt.UTC),
    ]
    hour0, hour1 = records[0].by_category, records[1].by_category
    assert hour0 == {"solar": 0.0, "wind": 12000.0, "coal": 7000.0}
    assert hour1 == {"solar": 0.0, "wind": 12500.0, "coal": 6900.0}
    # B10 (Hydro Pumped Storage) must not surface as a generation category.
    assert "hydro" not in hour0 or hour0.get("hydro") != 500.0


def test_fetch_production_rejects_unknown_zone() -> None:
    settings = _settings()

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            await entsoe.fetch_production(
                "XX",
                dt.datetime(2026, 8, 31, tzinfo=dt.UTC),
                dt.datetime(2026, 8, 31, 1, tzinfo=dt.UTC),
                client=client,
                settings=settings,
            )

    with pytest.raises(entsoe.EntsoeError, match="Unknown zone"):
        _run(go())


@respx.mock
def test_fetch_production_no_data_raises_typed_error() -> None:
    settings = _settings()
    fixture_xml = (FIXTURES / "entsoe_no_data.xml").read_text()
    respx.get(settings.entsoe_base_url).mock(return_value=httpx.Response(200, text=fixture_xml))

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            await entsoe.fetch_production(
                "DE-LU",
                dt.datetime(2026, 8, 31, tzinfo=dt.UTC),
                dt.datetime(2026, 8, 31, 1, tzinfo=dt.UTC),
                client=client,
                settings=settings,
            )

    with pytest.raises(entsoe.EntsoeNoDataError):
        _run(go())


# --------------------------------------------------------------------------
# ENTSO-E: load
# --------------------------------------------------------------------------


@respx.mock
def test_fetch_load_returns_hourly_records() -> None:
    settings = _settings()
    fixture_xml = (FIXTURES / "entsoe_load_de.xml").read_text()
    respx.get(settings.entsoe_base_url, params={"documentType": "A65"}).mock(
        return_value=httpx.Response(200, text=fixture_xml)
    )

    async def go() -> list[entsoe.LoadRecord]:
        async with httpx.AsyncClient() as client:
            return await entsoe.fetch_load(
                "DE-LU",
                dt.datetime(2026, 8, 31, 0, tzinfo=dt.UTC),
                dt.datetime(2026, 8, 31, 2, tzinfo=dt.UTC),
                client=client,
                settings=settings,
            )

    records = _run(go())

    assert records[0].timestamp == dt.datetime(2026, 8, 31, 0, tzinfo=dt.UTC)
    assert records[0].load_mw == pytest.approx(55000.0)
    assert records[1].load_mw == pytest.approx(53500.0)


def test_fetch_load_rejects_unknown_zone() -> None:
    settings = _settings()

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            await entsoe.fetch_load(
                "XX",
                dt.datetime(2026, 8, 31, tzinfo=dt.UTC),
                dt.datetime(2026, 8, 31, 1, tzinfo=dt.UTC),
                client=client,
                settings=settings,
            )

    with pytest.raises(entsoe.EntsoeError, match="Unknown zone"):
        _run(go())


# --------------------------------------------------------------------------
# ENTSO-E: exchange
# --------------------------------------------------------------------------


@respx.mock
def test_fetch_exchange_nets_both_directions() -> None:
    settings = _settings()
    forward_xml = (FIXTURES / "entsoe_exchange_forward.xml").read_text()
    backward_xml = (FIXTURES / "entsoe_exchange_backward.xml").read_text()

    de_domain = ENTSOE_DOMAIN_MAPPINGS["DE-LU"]
    fr_domain = ENTSOE_DOMAIN_MAPPINGS["FR"]
    # zone_from/zone_to = sorted("FR", "DE-LU") = ("DE-LU", "FR").
    # forward = flow INTO FR FROM DE-LU (in=FR, out=DE-LU) = DE-LU -> FR export.
    respx.get(
        settings.entsoe_base_url, params={"in_Domain": fr_domain, "out_Domain": de_domain}
    ).mock(return_value=httpx.Response(200, text=forward_xml))
    # backward = flow INTO DE-LU FROM FR (in=DE-LU, out=FR) = FR -> DE-LU export.
    respx.get(
        settings.entsoe_base_url, params={"in_Domain": de_domain, "out_Domain": fr_domain}
    ).mock(return_value=httpx.Response(200, text=backward_xml))

    async def go() -> list[entsoe.ExchangeRecord]:
        async with httpx.AsyncClient() as client:
            return await entsoe.fetch_exchange(
                "FR",
                "DE-LU",
                dt.datetime(2026, 8, 31, tzinfo=dt.UTC),
                dt.datetime(2026, 8, 31, 2, tzinfo=dt.UTC),
                client=client,
                settings=settings,
            )

    records = _run(go())

    assert records[0].zone_from == "DE-LU"
    assert records[0].zone_to == "FR"
    assert records[0].net_flow_mw == pytest.approx(1500 - 300)
    assert records[1].net_flow_mw == pytest.approx(1400 - 250)


@respx.mock
def test_fetch_exchange_treats_missing_direction_as_zero() -> None:
    settings = _settings()
    forward_xml = (FIXTURES / "entsoe_exchange_forward.xml").read_text()
    no_data_xml = (FIXTURES / "entsoe_no_data.xml").read_text()

    de_domain = ENTSOE_DOMAIN_MAPPINGS["DE-LU"]
    fr_domain = ENTSOE_DOMAIN_MAPPINGS["FR"]
    respx.get(
        settings.entsoe_base_url, params={"in_Domain": fr_domain, "out_Domain": de_domain}
    ).mock(return_value=httpx.Response(200, text=forward_xml))
    respx.get(
        settings.entsoe_base_url, params={"in_Domain": de_domain, "out_Domain": fr_domain}
    ).mock(return_value=httpx.Response(200, text=no_data_xml))

    async def go() -> list[entsoe.ExchangeRecord]:
        async with httpx.AsyncClient() as client:
            return await entsoe.fetch_exchange(
                "DE-LU",
                "FR",
                dt.datetime(2026, 8, 31, tzinfo=dt.UTC),
                dt.datetime(2026, 8, 31, 2, tzinfo=dt.UTC),
                client=client,
                settings=settings,
            )

    records = _run(go())
    assert records[0].net_flow_mw == pytest.approx(1500)  # backward treated as 0


def test_fetch_exchange_rejects_unknown_zone() -> None:
    settings = _settings()

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            await entsoe.fetch_exchange(
                "DE-LU",
                "XX",
                dt.datetime(2026, 8, 31, tzinfo=dt.UTC),
                dt.datetime(2026, 8, 31, 1, tzinfo=dt.UTC),
                client=client,
                settings=settings,
            )

    with pytest.raises(entsoe.EntsoeError, match="Unknown zone"):
        _run(go())


# --------------------------------------------------------------------------
# ENTSO-E: point/period parsing internals
# --------------------------------------------------------------------------


def test_parse_period_points_a01_is_directly_positioned() -> None:
    period = {
        "timeInterval": {"start": "2026-08-31T00:00Z", "end": "2026-08-31T02:00Z"},
        "resolution": "PT60M",
        "Point": [{"position": "1", "quantity": "10"}, {"position": "2", "quantity": "20"}],
    }
    points = entsoe._parse_period_points(period, "A01")
    assert points == [
        (dt.datetime(2026, 8, 31, 0, tzinfo=dt.UTC), 10.0),
        (dt.datetime(2026, 8, 31, 1, tzinfo=dt.UTC), 20.0),
    ]


def test_parse_period_points_a03_forward_fills_between_points() -> None:
    period = {
        "timeInterval": {"start": "2026-08-31T00:00Z", "end": "2026-08-31T03:00Z"},
        "resolution": "PT60M",
        "Point": [{"position": "1", "quantity": "8000"}, {"position": "3", "quantity": "8500"}],
    }
    points = entsoe._parse_period_points(period, "A03")
    assert points == [
        (dt.datetime(2026, 8, 31, 0, tzinfo=dt.UTC), 8000.0),
        (dt.datetime(2026, 8, 31, 1, tzinfo=dt.UTC), 8000.0),
        (dt.datetime(2026, 8, 31, 2, tzinfo=dt.UTC), 8500.0),
    ]


def test_parse_period_points_rejects_unknown_curve_type() -> None:
    period = {
        "timeInterval": {"start": "2026-08-31T00:00Z", "end": "2026-08-31T01:00Z"},
        "resolution": "PT60M",
        "Point": {"position": "1", "quantity": "1"},
    }
    with pytest.raises(entsoe.EntsoeError, match="curveType"):
        entsoe._parse_period_points(period, "A99")


@pytest.mark.parametrize(
    ("resolution", "expected"),
    [
        ("PT15M", dt.timedelta(minutes=15)),
        ("PT60M", dt.timedelta(hours=1)),
        ("P1D", dt.timedelta(days=1)),
    ],
)
def test_resolution_to_timedelta(resolution: str, expected: dt.timedelta) -> None:
    assert entsoe._resolution_to_timedelta(resolution) == expected


def test_resolution_to_timedelta_rejects_unknown_format() -> None:
    with pytest.raises(entsoe.EntsoeError):
        entsoe._resolution_to_timedelta("bogus")


# --------------------------------------------------------------------------
# NOAA GFS
# --------------------------------------------------------------------------

#: NOAA's real GFS idx sibling files describe the surface/10m fields OKO
#: needs with these exact (varname, level) pairs -- see noaa_gfs._byte_range.
_UGRD = ("UGRD", "10 m above ground")
_VGRD = ("VGRD", "10 m above ground")
_DSWRF = ("DSWRF", "surface")


def _split_grib_messages(data: bytes) -> dict[str, bytes]:
    """Split a multi-message GRIB2 blob into ``{shortName: message_bytes}``.

    Each GRIB2 message reports its own exact byte length (``totalLength``),
    so a real multi-field fixture can be split into standalone messages
    without needing separate single-field binary fixtures.
    """
    import eccodes as _eccodes

    offset = 0
    out: dict[str, bytes] = {}
    while offset < len(data):
        gid = _eccodes.codes_new_from_message(data[offset:])
        try:
            length = _eccodes.codes_get(gid, "totalLength")
            name = _eccodes.codes_get(gid, "shortName")
        finally:
            _eccodes.codes_release(gid)
        out[name] = data[offset : offset + length]
        offset += length
    return out


def _build_synthetic_object(msgs: dict[str, bytes]) -> tuple[bytes, str]:
    """Concatenate messages into one blob + a matching ``.idx`` text."""
    order = [("10u", *_UGRD), ("10v", *_VGRD), ("sdswrf", *_DSWRF)]
    blob = b""
    idx_lines = []
    for i, (short_name, varname, level) in enumerate(order, start=1):
        chunk = msgs[short_name]
        idx_lines.append(f"{i}:{len(blob)}:d=2026090100:{varname}:{level}:1 hour fcst:")
        blob += chunk
    return blob, "\n".join(idx_lines) + "\n"


def _mock_object(base_url: str, key: str, blob: bytes, idx_text: str) -> None:
    """Register respx routes for one GFS object's idx GET + ranged GETs."""
    url = f"{base_url}/{key}"
    respx.get(f"{url}.idx").mock(return_value=httpx.Response(200, text=idx_text))

    def _range_side_effect(request: httpx.Request) -> httpx.Response:
        range_header = request.headers["Range"]
        start_s, _, end_s = range_header.removeprefix("bytes=").partition("-")
        start = int(start_s)
        end = int(end_s) if end_s else None
        content = blob[start : end + 1] if end is not None else blob[start:]
        return httpx.Response(206, content=content)

    respx.get(url).mock(side_effect=_range_side_effect)


def test_latest_available_cycle_steps_back_to_last_published_cycle() -> None:
    # 14:00 UTC minus the 5h publish lag lands at 09:00 -> most recent
    # cycle hour <= 9 is 06:00.
    now = dt.datetime(2026, 9, 1, 14, 0, tzinfo=dt.UTC)
    cycle = noaa_gfs.latest_available_cycle(now)
    assert cycle == dt.datetime(2026, 9, 1, 6, 0, tzinfo=dt.UTC)


def test_object_key_and_url_shape() -> None:
    cycle = dt.datetime(2026, 9, 1, 0, tzinfo=dt.UTC)
    key = noaa_gfs._object_key(cycle, 3)
    assert key == "gfs.20260901/00/atmos/gfs.t00z.pgrb2.0p25.f003"
    assert noaa_gfs._object_url("https://example.com", cycle, 3) == f"https://example.com/{key}"


def test_parse_idx_and_byte_range() -> None:
    idx_text = (
        "1:0:d=2026090100:UGRD:10 mb:1 hour fcst:\n"
        "2:1000:d=2026090100:UGRD:10 m above ground:1 hour fcst:\n"
        "3:2000:d=2026090100:VGRD:10 m above ground:1 hour fcst:\n"
        "4:3000:d=2026090100:DSWRF:surface:0-1 hour ave fcst:\n"
    )
    entries = noaa_gfs._parse_idx(idx_text)
    assert len(entries) == 4

    # "10 mb" (a pressure level) must not match a "10 m above ground" lookup.
    start, end = noaa_gfs._byte_range(entries, varname="UGRD", level="10 m above ground")
    assert (start, end) == (1000, 1999)

    # Last entry in the idx gets an open-ended range.
    start, end = noaa_gfs._byte_range(entries, varname="DSWRF", level="surface")
    assert (start, end) == (3000, None)

    with pytest.raises(noaa_gfs.NoaaGfsError):
        noaa_gfs._byte_range(entries, varname="TMP", level="surface")


def test_parse_idx_raises_on_malformed_line() -> None:
    with pytest.raises(noaa_gfs.NoaaGfsError):
        noaa_gfs._parse_idx("not:enough:fields\n")


def test_decode_message_reads_real_fixture_message() -> None:
    msgs = _split_grib_messages((FIXTURES / "gfs_sample.grib2").read_bytes())
    lats, lons, grid = noaa_gfs._decode_message(msgs["10u"])
    assert grid.shape == (37, 45)
    assert lats.min() == pytest.approx(47.0)
    assert lats.max() == pytest.approx(56.0)
    assert lons.min() == pytest.approx(5.0)
    assert lons.max() == pytest.approx(16.0)


def test_decode_message_raises_on_garbage_input() -> None:
    with pytest.raises(noaa_gfs.NoaaGfsError):
        noaa_gfs._decode_message(b"not a grib file")
    with pytest.raises(noaa_gfs.NoaaGfsError):
        noaa_gfs._decode_message(b"")


def test_bbox_mean_matches_hand_computed_value() -> None:
    lats = np.array([10.0, 20.0])
    lons = np.array([355.0, 5.0])  # native GFS 0..360 grid longitudes
    grid = np.array([[1.0, 3.0], [2.0, 4.0]])  # grid[j, i]: rows=lats, cols=lons

    # bbox given in OKO's signed-degree convention (like ZONE_BBOXES):
    # leftlon=-10 -> 350.0, rightlon=-2 -> 358.0 after %360 -- a normal
    # (non-wrapping) range in 0..360 that should match only lon index 0
    # (355.0), exercising the negative-longitude conversion explicitly.
    bbox = {"leftlon": -10.0, "rightlon": -2.0, "bottomlat": 0.0, "toplat": 30.0}
    assert noaa_gfs._bbox_mean(lats, lons, grid, bbox) == pytest.approx((1.0 + 2.0) / 2)


def test_extract_zone_series_slices_shared_hours_locally() -> None:
    lats = np.array([47.0, 56.0])
    lons = np.array([5.0, 16.0])
    hours = [
        noaa_gfs._GlobalHour(
            valid_time=dt.datetime(2026, 9, 1, 1, tzinfo=dt.UTC),
            lats=lats,
            lons=lons,
            wind_speed_grid=np.array([[1.0, 2.0], [3.0, 4.0]]),
            dswrf_grid=np.array([[10.0, 20.0], [30.0, 40.0]]),
        )
    ]
    points = noaa_gfs.extract_zone_series(hours, ZONE_BBOXES["DE-LU"])
    assert len(points) == 1
    assert points[0].valid_time == hours[0].valid_time
    assert points[0].wind_speed_10m_ms == pytest.approx(np.mean([1.0, 2.0, 3.0, 4.0]))
    assert points[0].dswrf_wm2 == pytest.approx(np.mean([10.0, 20.0, 30.0, 40.0]))


@respx.mock
def test_fetch_forecast_skips_hour_zero_and_tolerates_one_bad_hour() -> None:
    base_url = "https://example-noaa-s3.test"
    cycle = dt.datetime(2026, 9, 1, 0, tzinfo=dt.UTC)
    msgs = _split_grib_messages((FIXTURES / "gfs_sample.grib2").read_bytes())
    blob, idx_text = _build_synthetic_object(msgs)

    key1 = noaa_gfs._object_key(cycle, 1)
    _mock_object(base_url, key1, blob, idx_text)
    key2 = noaa_gfs._object_key(cycle, 2)
    respx.get(f"{base_url}/{key2}.idx").mock(return_value=httpx.Response(500))

    async def go() -> list[noaa_gfs.WeatherPoint]:
        return await noaa_gfs.fetch_forecast(
            base_url=base_url,
            bbox=ZONE_BBOXES["DE-LU"],
            cycle=cycle,
            horizon_hours=2,
            max_retries=1,
        )

    points = _run(go())

    # Hour 0 is never requested at all; hour 2 fails and is skipped, but
    # doesn't take hour 1 down with it.
    assert len(points) == 1
    assert points[0].valid_time == dt.datetime(2026, 9, 1, 1, tzinfo=dt.UTC)


@respx.mock
def test_fetch_forecast_raises_if_every_hour_fails() -> None:
    base_url = "https://example-noaa-s3.test"
    respx.get(url__regex=r".*\.idx$").mock(return_value=httpx.Response(500))

    async def go() -> list[noaa_gfs.WeatherPoint]:
        return await noaa_gfs.fetch_forecast(
            base_url=base_url,
            bbox=ZONE_BBOXES["DE-LU"],
            cycle=dt.datetime(2026, 9, 1, tzinfo=dt.UTC),
            horizon_hours=1,
            max_retries=1,
        )

    with pytest.raises(noaa_gfs.NoaaGfsError):
        _run(go())


@respx.mock
def test_fetch_forecast_for_zones_shares_one_global_fetch() -> None:
    """One shared fetch must serve every zone's bbox -- not one fetch per zone."""
    base_url = "https://example-noaa-s3.test"
    cycle = dt.datetime(2026, 9, 1, 0, tzinfo=dt.UTC)
    msgs = _split_grib_messages((FIXTURES / "gfs_sample.grib2").read_bytes())
    blob, idx_text = _build_synthetic_object(msgs)
    key = noaa_gfs._object_key(cycle, 1)
    idx_route = respx.get(f"{base_url}/{key}.idx").mock(
        return_value=httpx.Response(200, text=idx_text)
    )

    def _range_side_effect(request: httpx.Request) -> httpx.Response:
        range_header = request.headers["Range"]
        start_s, _, end_s = range_header.removeprefix("bytes=").partition("-")
        start = int(start_s)
        end = int(end_s) if end_s else None
        content = blob[start : end + 1] if end is not None else blob[start:]
        return httpx.Response(206, content=content)

    respx.get(f"{base_url}/{key}").mock(side_effect=_range_side_effect)

    async def go() -> dict[str, list[noaa_gfs.WeatherPoint]]:
        return await noaa_gfs.fetch_forecast_for_zones(
            base_url=base_url,
            bboxes={"DE-LU": ZONE_BBOXES["DE-LU"], "FR": ZONE_BBOXES["FR"]},
            cycle=cycle,
            horizon_hours=1,
            max_retries=1,
        )

    result = _run(go())

    assert set(result) == {"DE-LU", "FR"}
    assert len(result["DE-LU"]) == 1
    assert len(result["FR"]) == 1
    # One shared fetch -> exactly one idx GET, regardless of zone count.
    assert idx_route.call_count == 1


# --------------------------------------------------------------------------
# energy-charts.info
# --------------------------------------------------------------------------


@respx.mock
def test_fetch_co2eq_reference_resamples_hourly_and_skips_null() -> None:
    base_url = "https://api.energy-charts.info"
    fixture_json = (FIXTURES / "energy_charts_co2eq.json").read_text()
    respx.get(f"{base_url}/co2eq").mock(
        return_value=httpx.Response(
            200, content=fixture_json, headers={"content-type": "application/json"}
        )
    )

    async def go() -> list[energy_charts.ReferencePoint]:
        async with httpx.AsyncClient() as client:
            return await energy_charts.fetch_co2eq_reference(
                start=dt.date(2026, 8, 29),
                end=dt.date(2026, 8, 30),
                base_url=base_url,
                client=client,
            )

    points = _run(go())

    assert len(points) == 2
    assert points[0].timestamp == dt.datetime(2026, 8, 29, 22, tzinfo=dt.UTC)
    assert points[0].co2eq_g_per_kwh == pytest.approx((273.6 + 271.2 + 265.7 + 254.6) / 4)
    assert points[1].timestamp == dt.datetime(2026, 8, 29, 23, tzinfo=dt.UTC)
    assert points[1].co2eq_g_per_kwh == pytest.approx((250.2 + 248.9 + 245.1) / 3)


@respx.mock
def test_fetch_co2eq_reference_raises_on_http_error() -> None:
    base_url = "https://api.energy-charts.info"
    respx.get(f"{base_url}/co2eq").mock(return_value=httpx.Response(503))

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            await energy_charts.fetch_co2eq_reference(
                start=dt.date(2026, 8, 29),
                end=dt.date(2026, 8, 30),
                base_url=base_url,
                client=client,
            )

    with pytest.raises(energy_charts.EnergyChartsError):
        _run(go())


@respx.mock
def test_fetch_co2eq_reference_raises_on_malformed_payload() -> None:
    base_url = "https://api.energy-charts.info"
    respx.get(f"{base_url}/co2eq").mock(
        return_value=httpx.Response(200, json={"unexpected": "shape"})
    )

    async def go() -> None:
        async with httpx.AsyncClient() as client:
            await energy_charts.fetch_co2eq_reference(
                start=dt.date(2026, 8, 29),
                end=dt.date(2026, 8, 30),
                base_url=base_url,
                client=client,
            )

    with pytest.raises(energy_charts.EnergyChartsError):
        _run(go())
