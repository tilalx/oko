"""NOAA GFS 0.25° fetcher: 10 m wind speed, downward shortwave radiation, 2 m temperature.

Public-domain US government data, no account required. Fetched from NOAA's
public S3 mirror via the NODD (NOAA Open Data Dissemination) program
(``https://noaa-gfs-bdp-pds.s3.amazonaws.com``), which republishes NOMADS'
GFS output with anonymous, unrate-limited HTTPS access. Unlike NOMADS'
filter/subsetting CGI (~120 req/min per IP, previously the source here —
see git history), S3 has no server-side spatial subsetting: fetching one
field means downloading the whole global grid message. Each GFS object has
a sibling ``.idx`` text file listing every field's byte offset within it,
so the field is fetched via an HTTP byte-range GET instead of downloading
the full ~500MB file.

Because there's no per-zone subsetting on the server side, this module
fetches each field **once per forecast hour, globally**, decodes it into a
lat/lon/value grid, and lets every zone slice its own bounding-box mean
locally (see ``extract_zone_series``) — one shared fetch serves every zone
in the network instead of one fetch per zone, and adding more zones later
costs no extra network traffic.

Each forecast hour needs 5 requests (1 idx + 4 field byte-ranges), so
producing OKO's 120-hour (5-day) horizon means up to ~600 requests total
per pipeline run, independent of how many zones are modeled. Concurrency
is bounded and each hour is fetched independently so a handful of
missing/failed hours don't abort the whole forecast — see
``fetch_global_forecast``.
"""

from __future__ import annotations

import asyncio
import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass

import eccodes
import httpx
import numpy as np
import structlog

logger = structlog.get_logger(__name__)

#: GFS model cycles run every 6 hours.
GFS_CYCLE_HOURS: tuple[int, ...] = (0, 6, 12, 18)

#: Conservative publish lag: NOAA typically has a cycle's files available
#: roughly 3h45m after the cycle's nominal time; padded for safety margin.
GFS_PUBLISH_LAG = dt.timedelta(hours=5)

#: GFS 0.25° forecasts are hourly through F120, matching OKO's 5-day horizon.
MAX_FORECAST_HOUR = 120

#: Identifying the client is standard courtesy for automated access to
#: NOAA's open-data services, even though (unlike the old NOMADS filter
#: CGI) S3 has no rate limiting or bot-detection quirks to work around.
REQUEST_HEADERS = {"User-Agent": "oko-forecast-pipeline/0.1"}

#: NOAA's public NODD (Open Data Dissemination) mirror of GFS. Anonymous,
#: unauthenticated, unrate-limited HTTPS access with byte-range support.
DEFAULT_BASE_URL = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"

#: Max concurrent forecast-hour fetches. Unlike the old NOMADS throttle
#: (shared *across* per-zone calls to cap aggregate request rate against a
#: rate-limited endpoint), this is a plain concurrency cap on one shared
#: fetch's hour-level fan-out -- S3 has no rate limit to pace against.
DEFAULT_MAX_CONCURRENCY = 8


class NoaaGfsError(RuntimeError):
    """Raised when a GFS forecast hour can't be retrieved or parsed."""


@dataclass(frozen=True, slots=True)
class WeatherPoint:
    """Zone-averaged weather forecast for one valid hour.

    Attributes:
        valid_time: UTC timestamp this forecast value applies to.
        wind_speed_10m_ms: mean 10 m wind speed over the requested
            bounding box, m/s.
        dswrf_wm2: mean downward shortwave radiation flux over the same
            bounding box, W/m².
        temperature_2m_c: mean 2 m air temperature over the same bounding
            box, °C -- a primary electricity-demand driver (heating/
            cooling load) that wind/solar alone don't capture.
    """

    valid_time: dt.datetime
    wind_speed_10m_ms: float
    dswrf_wm2: float
    temperature_2m_c: float


def latest_available_cycle(now: dt.datetime | None = None) -> dt.datetime:
    """Return the most recent GFS cycle expected to already be published.

    Args:
        now: reference time (UTC); defaults to the current time.

    Returns:
        The cycle's reference datetime, truncated to one of the four daily
        cycle hours (0/6/12/18 UTC) and shifted back by the typical NOAA
        publish lag so the pipeline doesn't request a cycle whose files
        haven't landed yet.
    """
    reference = (now or dt.datetime.now(dt.UTC)) - GFS_PUBLISH_LAG
    cycle_hour = max(h for h in GFS_CYCLE_HOURS if h <= reference.hour)
    return reference.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)


def _object_key(cycle: dt.datetime, forecast_hour: int) -> str:
    """S3 object key for one GFS cycle/forecast-hour (no file extension)."""
    return (
        f"gfs.{cycle:%Y%m%d}/{cycle.hour:02d}/atmos/"
        f"gfs.t{cycle.hour:02d}z.pgrb2.0p25.f{forecast_hour:03d}"
    )


def _object_url(base_url: str, cycle: dt.datetime, forecast_hour: int) -> str:
    return f"{base_url}/{_object_key(cycle, forecast_hour)}"


@dataclass(frozen=True, slots=True)
class _IdxEntry:
    """One line of a GFS ``.idx`` sibling file."""

    message_num: int
    start_byte: int
    varname: str
    level: str


def _parse_idx(text: str) -> list[_IdxEntry]:
    """Parse a ``.idx`` sibling object's text into ordered entries.

    Each line is ``{msg_num}:{byte_offset}:d=...:{VARNAME}:{LEVEL}:{desc}:``.
    The forecast-step description (last field) varies across forecast
    hours (e.g. ``"48 hour fcst"`` vs ``"42-48 hour ave fcst"``) and is
    deliberately not parsed here -- matching is by (varname, level) only.

    Raises:
        NoaaGfsError: a line doesn't split into at least the 5 expected
            colon-separated fields.
    """
    entries = []
    for line in text.strip().splitlines():
        if not line:
            continue
        parts = line.split(":")
        if len(parts) < 5:
            raise NoaaGfsError(f"malformed GFS idx line: {line!r}")
        entries.append(
            _IdxEntry(
                message_num=int(parts[0]),
                start_byte=int(parts[1]),
                varname=parts[3],
                level=parts[4],
            )
        )
    return entries


def _byte_range(entries: list[_IdxEntry], *, varname: str, level: str) -> tuple[int, int | None]:
    """Find one field's byte range in an already-parsed idx.

    Matching is exact-equality on ``(varname, level)`` -- level strings
    like ``"10 mb"`` (a pressure level) and ``"10 m above ground"`` are
    distinct entries for the same varname, and substring matching on
    level (e.g. ``"surface"``) can false-match unrelated longer level
    descriptions.

    Returns:
        ``(start, end)`` -- ``end`` is the message's last byte
        (inclusive), or ``None`` for an open-ended range if the matched
        entry is the last one in the idx.

    Raises:
        NoaaGfsError: no entry matches ``(varname, level)``.
    """
    for i, entry in enumerate(entries):
        if entry.varname == varname and entry.level == level:
            end = entries[i + 1].start_byte - 1 if i + 1 < len(entries) else None
            return entry.start_byte, end
    raise NoaaGfsError(f"GFS idx has no entry for {varname}:{level}")


def _range_header(start: int, end: int | None) -> str:
    return f"bytes={start}-{end}" if end is not None else f"bytes={start}-"


def _decode_message(data: bytes) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Decode one in-memory GRIB2 message into ``(lats, lons, grid)``.

    ``grid`` has shape ``(Nj, Ni)`` -- ``grid[j, i]`` corresponds to
    ``lats[j]``/``lons[i]``. Longitudes are in GFS's native 0..360
    convention; latitudes run north-to-south (matching the native grid
    order, since the message's scan direction isn't otherwise applied
    here -- GFS 0.25° always scans this way).

    Raises:
        NoaaGfsError: ``data`` isn't a valid, decodable GRIB2 message.
    """
    gid = None
    try:
        gid = eccodes.codes_new_from_message(data)
        ni = eccodes.codes_get(gid, "Ni")
        nj = eccodes.codes_get(gid, "Nj")
        lat_first = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
        lat_last = eccodes.codes_get(gid, "latitudeOfLastGridPointInDegrees")
        lon_first = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
        lon_last = eccodes.codes_get(gid, "longitudeOfLastGridPointInDegrees")
        values = np.asarray(eccodes.codes_get_values(gid), dtype=np.float64)
    except eccodes.GribInternalError as exc:
        raise NoaaGfsError(f"failed to decode GRIB2 message: {exc}") from exc
    finally:
        if gid is not None:
            eccodes.codes_release(gid)
    lats = np.linspace(lat_first, lat_last, nj)
    lons = np.linspace(lon_first, lon_last, ni)
    return lats, lons, values.reshape(nj, ni)


def _bbox_mean(
    lats: np.ndarray, lons: np.ndarray, grid: np.ndarray, bbox: Mapping[str, float]
) -> float:
    """Area-weighted spatial mean of ``grid`` restricted to ``bbox`` (signed-degree convention).

    ``bbox`` (``oko.config.ZONE_BBOXES[zone]`` shape) uses signed
    longitudes (e.g. France's ``leftlon: -5.0``); GFS's grid uses 0..360,
    so bbox longitudes are converted via ``% 360.0`` before masking.

    A lat/lon grid cell's physical area shrinks toward the poles
    (proportional to ``cos(latitude)``); a plain ``.mean()`` would treat a
    cell near a bbox's northern edge as equally representative as one
    near the equator-ward edge, which measurably skews zones spanning a
    wide latitude range (e.g. Norway's bboxes). Weighting each row by
    ``cos(lat)`` corrects for that.
    """
    leftlon = bbox["leftlon"] % 360.0
    rightlon = bbox["rightlon"] % 360.0
    lat_mask = (lats >= bbox["bottomlat"]) & (lats <= bbox["toplat"])
    if leftlon <= rightlon:
        lon_mask = (lons >= leftlon) & (lons <= rightlon)
    else:
        # Only reachable for a bbox straddling the 0/360 seam -- none of
        # OKO's current zones do, but handled defensively for a future one.
        lon_mask = (lons >= leftlon) | (lons <= rightlon)
    selected = grid[np.ix_(lat_mask, lon_mask)]
    if selected.size == 0:
        return float("nan")  # matches the previous plain-.mean() behaviour for an empty selection
    row_weights = np.cos(np.radians(lats[lat_mask]))
    weights = np.broadcast_to(row_weights[:, np.newaxis], selected.shape)
    return float(np.average(selected, weights=weights))


#: Kelvin -> Celsius offset for GFS's native TMP field.
KELVIN_TO_CELSIUS = 273.15


@dataclass(frozen=True, slots=True)
class _GlobalHour:
    """One forecast hour's full global grid for every fetched field, fetched once."""

    valid_time: dt.datetime
    lats: np.ndarray
    lons: np.ndarray
    wind_speed_grid: np.ndarray
    dswrf_grid: np.ndarray
    temperature_grid_c: np.ndarray


async def _fetch_hour_grids(
    client: httpx.AsyncClient,
    base_url: str,
    cycle: dt.datetime,
    forecast_hour: int,
    *,
    timeout: float,
    max_retries: int,
) -> _GlobalHour | None:
    """Fetch and decode one forecast hour's global UGRD/VGRD/DSWRF grids.

    Retries the whole hour (idx fetch, all 3 field fetches, and decoding)
    on any failure, with exponential backoff. Returns ``None`` (rather
    than raising) once retries are exhausted, so the caller can skip this
    hour without failing the whole forecast.
    """
    url = _object_url(base_url, cycle, forecast_hour)
    valid_time = cycle + dt.timedelta(hours=forecast_hour)
    for attempt in range(1, max_retries + 1):
        try:
            idx_response = await client.get(f"{url}.idx", timeout=timeout, headers=REQUEST_HEADERS)
            idx_response.raise_for_status()
            entries_snapshot = _parse_idx(idx_response.text)

            async def _fetch_field(
                field_var: str, field_level: str, entries: list[_IdxEntry] = entries_snapshot
            ) -> bytes:
                start, end = _byte_range(entries, varname=field_var, level=field_level)
                response = await client.get(
                    url,
                    timeout=timeout,
                    headers={**REQUEST_HEADERS, "Range": _range_header(start, end)},
                )
                response.raise_for_status()
                if not response.content:
                    raise NoaaGfsError(f"empty response body for {field_var}:{field_level}")
                return response.content

            u_bytes, v_bytes, dswrf_bytes, tmp_bytes = await asyncio.gather(
                _fetch_field("UGRD", "10 m above ground"),
                _fetch_field("VGRD", "10 m above ground"),
                _fetch_field("DSWRF", "surface"),
                _fetch_field("TMP", "2 m above ground"),
            )
            lats, lons, u_grid = _decode_message(u_bytes)
            _, _, v_grid = _decode_message(v_bytes)
            _, _, dswrf_grid = _decode_message(dswrf_bytes)
            _, _, tmp_grid_k = _decode_message(tmp_bytes)
            return _GlobalHour(
                valid_time=valid_time,
                lats=lats,
                lons=lons,
                wind_speed_grid=np.hypot(u_grid, v_grid),
                dswrf_grid=dswrf_grid,
                temperature_grid_c=tmp_grid_k - KELVIN_TO_CELSIUS,
            )
        except (httpx.HTTPError, NoaaGfsError) as exc:
            logger.warning(
                "noaa_gfs.hour_fetch_failed",
                forecast_hour=forecast_hour,
                attempt=attempt,
                error=str(exc),
            )
            if attempt == max_retries:
                return None
            await asyncio.sleep(2**attempt)
    return None


async def fetch_global_forecast(
    *,
    base_url: str,
    cycle: dt.datetime | None = None,
    horizon_hours: int = MAX_FORECAST_HOUR,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    max_retries: int = 3,
    timeout: float = 30.0,
    client: httpx.AsyncClient | None = None,
) -> list[_GlobalHour]:
    """Fetch every forecast hour's full-global UGRD/VGRD/DSWRF grids once.

    Shared across every zone -- callers slice each zone's bbox locally via
    ``extract_zone_series`` instead of re-fetching per zone.

    Args:
        base_url: NOAA S3 bucket base URL (``settings.noaa_gfs_base_url``).
        cycle: GFS model cycle to use; defaults to the latest one expected
            to already be published (see ``latest_available_cycle``).
        horizon_hours: number of forecast hours to fetch, 1..horizon_hours
            (hour 0 is always skipped -- see below).
        max_concurrency: concurrent forecast-hour fetches in flight.
        max_retries: retry attempts per forecast hour before giving up on it.
        timeout: per-request timeout in seconds.
        client: optional shared ``httpx.AsyncClient`` (mainly for tests);
            a temporary one is created and closed if omitted.

    Returns:
        One ``_GlobalHour`` per successfully fetched forecast hour, sorted
        by valid time. Hours that fail after retries are skipped (logged
        as warnings), not raised.

    Raises:
        NoaaGfsError: if not a single forecast hour could be fetched.
    """
    resolved_cycle = cycle or latest_available_cycle()
    owns_client = client is None
    http_client = client or httpx.AsyncClient()
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _bounded(hour: int) -> _GlobalHour | None:
        async with semaphore:
            return await _fetch_hour_grids(
                http_client,
                base_url,
                resolved_cycle,
                hour,
                timeout=timeout,
                max_retries=max_retries,
            )

    # Forecast hour 0 (the analysis step) systematically has no averaged
    # DSWRF field -- there's no preceding interval to average over -- so
    # it's skipped rather than retried for a guaranteed-missing field.
    try:
        results = await asyncio.gather(*(_bounded(h) for h in range(1, horizon_hours + 1)))
    finally:
        if owns_client:
            await http_client.aclose()

    hours = sorted((h for h in results if h is not None), key=lambda h: h.valid_time)
    if not hours:
        raise NoaaGfsError(f"No GFS forecast hours could be fetched for cycle {resolved_cycle}")
    logger.info(
        "noaa_gfs.fetch_complete",
        cycle=resolved_cycle.isoformat(),
        requested_hours=horizon_hours,
        received_hours=len(hours),
    )
    return hours


def extract_zone_series(hours: list[_GlobalHour], bbox: Mapping[str, float]) -> list[WeatherPoint]:
    """Slice one zone's ``WeatherPoint`` series from already-fetched global hours.

    Pure/local -- no network access, just a bbox-restricted spatial mean
    per hour (see ``_bbox_mean``).
    """
    return [
        WeatherPoint(
            valid_time=hour.valid_time,
            wind_speed_10m_ms=_bbox_mean(hour.lats, hour.lons, hour.wind_speed_grid, bbox),
            dswrf_wm2=_bbox_mean(hour.lats, hour.lons, hour.dswrf_grid, bbox),
            temperature_2m_c=_bbox_mean(hour.lats, hour.lons, hour.temperature_grid_c, bbox),
        )
        for hour in hours
    ]


async def fetch_forecast_for_zones(
    *,
    base_url: str,
    bboxes: Mapping[str, Mapping[str, float]],
    cycle: dt.datetime | None = None,
    horizon_hours: int = MAX_FORECAST_HOUR,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    max_retries: int = 3,
    timeout: float = 30.0,
    client: httpx.AsyncClient | None = None,
) -> dict[str, list[WeatherPoint]]:
    """Fetch the shared global forecast once, then slice every zone's bbox from it.

    This is the entry point ``oko.pipeline.run_pipeline`` calls once per
    pipeline run (not once per zone). Raises (does not partially succeed)
    if the global fetch itself fails entirely -- see
    ``fetch_global_forecast``.
    """
    hours = await fetch_global_forecast(
        base_url=base_url,
        cycle=cycle,
        horizon_hours=horizon_hours,
        max_concurrency=max_concurrency,
        max_retries=max_retries,
        timeout=timeout,
        client=client,
    )
    return {zone: extract_zone_series(hours, bbox) for zone, bbox in bboxes.items()}


async def fetch_forecast(
    *,
    base_url: str,
    bbox: Mapping[str, float],
    cycle: dt.datetime | None = None,
    horizon_hours: int = MAX_FORECAST_HOUR,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    max_retries: int = 3,
    timeout: float = 30.0,
    client: httpx.AsyncClient | None = None,
) -> list[WeatherPoint]:
    """Single-zone convenience wrapper around ``fetch_global_forecast``.

    Kept for API simplicity and any single-zone caller (tests, one-off
    scripts) -- ``oko.pipeline`` itself calls ``fetch_forecast_for_zones``
    directly since it needs every zone from one shared fetch.
    """
    hours = await fetch_global_forecast(
        base_url=base_url,
        cycle=cycle,
        horizon_hours=horizon_hours,
        max_concurrency=max_concurrency,
        max_retries=max_retries,
        timeout=timeout,
        client=client,
    )
    return extract_zone_series(hours, bbox)
