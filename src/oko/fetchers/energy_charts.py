"""energy-charts.info (Fraunhofer ISE) fetcher: CO2 intensity reference data.

CC-BY 4.0, no account required. Used **only** as a backtesting/validation
reference for OKO's own emissions calculation and forecast (see
``oko.forecast.backtest``) — never as a live-forecast input, per the
project's data-source constraints.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger(__name__)


class EnergyChartsError(RuntimeError):
    """Raised when the energy-charts.info API request fails or is malformed."""


@dataclass(frozen=True, slots=True)
class ReferencePoint:
    """One hourly reference CO2 intensity observation.

    Attributes:
        timestamp: start of the hour, UTC.
        co2eq_g_per_kwh: mean measured carbon intensity for that hour.
    """

    timestamp: dt.datetime
    co2eq_g_per_kwh: float


async def fetch_co2eq_reference(
    *,
    start: dt.date,
    end: dt.date,
    country: str = "de",
    base_url: str,
    client: httpx.AsyncClient,
    timeout: float = 30.0,
) -> list[ReferencePoint]:
    """Fetch historical CO2 intensity from energy-charts.info, resampled to hourly.

    The upstream API reports at 15-minute resolution; points are grouped
    by hour and averaged. Null values (energy-charts marks incomplete
    slots as ``null``) are dropped before averaging, and an hour with no
    valid slots at all is omitted from the result.

    Args:
        start: first day to include (inclusive).
        end: last day to include (inclusive).
        country: energy-charts.info country code, lowercase (e.g. ``"de"``).
        base_url: energy-charts.info API base URL
            (``settings.energy_charts_base_url``).
        client: shared HTTP client.
        timeout: request timeout in seconds.

    Returns:
        Hourly reference points, sorted by timestamp.

    Raises:
        EnergyChartsError: if the request fails or the response is
            missing the expected fields.
    """
    params = {"country": country, "start": start.isoformat(), "end": end.isoformat()}
    try:
        response = await client.get(f"{base_url}/co2eq", params=params, timeout=timeout)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise EnergyChartsError(f"energy-charts.info request failed: {exc}") from exc

    try:
        payload = response.json()
        unix_seconds: list[int] = payload["unix_seconds"]
        co2eq: list[float | None] = payload["co2eq"]
    except (ValueError, KeyError) as exc:
        raise EnergyChartsError(f"Unexpected energy-charts.info response shape: {exc}") from exc

    if len(unix_seconds) != len(co2eq):
        raise EnergyChartsError(
            f"energy-charts.info returned mismatched array lengths: "
            f"{len(unix_seconds)} timestamps vs {len(co2eq)} values"
        )

    hourly_buckets: dict[dt.datetime, list[float]] = {}
    for seconds, value in zip(unix_seconds, co2eq, strict=True):
        if value is None:
            continue
        timestamp = dt.datetime.fromtimestamp(seconds, tz=dt.UTC)
        hour = timestamp.replace(minute=0, second=0, microsecond=0)
        hourly_buckets.setdefault(hour, []).append(value)

    points = [
        ReferencePoint(timestamp=hour, co2eq_g_per_kwh=sum(values) / len(values))
        for hour, values in sorted(hourly_buckets.items())
    ]
    logger.info(
        "energy_charts.co2eq_fetched",
        country=country,
        start=start.isoformat(),
        end=end.isoformat(),
        hours=len(points),
    )
    return points
