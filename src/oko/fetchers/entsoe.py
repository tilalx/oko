"""ENTSO-E Transparency Platform fetcher: production mix and cross-border flows.

Adapted from electricitymaps-contrib's ``parsers/ENTSOE.py`` (AGPLv3 — see
``ATTRIBUTION.md``): the query parameter names/values, the ENTSO-E EIC
domain-code mapping (``oko.config.ENTSOE_DOMAIN_MAPPINGS``), the PSR
production-type code table and its grouping into generation categories, and
the curve-type (A01 / A03 run-length compression) point-expansion algorithm
are reused from there, translated into this module's own data model. The
HTTP client, retry handling, XML parsing via ``xmltodict``, and the
cross-border net-flow computation are OKO-original, written against the
official ENTSO-E REST endpoint (``https://web-api.tp.entsoe.eu/api``)
rather than electricitymaps' internal proxy.

Document types used:

- ``A75``/``A16`` ("Actual generation per type", realised) for production.
- ``A11`` ("Aggregated energy data report") for cross-border physical
  flows, queried in both directions per border and netted.
- ``A65``/``A16`` ("System total load", realised) for total load.
- ``A44`` ("Price Document") for day-ahead auction prices.
- ``A68``/``A33`` ("Installed generation capacity aggregated", year-ahead)
  for installed capacity per category, refreshed infrequently (yearly
  publication cadence) -- see ``fetch_installed_capacity``.

Storage technologies (hydro pumped storage / battery, PSR codes B10/B25)
are excluded from the production mix entirely rather than tracking their
charge/discharge sign — a deliberate MVP simplification (storage is a small
share of DE-LU generation and correctly modelling charge vs. discharge
would need scope beyond the "no full flow-tracing" MVP boundary).
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Any

import httpx
import structlog
import xmltodict

from oko.config import ENTSOE_DOMAIN_MAPPINGS, Settings

logger = structlog.get_logger(__name__)

#: ENTSO-E PSR production-type codes grouped into OKO generation categories.
#: Source: electricitymaps-contrib parsers/ENTSOE.py `ENTSOE_PARAMETER_GROUPS`
#: (AGPLv3). B10 (Hydro Pumped Storage) and B25 (Energy Storage) are handled
#: as storage, not generation — see module docstring.
PSR_TYPE_TO_CATEGORY: dict[str, str] = {
    "B01": "biomass",
    "B02": "coal",  # Fossil Brown coal/Lignite
    "B03": "gas",  # Fossil Coal-derived gas
    "B04": "gas",  # Fossil Gas
    "B05": "coal",  # Fossil Hard coal
    "B06": "oil",  # Fossil Oil
    "B07": "coal",  # Fossil Oil shale
    "B08": "coal",  # Fossil Peat
    "B09": "geothermal",
    "B11": "hydro",  # Hydro Run-of-river and poundage
    "B12": "hydro",  # Hydro Water Reservoir
    "B13": "unknown",  # Marine
    "B14": "nuclear",
    "B15": "unknown",  # Other renewable
    "B16": "solar",
    "B17": "waste",  # Municipal/industrial waste incineration -- not biogenic-neutral
    # like B01 biomass, so kept as its own category with its own (non-zero)
    # emission factor rather than folded into "biomass" (see emissions/factors.py).
    "B18": "wind",  # Wind Offshore
    "B19": "wind",  # Wind Onshore
    "B20": "unknown",  # Other
}

#: PSR codes representing storage rather than generation; excluded from the
#: production mix (see module docstring).
STORAGE_PSR_TYPES = frozenset({"B10", "B25"})

_RESOLUTION_RE = re.compile(r"PT(\d+)([MH])")
_RESOLUTION_DAY_RE = re.compile(r"P(\d+)D")


class EntsoeError(RuntimeError):
    """Raised when the ENTSO-E API request fails or returns malformed XML."""


class EntsoeNoDataError(EntsoeError):
    """Raised when ENTSO-E explicitly reports 'no matching data found'.

    Distinguished from other errors so callers (the pipeline's per-zone
    fault isolation) can treat "no data for this period" differently from
    a genuine outage or malformed query.
    """


@dataclass(frozen=True, slots=True)
class ProductionRecord:
    """Hourly production mix for one zone.

    Attributes:
        zone: OKO zone key (e.g. ``"DE-LU"``).
        timestamp: start of the hour, UTC.
        by_category: generation category -> power in MW. Categories with
            no reported generation in this hour are simply absent.
    """

    zone: str
    timestamp: dt.datetime
    by_category: dict[str, float]


@dataclass(frozen=True, slots=True)
class ExchangeRecord:
    """Hourly net cross-border physical flow between two zones.

    Attributes:
        zone_from: the alphabetically-first zone of the pair.
        zone_to: the alphabetically-second zone of the pair.
        timestamp: start of the hour, UTC.
        net_flow_mw: positive means net flow from ``zone_from`` to
            ``zone_to`` (i.e. ``zone_from`` is a net exporter to
            ``zone_to`` in this hour); negative means the reverse.
    """

    zone_from: str
    zone_to: str
    timestamp: dt.datetime
    net_flow_mw: float


def _resolution_to_timedelta(resolution: str) -> dt.timedelta:
    """Convert an ENTSO-E ISO-8601-ish resolution string to a timedelta.

    Args:
        resolution: e.g. ``"PT15M"``, ``"PT60M"``, ``"P1D"``.

    Returns:
        The equivalent ``datetime.timedelta``.

    Raises:
        EntsoeError: if the resolution string isn't one of the forms
            ENTSO-E is documented to emit.
    """
    if match := _RESOLUTION_RE.fullmatch(resolution):
        digits, scale = match.groups()
        return (
            dt.timedelta(minutes=int(digits)) if scale == "M" else dt.timedelta(hours=int(digits))
        )
    if match := _RESOLUTION_DAY_RE.fullmatch(resolution):
        return dt.timedelta(days=int(match.group(1)))
    raise EntsoeError(f"Unrecognised ENTSO-E resolution: {resolution!r}")


def _as_list(value: Any) -> list[Any]:
    """Normalise an xmltodict node that may be a dict, list, or None to a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _parse_period_points(
    period: dict[str, Any], curve_type: str, value_field: str = "quantity"
) -> list[tuple[dt.datetime, float]]:
    """Expand one ``<Period>`` element into ``(timestamp, value)`` pairs.

    Handles both ENTSO-E curve types: ``A01`` (one value per point,
    directly positioned) and ``A03`` (run-length compressed — a point's
    value holds until the next point, forward-filled to the period end).

    Args:
        period: the xmltodict-parsed ``<Period>`` element.
        curve_type: the parent TimeSeries's ``<curveType>`` value.
        value_field: the ``<Point>`` child element holding the value
            (``"quantity"`` for generation/load/flow documents,
            ``"price.amount"`` for the day-ahead price document).

    Returns:
        A list of ``(timestamp, value)`` tuples, one per resolution step
        covered by the period.

    Raises:
        EntsoeError: for a curve type other than A01/A03.
    """
    start = dt.datetime.fromisoformat(period["timeInterval"]["start"].replace("Z", "+00:00"))
    resolution = _resolution_to_timedelta(str(period["resolution"]))
    points = sorted(
        ((int(p["position"]), float(p[value_field])) for p in _as_list(period.get("Point"))),
        key=lambda pv: pv[0],
    )
    if not points:
        return []

    if curve_type == "A01":
        return [(start + (position - 1) * resolution, value) for position, value in points]

    if curve_type == "A03":
        end = dt.datetime.fromisoformat(period["timeInterval"]["end"].replace("Z", "+00:00"))
        expected_positions = int((end - start) / resolution)
        expanded: list[tuple[dt.datetime, float]] = []
        for (pos, value), (next_pos, _) in pairwise(points):
            expanded.extend((start + (p - 1) * resolution, value) for p in range(pos, next_pos))
        last_pos, last_value = points[-1]
        expanded.extend(
            (start + (p - 1) * resolution, last_value)
            for p in range(last_pos, expected_positions + 1)
        )
        return expanded

    raise EntsoeError(f"Unsupported ENTSO-E curveType: {curve_type!r}")


async def _request_entsoe(
    client: httpx.AsyncClient,
    settings: Settings,
    params: dict[str, str],
) -> dict[str, Any]:
    """Perform one ENTSO-E API request and parse the XML response.

    Args:
        client: shared HTTP client.
        settings: application settings (token, base URL, timeout).
        params: query parameters, excluding ``securityToken``.

    Returns:
        The XML response parsed into a nested dict via ``xmltodict``.

    Raises:
        EntsoeNoDataError: if ENTSO-E reports no matching data for the query.
        EntsoeError: for any other request failure or unparsable response.
    """
    full_params = {**params, "securityToken": settings.entsoe_token}
    try:
        response = await client.get(
            settings.entsoe_base_url, params=full_params, timeout=settings.http_timeout_seconds
        )
    except httpx.HTTPError as exc:
        raise EntsoeError(f"ENTSO-E request failed: {exc}") from exc

    try:
        parsed = xmltodict.parse(response.text)
    except Exception as exc:  # xmltodict raises plain ExpatError on bad XML
        raise EntsoeError(
            f"ENTSO-E returned unparsable XML (status {response.status_code}): {exc}"
        ) from exc

    if "Acknowledgement_MarketDocument" in parsed:
        reason = parsed["Acknowledgement_MarketDocument"].get("Reason", {})
        reason_text = (
            reason.get("text", "unknown reason") if isinstance(reason, dict) else str(reason)
        )
        if "no matching data" in reason_text.lower():
            raise EntsoeNoDataError(f"No ENTSO-E data for query {params}: {reason_text}")
        raise EntsoeError(f"ENTSO-E rejected query {params}: {reason_text}")

    if not response.is_success:
        raise EntsoeError(f"ENTSO-E returned HTTP {response.status_code} for query {params}")

    return parsed


def _period_span(start: dt.datetime, end: dt.datetime) -> dict[str, str]:
    return {
        "periodStart": start.strftime("%Y%m%d%H%M"),
        "periodEnd": end.strftime("%Y%m%d%H%M"),
    }


async def fetch_production(
    zone: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> list[ProductionRecord]:
    """Fetch realised hourly production per generation category for a zone.

    Args:
        zone: OKO zone key, must be a key of ``ENTSOE_DOMAIN_MAPPINGS``.
        start: start of the query window (UTC).
        end: end of the query window (UTC), exclusive.
        client: shared HTTP client.
        settings: application settings (token, base URL, timeout).

    Returns:
        One ``ProductionRecord`` per hour in ``[start, end)`` for which
        ENTSO-E reported at least one generation category.

    Raises:
        EntsoeError: if the zone is unknown, the request fails, or the
            response can't be parsed. Callers are responsible for
            catching this per-zone so a single zone's failure doesn't
            abort the whole pipeline run.
    """
    if zone not in ENTSOE_DOMAIN_MAPPINGS:
        raise EntsoeError(f"Unknown zone for ENTSO-E production query: {zone!r}")

    params = {
        "documentType": "A75",
        "processType": "A16",
        "in_Domain": ENTSOE_DOMAIN_MAPPINGS[zone],
        **_period_span(start, end),
    }
    parsed = await _request_entsoe(client, settings, params)
    document = parsed.get("GL_MarketDocument", {})

    by_timestamp: dict[dt.datetime, dict[str, float]] = {}
    for timeseries in _as_list(document.get("TimeSeries")):
        psr_type = timeseries.get("MktPSRType", {}).get("psrType")
        if psr_type is None or psr_type in STORAGE_PSR_TYPES:
            continue
        category = PSR_TYPE_TO_CATEGORY.get(psr_type, "unknown")
        curve_type = str(timeseries.get("curveType", "A01"))
        for period in _as_list(timeseries.get("Period")):
            for timestamp, value in _parse_period_points(period, curve_type):
                if start <= timestamp < end:
                    by_timestamp.setdefault(timestamp, {})
                    by_timestamp[timestamp][category] = (
                        by_timestamp[timestamp].get(category, 0.0) + value
                    )

    records = [
        ProductionRecord(zone=zone, timestamp=ts, by_category=categories)
        for ts, categories in sorted(by_timestamp.items())
    ]
    logger.info("entsoe.production_fetched", zone=zone, hours=len(records))
    return records


@dataclass(frozen=True, slots=True)
class LoadRecord:
    """Hourly realised (actual, not forecast) total load for one zone.

    Attributes:
        zone: OKO zone key.
        timestamp: start of the hour, UTC.
        load_mw: total system load in MW.
    """

    zone: str
    timestamp: dt.datetime
    load_mw: float


async def fetch_load(
    zone: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> list[LoadRecord]:
    """Fetch realised (actual) hourly total system load for a zone.

    Used for historical model training only — ENTSO-E's own load
    *forecast* only reaches ~24-48h ahead (day-ahead market horizon),
    which can't cover OKO's 120-hour forecast; see
    ``oko.forecast.features`` for how the training/inference gap is
    bridged with NOAA GFS weather data at inference time.

    Args:
        zone: OKO zone key, must be a key of ``ENTSOE_DOMAIN_MAPPINGS``.
        start: start of the query window (UTC).
        end: end of the query window (UTC), exclusive.
        client: shared HTTP client.
        settings: application settings (token, base URL, timeout).

    Returns:
        One ``LoadRecord`` per hour in ``[start, end)``.

    Raises:
        EntsoeError: if the zone is unknown, the request fails, or the
            response can't be parsed.
    """
    if zone not in ENTSOE_DOMAIN_MAPPINGS:
        raise EntsoeError(f"Unknown zone for ENTSO-E load query: {zone!r}")

    params = {
        "documentType": "A65",
        "processType": "A16",
        "outBiddingZone_Domain": ENTSOE_DOMAIN_MAPPINGS[zone],
        **_period_span(start, end),
    }
    parsed = await _request_entsoe(client, settings, params)
    document = parsed.get("GL_MarketDocument", {})

    by_timestamp: dict[dt.datetime, float] = {}
    for timeseries in _as_list(document.get("TimeSeries")):
        curve_type = str(timeseries.get("curveType", "A01"))
        for period in _as_list(timeseries.get("Period")):
            for timestamp, value in _parse_period_points(period, curve_type):
                if start <= timestamp < end:
                    by_timestamp[timestamp] = value

    records = [
        LoadRecord(zone=zone, timestamp=ts, load_mw=value)
        for ts, value in sorted(by_timestamp.items())
    ]
    logger.info("entsoe.load_fetched", zone=zone, hours=len(records))
    return records


async def _fetch_directional_flow(
    domain_in: str,
    domain_out: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> dict[dt.datetime, float]:
    """Fetch one direction of a cross-border physical flow (A11) query."""
    params = {
        "documentType": "A11",
        "in_Domain": domain_in,
        "out_Domain": domain_out,
        **_period_span(start, end),
    }
    try:
        parsed = await _request_entsoe(client, settings, params)
    except EntsoeNoDataError:
        return {}
    document = parsed.get("Publication_MarketDocument", {})

    values: dict[dt.datetime, float] = {}
    for timeseries in _as_list(document.get("TimeSeries")):
        curve_type = str(timeseries.get("curveType", "A01"))
        for period in _as_list(timeseries.get("Period")):
            for timestamp, value in _parse_period_points(period, curve_type):
                if start <= timestamp < end:
                    values[timestamp] = values.get(timestamp, 0.0) + value
    return values


async def fetch_exchange(
    zone1: str,
    zone2: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> list[ExchangeRecord]:
    """Fetch the net hourly cross-border physical flow between two zones.

    Queries ENTSO-E's A11 physical-flow report in both directions and
    nets them: ``net = flow(zone1 -> zone2) - flow(zone2 -> zone1)``. Zones
    are internally sorted alphabetically so a given border is always
    reported with a consistent, order-independent sign convention.

    Args:
        zone1: first OKO zone key.
        zone2: second OKO zone key.
        start: start of the query window (UTC).
        end: end of the query window (UTC), exclusive.
        client: shared HTTP client.
        settings: application settings (token, base URL, timeout).

    Returns:
        One ``ExchangeRecord`` per hour for which at least one direction
        reported data. Missing directions are treated as zero flow for
        that hour rather than failing the whole border.

    Raises:
        EntsoeError: if either zone is unknown or both directional
            queries fail outright.
    """
    for zone in (zone1, zone2):
        if zone not in ENTSOE_DOMAIN_MAPPINGS:
            raise EntsoeError(f"Unknown zone for ENTSO-E exchange query: {zone!r}")

    zone_from, zone_to = sorted((zone1, zone2))
    domain_from = ENTSOE_DOMAIN_MAPPINGS[zone_from]
    domain_to = ENTSOE_DOMAIN_MAPPINGS[zone_to]

    forward, backward = await asyncio.gather(
        _fetch_directional_flow(
            domain_to, domain_from, start, end, client=client, settings=settings
        ),
        _fetch_directional_flow(
            domain_from, domain_to, start, end, client=client, settings=settings
        ),
    )
    if not forward and not backward:
        raise EntsoeError(f"No exchange data in either direction for {zone_from}<->{zone_to}")

    timestamps = sorted(set(forward) | set(backward))
    records = [
        ExchangeRecord(
            zone_from=zone_from,
            zone_to=zone_to,
            timestamp=ts,
            net_flow_mw=forward.get(ts, 0.0) - backward.get(ts, 0.0),
        )
        for ts in timestamps
    ]
    logger.info("entsoe.exchange_fetched", border=f"{zone_from}-{zone_to}", hours=len(records))
    return records


@dataclass(frozen=True, slots=True)
class PriceRecord:
    """Hourly day-ahead auction price for one zone.

    Attributes:
        zone: OKO zone key.
        timestamp: start of the hour, UTC.
        price_eur_per_mwh: day-ahead price in EUR/MWh. Can be negative.
    """

    zone: str
    timestamp: dt.datetime
    price_eur_per_mwh: float


async def fetch_day_ahead_prices(
    zone: str,
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> list[PriceRecord]:
    """Fetch hourly day-ahead auction prices for a zone.

    Args:
        zone: OKO zone key, must be a key of ``ENTSOE_DOMAIN_MAPPINGS``.
        start: start of the query window (UTC).
        end: end of the query window (UTC), exclusive.
        client: shared HTTP client.
        settings: application settings (token, base URL, timeout).

    Returns:
        One ``PriceRecord`` per hour in ``[start, end)`` for which
        ENTSO-E published a day-ahead price. Not every OKO zone clears
        its own day-ahead auction (some sub-zones price off a
        neighbour's), in which case ENTSO-E reports no data.

    Raises:
        EntsoeNoDataError: if the zone has no day-ahead price data for
            this window.
        EntsoeError: if the zone is unknown, the request fails, or the
            response can't be parsed. Callers are responsible for
            catching this per-zone so a single zone's failure doesn't
            abort the whole pipeline run.
    """
    if zone not in ENTSOE_DOMAIN_MAPPINGS:
        raise EntsoeError(f"Unknown zone for ENTSO-E price query: {zone!r}")

    domain = ENTSOE_DOMAIN_MAPPINGS[zone]
    params = {
        "documentType": "A44",
        "in_Domain": domain,
        "out_Domain": domain,
        **_period_span(start, end),
    }
    parsed = await _request_entsoe(client, settings, params)
    document = parsed.get("Publication_MarketDocument", {})

    by_timestamp: dict[dt.datetime, float] = {}
    for timeseries in _as_list(document.get("TimeSeries")):
        curve_type = str(timeseries.get("curveType", "A01"))
        for period in _as_list(timeseries.get("Period")):
            for timestamp, value in _parse_period_points(
                period, curve_type, value_field="price.amount"
            ):
                if start <= timestamp < end:
                    by_timestamp[timestamp] = value

    records = [
        PriceRecord(zone=zone, timestamp=ts, price_eur_per_mwh=value)
        for ts, value in sorted(by_timestamp.items())
    ]
    logger.info("entsoe.prices_fetched", zone=zone, hours=len(records))
    return records


async def fetch_installed_capacity(
    zone: str,
    year: int,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> dict[str, float]:
    """Fetch installed generation capacity per category for a zone/year.

    ENTSO-E's ``A68`` ("Installed generation capacity aggregated")
    document is published yearly (``processType`` ``A33``, "year ahead"),
    one ``TimeSeries`` per PSR type with a single annual value rather than
    an hourly curve -- unlike ``fetch_production``/``fetch_load``, this
    doesn't need ``_parse_period_points``' resolution handling; each
    ``Period`` has exactly one ``Point``.

    Args:
        zone: OKO zone key, must be a key of ``ENTSOE_DOMAIN_MAPPINGS``.
        year: the capacity year to query (ENTSO-E requires the period to
            span exactly one calendar year, UTC).
        client: shared HTTP client.
        settings: application settings (token, base URL, timeout).

    Returns:
        Category -> installed capacity, MW. A category absent from the
        response has no installed capacity reported for that year.

    Raises:
        EntsoeNoDataError: if the zone has no capacity data for this year.
        EntsoeError: if the zone is unknown, the request fails, or the
            response can't be parsed.
    """
    if zone not in ENTSOE_DOMAIN_MAPPINGS:
        raise EntsoeError(f"Unknown zone for ENTSO-E capacity query: {zone!r}")

    start = dt.datetime(year, 1, 1, tzinfo=dt.UTC)
    end = dt.datetime(year + 1, 1, 1, tzinfo=dt.UTC)
    params = {
        "documentType": "A68",
        "processType": "A33",
        "in_Domain": ENTSOE_DOMAIN_MAPPINGS[zone],
        **_period_span(start, end),
    }
    parsed = await _request_entsoe(client, settings, params)
    document = parsed.get("GL_MarketDocument", {})

    capacity_by_category: dict[str, float] = {}
    for timeseries in _as_list(document.get("TimeSeries")):
        psr_type = timeseries.get("MktPSRType", {}).get("psrType")
        if psr_type is None or psr_type in STORAGE_PSR_TYPES:
            continue
        category = PSR_TYPE_TO_CATEGORY.get(psr_type, "unknown")
        for period in _as_list(timeseries.get("Period")):
            points = _as_list(period.get("Point"))
            if not points:
                continue
            # One annual value per TimeSeries -- take it directly rather
            # than expanding via _parse_period_points (built for hourly
            # curves with A01/A03 curveType and PT*M/P*D resolutions,
            # neither of which applies to a single yearly point).
            value = float(points[0]["quantity"])
            capacity_by_category[category] = capacity_by_category.get(category, 0.0) + value

    logger.info(
        "entsoe.installed_capacity_fetched",
        zone=zone,
        year=year,
        categories=len(capacity_by_category),
    )
    return capacity_by_category
