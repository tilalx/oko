"""Backtest OKO's calculated carbon intensity against energy-charts.info.

Compares ``flow_tracing.trace_flows_series`` output for the target zone
against energy-charts.info's own published ``co2eq`` values (a
Fraunhofer-ISE-maintained, CC-BY-4.0 reference — never used as a
live-forecast input, only for validation here) over a historical window,
and reports the deviation rather than hiding it, per the project's Phase 2
acceptance criterion.

Run as a script::

    uv run python -m oko.emissions.backtest --days 28

Requires ``ENTSOE_TOKEN`` (production/exchange data comes from ENTSO-E;
the energy-charts.info reference side needs no token).
"""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import statistics
from dataclasses import dataclass

import httpx
import structlog

from oko.config import EXCHANGE_BORDERS, FLOW_TRACING_ZONES, TARGET_ZONE, Settings, get_settings
from oko.emissions import flow_tracing
from oko.emissions.calculator import CarbonIntensity
from oko.emissions.factors import factors_for_zone
from oko.fetchers import energy_charts, entsoe

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class BacktestReport:
    """Summary statistics comparing calculated vs. reference intensity.

    Attributes:
        zone: OKO zone key the backtest was run for.
        window_start: first hour included, UTC.
        window_end: last hour included (exclusive), UTC.
        matched_hours: number of hours present in both series.
        mean_absolute_error: mean(|calculated - reference|), g CO2eq/kWh.
        mean_bias: mean(calculated - reference), g CO2eq/kWh — positive
            means OKO's calculation runs systematically high.
        rmse: root-mean-squared error, g CO2eq/kWh.
    """

    zone: str
    window_start: dt.datetime
    window_end: dt.datetime
    matched_hours: int
    mean_absolute_error: float
    mean_bias: float
    rmse: float


def compare(
    calculated: list[CarbonIntensity], reference: list[energy_charts.ReferencePoint]
) -> BacktestReport:
    """Compare calculated and reference intensity series hour-by-hour.

    Args:
        calculated: OKO's own computed intensity, e.g. from
            ``flow_tracing.trace_flows_series``.
        reference: energy-charts.info reference points covering (at
            least part of) the same window.

    Returns:
        A ``BacktestReport`` over whichever hours appear in both inputs.

    Raises:
        ValueError: if the two series share no timestamps at all.
    """
    reference_by_hour = {point.timestamp: point.co2eq_g_per_kwh for point in reference}
    errors = [
        (entry.corrected_g_per_kwh - reference_by_hour[entry.timestamp])
        for entry in calculated
        if entry.timestamp in reference_by_hour
    ]
    if not errors:
        raise ValueError("No overlapping hours between calculated and reference series")

    all_hours = sorted({e.timestamp for e in calculated} | set(reference_by_hour))
    return BacktestReport(
        zone=calculated[0].zone,
        window_start=all_hours[0],
        window_end=all_hours[-1],
        matched_hours=len(errors),
        mean_absolute_error=statistics.fmean(abs(e) for e in errors),
        mean_bias=statistics.fmean(errors),
        rmse=statistics.fmean(e**2 for e in errors) ** 0.5,
    )


async def _fetch_production_history(
    zones: tuple[str, ...],
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> dict[str, dict[dt.datetime, dict[str, float]]]:
    """Fetch production history for each zone, skipping any zone that fails."""
    result: dict[str, dict[dt.datetime, dict[str, float]]] = {}
    for zone in zones:
        try:
            records = await entsoe.fetch_production(
                zone, start, end, client=client, settings=settings
            )
        except entsoe.EntsoeError as exc:
            logger.warning("backtest.production_fetch_failed", zone=zone, error=str(exc))
            continue
        result[zone] = {record.timestamp: record.by_category for record in records}
    return result


async def _fetch_exchange_history(
    borders: tuple[tuple[str, str], ...],
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> list[entsoe.ExchangeRecord]:
    """Fetch exchange history for every border in the flow-tracing network, skipping failures."""
    records: list[entsoe.ExchangeRecord] = []
    for zone_a, zone_b in borders:
        try:
            records.extend(
                await entsoe.fetch_exchange(
                    zone_a, zone_b, start, end, client=client, settings=settings
                )
            )
        except entsoe.EntsoeError as exc:
            logger.warning(
                "backtest.exchange_fetch_failed", border=f"{zone_a}-{zone_b}", error=str(exc)
            )
    return records


async def run_backtest(days: int, *, settings: Settings | None = None) -> BacktestReport:
    """Run the full backtest: fetch history, calculate, compare to reference.

    Args:
        days: length of the historical window to backtest, ending now.
        settings: application settings; defaults to ``get_settings()``.

    Returns:
        The resulting ``BacktestReport``.
    """
    resolved_settings = settings or get_settings()
    end = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=days)

    async with httpx.AsyncClient() as client:
        production = await _fetch_production_history(
            FLOW_TRACING_ZONES, start, end, client=client, settings=resolved_settings
        )
        exchanges = await _fetch_exchange_history(
            EXCHANGE_BORDERS, start, end, client=client, settings=resolved_settings
        )
        reference = await energy_charts.fetch_co2eq_reference(
            start=start.date(),
            end=end.date(),
            base_url=resolved_settings.energy_charts_base_url,
            client=client,
        )

    factor_tables = {zone: factors_for_zone(zone) for zone in production}
    traced_series = flow_tracing.trace_flows_series(production, exchanges, factor_tables)
    calculated = traced_series.get(TARGET_ZONE, [])
    return compare(calculated, reference)


def main() -> None:
    """CLI entry point: ``python -m oko.emissions.backtest --days 28``."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=28, help="Backtest window length in days.")
    args = parser.parse_args()

    report = asyncio.run(run_backtest(args.days))
    print(f"Backtest: {report.zone}, {report.window_start} -> {report.window_end}")
    print(f"  matched hours:        {report.matched_hours}")
    print(f"  mean absolute error:  {report.mean_absolute_error:.1f} gCO2eq/kWh")
    print(f"  mean bias:            {report.mean_bias:+.1f} gCO2eq/kWh")
    print(f"  RMSE:                 {report.rmse:.1f} gCO2eq/kWh")


if __name__ == "__main__":
    main()
