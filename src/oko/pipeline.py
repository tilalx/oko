"""Hourly pipeline: fetch history -> upsert -> train -> forecast -> export."""

from __future__ import annotations

import argparse
import asyncio
import datetime as dt
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog

from oko.config import (
    EXCHANGE_BORDERS,
    FLOW_TRACING_ZONES,
    TARGET_ZONE,
    ZONE_BBOXES,
    Settings,
    get_settings,
)
from oko.emissions import flow_tracing
from oko.emissions.calculator import (
    CarbonIntensity,
    emissions_weighted_breakdown_percentages,
    power_breakdown_percentages,
)
from oko.emissions.factors import factors_for_zone
from oko.export import (
    CurrentBreakdown,
    build_exchanges_payload,
    build_payload,
    write_json,
)
from oko.fetchers import entsoe, noaa_gfs
from oko.forecast.features import (
    PRICE_LAG_HOURS,
    build_forecast_features,
    build_training_features,
    with_price_lag,
)
from oko.forecast.model import BreakdownModel, CarbonIntensityModel, Prediction, PriceModel
from oko.history import (
    HistoryRow,
    load_breakdown_training_rows,
    load_price_training_rows,
    load_recent_prices,
    load_training_rows,
    upsert_rows,
)

logger = structlog.get_logger(__name__)

HISTORY_FETCH_WINDOW_HOURS = 49
MIN_TRAINING_ROWS = 24 * 14


class PipelineError(RuntimeError):
    """Pipeline execution failed."""

    pass


class InsufficientHistoryError(PipelineError):
    """Insufficient training history to forecast."""

    pass


ENTSOE_MAX_CONCURRENCY = 4


async def _fetch_production_for_zones(
    zones: tuple[str, ...],
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    max_concurrency: int = ENTSOE_MAX_CONCURRENCY,
) -> dict[str, dict[dt.datetime, dict[str, float]]]:
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _fetch_one(zone: str) -> tuple[str, dict[dt.datetime, dict[str, float]]] | None:
        async with semaphore:
            try:
                records = await entsoe.fetch_production(
                    zone, start, end, client=client, settings=settings
                )
            except entsoe.EntsoeError as exc:
                logger.warning("pipeline.production_fetch_failed", zone=zone, error=str(exc))
                return None
        return zone, {record.timestamp: record.by_category for record in records}

    results = await asyncio.gather(*(_fetch_one(zone) for zone in zones))
    return dict(result for result in results if result is not None)


async def _fetch_all_borders(
    borders: tuple[tuple[str, str], ...],
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    max_concurrency: int = ENTSOE_MAX_CONCURRENCY,
) -> list[entsoe.ExchangeRecord]:
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _fetch_one(border: tuple[str, str]) -> list[entsoe.ExchangeRecord]:
        zone_a, zone_b = border
        async with semaphore:
            try:
                return await entsoe.fetch_exchange(
                    zone_a, zone_b, start, end, client=client, settings=settings
                )
            except entsoe.EntsoeError as exc:
                logger.warning(
                    "pipeline.exchange_fetch_failed", border=f"{zone_a}-{zone_b}", error=str(exc)
                )
                return []

    results = await asyncio.gather(*(_fetch_one(border) for border in borders))
    return [record for records in results for record in records]


async def _fetch_load_for_zones(
    zones: tuple[str, ...],
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    max_concurrency: int = ENTSOE_MAX_CONCURRENCY,
) -> dict[str, dict[dt.datetime, float]]:
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _fetch_one(zone: str) -> tuple[str, dict[dt.datetime, float]] | None:
        async with semaphore:
            try:
                records = await entsoe.fetch_load(
                    zone, start, end, client=client, settings=settings
                )
            except entsoe.EntsoeError as exc:
                logger.warning("pipeline.load_fetch_failed", zone=zone, error=str(exc))
                return None
        return zone, {record.timestamp: record.load_mw for record in records}

    results = await asyncio.gather(*(_fetch_one(zone) for zone in zones))
    return dict(result for result in results if result is not None)


async def _fetch_price_for_zones(
    zones: tuple[str, ...],
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
    max_concurrency: int = ENTSOE_MAX_CONCURRENCY,
) -> dict[str, dict[dt.datetime, float]]:
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _fetch_one(zone: str) -> tuple[str, dict[dt.datetime, float]] | None:
        async with semaphore:
            try:
                records = await entsoe.fetch_day_ahead_prices(
                    zone, start, end, client=client, settings=settings
                )
            except entsoe.EntsoeError as exc:
                logger.warning("pipeline.price_fetch_failed", zone=zone, error=str(exc))
                return None
        return zone, {record.timestamp: record.price_eur_per_mwh for record in records}

    results = await asyncio.gather(*(_fetch_one(zone) for zone in zones))
    return dict(result for result in results if result is not None)


def _current_breakdown(
    production_by_hour: Mapping[dt.datetime, Mapping[str, float]] | None,
    factors: Mapping[str, float],
) -> CurrentBreakdown | None:
    if not production_by_hour:
        return None
    latest_hour = max(production_by_hour)
    latest_production = production_by_hour[latest_hour]
    breakdown, renewable_pct, fossil_free_pct = power_breakdown_percentages(latest_production)
    if not breakdown:
        return None
    return CurrentBreakdown(
        timestamp=latest_hour,
        power_breakdown_percent=breakdown,
        renewable_percent=renewable_pct,
        fossil_free_percent=fossil_free_pct,
        emissions_breakdown_percent=emissions_weighted_breakdown_percentages(
            latest_production, factors
        ),
    )


@dataclass(frozen=True, slots=True)
class PipelineResult:
    """Results from one pipeline run across all zones."""

    zones: dict[str, dict[str, object]]
    exchanges: dict[str, object] | None


def upsert_zone_history(
    zone: str,
    *,
    production_by_hour: Mapping[dt.datetime, Mapping[str, float]] | None,
    load_by_hour: Mapping[dt.datetime, float] | None,
    traced: list[CarbonIntensity],
    settings: Settings,
    price_by_hour: Mapping[dt.datetime, float] | None = None,
) -> int:
    """Upsert calculated intensities to history database."""
    intensity_by_hour = {entry.timestamp: entry for entry in traced}
    if not (production_by_hour and load_by_hour):
        logger.warning(
            "pipeline.skipping_history_update",
            zone=zone,
            reason="missing production or load this run",
        )
        return 0
    new_feature_rows = build_training_features(production_by_hour, load_by_hour)
    new_history = [
        HistoryRow(
            zone=zone,
            features=row,
            target_g_per_kwh=intensity_by_hour[row.timestamp].corrected_g_per_kwh,
            lifecycle_g_per_kwh=intensity_by_hour[row.timestamp].lifecycle_g_per_kwh,
            method=intensity_by_hour[row.timestamp].method,
            breakdown_percent=(
                power_breakdown_percentages(production_by_hour[row.timestamp])[0] or None
            ),
            price_eur_per_mwh=(price_by_hour or {}).get(row.timestamp),
        )
        for row in new_feature_rows
        if row.timestamp in intensity_by_hour
    ]
    upsert_rows(settings.sqlite_path, new_history)
    return len(new_history)


async def _run_zone(
    zone: str,
    *,
    production_by_hour: Mapping[dt.datetime, Mapping[str, float]] | None,
    load_by_hour: Mapping[dt.datetime, float] | None,
    traced: list[CarbonIntensity],
    factors: Mapping[str, float],
    now: dt.datetime,
    weather: list[noaa_gfs.WeatherPoint],
    settings: Settings,
    strict: bool,
    price_by_hour: Mapping[dt.datetime, float] | None = None,
) -> dict[str, object] | None:
    if zone != TARGET_ZONE:
        upsert_zone_history(
            zone,
            production_by_hour=production_by_hour,
            load_by_hour=load_by_hour,
            traced=traced,
            settings=settings,
            price_by_hour=price_by_hour,
        )

    training_rows, training_targets = load_training_rows(settings.sqlite_path, zone)
    if len(training_rows) < MIN_TRAINING_ROWS:
        message = (
            f"Only {len(training_rows)}/{MIN_TRAINING_ROWS} accumulated training hours "
            f"for {zone} — bootstrapping, no forecast produced this run."
        )
        if strict:
            raise InsufficientHistoryError(message)
        logger.warning(
            "pipeline.zone_bootstrapping",
            zone=zone,
            training_rows=len(training_rows),
            required=MIN_TRAINING_ROWS,
        )
        return None

    model_dir = settings.model_dir / zone
    direct_model = CarbonIntensityModel.train(training_rows, training_targets)
    direct_model.save(model_dir / "direct.txt")

    lifecycle_rows, lifecycle_targets = load_training_rows(
        settings.sqlite_path, zone, target="lifecycle"
    )
    lifecycle_model: CarbonIntensityModel | None = None
    if len(lifecycle_rows) >= MIN_TRAINING_ROWS:
        lifecycle_model = CarbonIntensityModel.train(lifecycle_rows, lifecycle_targets)
        lifecycle_model.save(model_dir / "lifecycle.txt")

    breakdown_rows, breakdown_targets = load_breakdown_training_rows(settings.sqlite_path, zone)
    breakdown_model: BreakdownModel | None = None
    if len(breakdown_rows) >= MIN_TRAINING_ROWS:
        breakdown_model = BreakdownModel.train(breakdown_rows, breakdown_targets)
        breakdown_model.save(model_dir / "breakdown")

    price_rows, price_targets = load_price_training_rows(settings.sqlite_path, zone)
    price_model: PriceModel | None = None
    if len(price_rows) >= MIN_TRAINING_ROWS:
        price_model = PriceModel.train(price_rows, price_targets)
        price_model.save(model_dir / "price.txt")

    forecast_rows = build_forecast_features(weather, reference_time=now)
    direct_predictions = direct_model.predict(forecast_rows)
    lifecycle_by_timestamp = (
        {p.timestamp: p.value_g_per_kwh for p in lifecycle_model.predict(forecast_rows)}
        if lifecycle_model is not None
        else {}
    )
    breakdown_by_timestamp = (
        {p.timestamp: p.power_breakdown_percent for p in breakdown_model.predict(forecast_rows)}
        if breakdown_model is not None
        else {}
    )
    price_by_timestamp: dict[dt.datetime, float] = {}
    if price_model is not None:
        # PRICE_LAG_HOURS > the 120h forecast horizon, so every lag lookup
        # resolves to an already-observed hour -- never one still ahead.
        price_lag_history = load_recent_prices(
            settings.sqlite_path, zone, since=now - dt.timedelta(hours=PRICE_LAG_HOURS + 1)
        )
        price_forecast_rows = with_price_lag(forecast_rows, price_lag_history)
        price_by_timestamp = {
            p.timestamp: p.price_eur_per_mwh for p in price_model.predict(price_forecast_rows)
        }
    predictions = [
        Prediction(
            timestamp=p.timestamp,
            value_g_per_kwh=p.value_g_per_kwh,
            confidence=p.confidence,
            value_lifecycle_g_per_kwh=lifecycle_by_timestamp.get(p.timestamp),
            power_breakdown_percent=breakdown_by_timestamp.get(p.timestamp),
            price_eur_per_mwh=price_by_timestamp.get(p.timestamp),
        )
        for p in direct_predictions
    ]

    logger.info(
        "pipeline.zone_forecast_complete",
        zone=zone,
        training_rows=len(training_rows),
        lifecycle_training_rows=len(lifecycle_rows),
        breakdown_training_rows=len(breakdown_rows),
        price_training_rows=len(price_rows),
        forecast_horizon_hours=len(predictions),
    )

    return build_payload(
        predictions,
        zone=zone,
        generated_at=now,
        model_version=settings.model_version,
        source_repo_url=settings.source_repo_url,
        training_rows=len(training_rows),
        current=_current_breakdown(production_by_hour, factors),
    )


@dataclass(frozen=True, slots=True)
class WindowData:
    """Production and exchange data for one time window."""

    production: dict[str, dict[dt.datetime, dict[str, float]]]
    exchanges: list[entsoe.ExchangeRecord]
    load_by_zone: dict[str, dict[dt.datetime, float]]
    traced_series: dict[str, list[CarbonIntensity]]
    direct_factor_tables: dict[str, dict[str, float]]
    price_by_zone: dict[str, dict[dt.datetime, float]]


async def fetch_and_trace_window(
    start: dt.datetime,
    end: dt.datetime,
    *,
    client: httpx.AsyncClient,
    settings: Settings,
) -> WindowData:
    """Fetch and trace flows for production and exchanges."""
    production, exchanges, load_by_zone, price_by_zone = await asyncio.gather(
        _fetch_production_for_zones(
            FLOW_TRACING_ZONES, start, end, client=client, settings=settings
        ),
        _fetch_all_borders(EXCHANGE_BORDERS, start, end, client=client, settings=settings),
        _fetch_load_for_zones(FLOW_TRACING_ZONES, start, end, client=client, settings=settings),
        _fetch_price_for_zones(FLOW_TRACING_ZONES, start, end, client=client, settings=settings),
    )

    direct_factor_tables = {zone: factors_for_zone(zone) for zone in production}
    lifecycle_factor_tables = {
        zone: factors_for_zone(zone, kind="lifecycle") for zone in production
    }
    traced_series = flow_tracing.trace_flows_series(
        production, exchanges, direct_factor_tables, lifecycle_factor_tables
    )
    return WindowData(
        production=production,
        exchanges=exchanges,
        load_by_zone=load_by_zone,
        traced_series=traced_series,
        direct_factor_tables=direct_factor_tables,
        price_by_zone=price_by_zone,
    )


async def run_pipeline(*, settings: Settings | None = None) -> PipelineResult:
    """Run hourly forecast pipeline for all zones."""
    resolved_settings = settings or get_settings()
    now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    fetch_start = now - dt.timedelta(hours=HISTORY_FETCH_WINDOW_HOURS)

    async with httpx.AsyncClient() as client:
        window = await fetch_and_trace_window(
            fetch_start, now, client=client, settings=resolved_settings
        )

        succeeded_zones = sorted(window.production)
        failed_zones = sorted(set(FLOW_TRACING_ZONES) - set(window.production))

        upsert_zone_history(
            TARGET_ZONE,
            production_by_hour=window.production.get(TARGET_ZONE),
            load_by_hour=window.load_by_zone.get(TARGET_ZONE),
            traced=window.traced_series.get(TARGET_ZONE, []),
            settings=resolved_settings,
            price_by_hour=window.price_by_zone.get(TARGET_ZONE),
        )
        target_training_rows, _ = load_training_rows(resolved_settings.sqlite_path, TARGET_ZONE)
        if len(target_training_rows) < MIN_TRAINING_ROWS:
            raise InsufficientHistoryError(
                f"Only {len(target_training_rows)}/{MIN_TRAINING_ROWS} accumulated training "
                f"hours for {TARGET_ZONE} — bootstrapping, no forecast produced this run."
            )

        try:
            weather_by_zone = await noaa_gfs.fetch_forecast_for_zones(
                base_url=resolved_settings.noaa_gfs_base_url,
                bboxes={zone: ZONE_BBOXES[zone] for zone in FLOW_TRACING_ZONES},
                timeout=resolved_settings.http_timeout_seconds,
                client=client,
            )
        except noaa_gfs.NoaaGfsError as exc:
            raise PipelineError(f"NOAA GFS forecast fetch failed entirely: {exc}") from exc

        results = await asyncio.gather(
            *(
                _run_zone(
                    zone,
                    production_by_hour=window.production.get(zone),
                    load_by_hour=window.load_by_zone.get(zone),
                    traced=window.traced_series.get(zone, []),
                    factors=window.direct_factor_tables.get(zone) or factors_for_zone(zone),
                    now=now,
                    weather=weather_by_zone[zone],
                    settings=resolved_settings,
                    strict=(zone == TARGET_ZONE),
                    price_by_hour=window.price_by_zone.get(zone),
                )
                for zone in FLOW_TRACING_ZONES
            )
        )
        payloads: dict[str, dict[str, object]] = {
            zone: payload
            for zone, payload in zip(FLOW_TRACING_ZONES, results, strict=True)
            if payload is not None
        }

        exchanges_payload = (
            build_exchanges_payload(
                window.exchanges,
                generated_at=now,
                source_repo_url=resolved_settings.source_repo_url,
            )
            if window.exchanges
            else None
        )

    logger.info(
        "pipeline.run_complete",
        zones_ok=succeeded_zones,
        zones_failed=failed_zones,
        zones_forecast=sorted(payloads),
        borders_ok=len(exchanges_payload["exchanges"]) if exchanges_payload else 0,
        model_version=resolved_settings.model_version,
    )
    return PipelineResult(zones=payloads, exchanges=exchanges_payload)


def main() -> None:
    """Entry point: run pipeline and export forecast JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", required=True, help="Path to write DE-LU's forecast JSON to.")
    args = parser.parse_args()

    try:
        result = asyncio.run(run_pipeline())
    except InsufficientHistoryError as exc:
        logger.warning("pipeline.bootstrap_no_forecast", error=str(exc))
        return
    except PipelineError as exc:
        logger.error("pipeline.failed", error=str(exc))
        raise SystemExit(1) from exc

    target_path = Path(args.export)
    export_dir = target_path.parent
    for zone, payload in result.zones.items():
        path = target_path if zone == TARGET_ZONE else export_dir / f"forecast_{zone}.json"
        write_json(payload, path)
        logger.info("pipeline.exported", zone=zone, path=str(path))

    if result.exchanges is not None:
        exchanges_path = export_dir / "exchanges.json"
        write_json(result.exchanges, exchanges_path)
        logger.info("pipeline.exported", zone="exchanges", path=str(exchanges_path))


if __name__ == "__main__":
    main()
