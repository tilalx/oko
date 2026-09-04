"""Carbon intensity: production mix weighted by emission factors, plus one-hop import correction."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

import structlog

from oko.fetchers.entsoe import ExchangeRecord

logger = structlog.get_logger(__name__)


class CalculatorError(RuntimeError):
    """Carbon intensity calculation failed."""

    pass


RENEWABLE_CATEGORIES = frozenset({"wind", "solar", "hydro", "biomass", "geothermal"})
FOSSIL_FREE_CATEGORIES = RENEWABLE_CATEGORIES | {"nuclear"}


@dataclass(frozen=True, slots=True)
class CarbonIntensity:
    """Carbon intensity with import correction and breakdown."""

    zone: str
    timestamp: dt.datetime
    domestic_g_per_kwh: float | None
    corrected_g_per_kwh: float
    import_share: float
    lifecycle_g_per_kwh: float | None = None
    method: Literal["flow_trace", "one_hop_fallback"] = "one_hop_fallback"


def power_breakdown_percentages(
    production_by_category: Mapping[str, float],
) -> tuple[dict[str, float], float, float]:
    """Compute power mix breakdown, renewable %, and fossil-free %."""
    total_mw = sum(mw for mw in production_by_category.values() if mw > 0)
    if total_mw <= 0:
        return {}, 0.0, 0.0
    breakdown = {
        category: 100.0 * mw / total_mw for category, mw in production_by_category.items() if mw > 0
    }
    renewable_mw = sum(
        mw
        for category, mw in production_by_category.items()
        if mw > 0 and category in RENEWABLE_CATEGORIES
    )
    fossil_free_mw = sum(
        mw
        for category, mw in production_by_category.items()
        if mw > 0 and category in FOSSIL_FREE_CATEGORIES
    )
    return breakdown, 100.0 * renewable_mw / total_mw, 100.0 * fossil_free_mw / total_mw


def emissions_weighted_breakdown_percentages(
    production_by_category: Mapping[str, float], factors: Mapping[str, float]
) -> dict[str, float]:
    """Breakdown percentages weighted by emission factors."""
    unknown_factor = factors["unknown"]
    weighted = {
        category: mw * factors.get(category, unknown_factor)
        for category, mw in production_by_category.items()
        if mw > 0
    }
    total = sum(value for value in weighted.values() if value > 0)
    if total <= 0:
        return {}
    return {category: 100.0 * value / total for category, value in weighted.items() if value > 0}


def production_intensity(
    production_by_category: Mapping[str, float], factors: Mapping[str, float]
) -> float | None:
    """Emission intensity of production mix (g CO2eq/kWh)."""
    total_mw = sum(mw for mw in production_by_category.values() if mw > 0)
    if total_mw <= 0:
        return None
    unknown_factor = factors["unknown"]
    weighted = sum(
        factors.get(category, unknown_factor) * mw
        for category, mw in production_by_category.items()
        if mw > 0
    )
    return weighted / total_mw


def import_mw_into(zone: str, record: ExchangeRecord) -> float:
    """Import power into zone from exchange record (MW)."""
    if record.zone_to == zone:
        return max(record.net_flow_mw, 0.0)
    if record.zone_from == zone:
        return max(-record.net_flow_mw, 0.0)
    return 0.0


def other_zone(zone: str, record: ExchangeRecord) -> str | None:
    """Return the zone on the other side of ``record`` from ``zone``."""
    if record.zone_from == zone:
        return record.zone_to
    if record.zone_to == zone:
        return record.zone_from
    return None


def calculate(
    zone: str,
    timestamp: dt.datetime,
    production_by_category: Mapping[str, float],
    factors: Mapping[str, float],
    neighbor_imports_mw: Mapping[str, float],
    neighbor_domestic_intensities: Mapping[str, float],
) -> CarbonIntensity:
    """Compute carbon intensity with one-hop import correction."""
    domestic = production_intensity(production_by_category, factors)
    domestic_mw = sum(mw for mw in production_by_category.values() if mw > 0)

    numerator = domestic_mw * domestic if domestic is not None else 0.0
    denominator = domestic_mw if domestic is not None else 0.0

    for neighbor, import_mw in neighbor_imports_mw.items():
        if import_mw <= 0:
            continue
        neighbor_intensity = neighbor_domestic_intensities.get(neighbor)
        if neighbor_intensity is None:
            logger.warning(
                "calculator.missing_neighbor_intensity",
                zone=zone,
                neighbor=neighbor,
                timestamp=timestamp.isoformat(),
            )
            continue
        numerator += import_mw * neighbor_intensity
        denominator += import_mw

    if denominator <= 0:
        raise CalculatorError(
            f"No domestic production and no usable imports for {zone} at {timestamp.isoformat()}"
        )

    corrected = numerator / denominator
    import_share = max(denominator - domestic_mw, 0.0) / denominator

    return CarbonIntensity(
        zone=zone,
        timestamp=timestamp,
        domestic_g_per_kwh=domestic,
        corrected_g_per_kwh=corrected,
        import_share=import_share,
    )


def calculate_series(
    target_zone: str,
    production_by_zone: Mapping[str, Mapping[dt.datetime, Mapping[str, float]]],
    exchange_records: Sequence[ExchangeRecord],
    factors_for_zone: Mapping[str, Mapping[str, float]],
) -> list[CarbonIntensity]:
    """Compute carbon intensity over a time series with one-hop fallback."""
    exchanges_by_hour: dict[dt.datetime, list[ExchangeRecord]] = {}
    for record in exchange_records:
        if target_zone in (record.zone_from, record.zone_to):
            exchanges_by_hour.setdefault(record.timestamp, []).append(record)

    target_hours = production_by_zone.get(target_zone, {})
    all_hours = sorted(set(target_hours) | set(exchanges_by_hour))

    results: list[CarbonIntensity] = []
    for hour in all_hours:
        production = target_hours.get(hour, {})
        neighbor_imports: dict[str, float] = {}
        neighbor_intensities: dict[str, float] = {}
        for record in exchanges_by_hour.get(hour, []):
            neighbor = other_zone(target_zone, record)
            if neighbor is None:
                continue
            neighbor_imports[neighbor] = import_mw_into(target_zone, record)
            neighbor_production = production_by_zone.get(neighbor, {}).get(hour)
            if neighbor_production is not None:
                intensity = production_intensity(neighbor_production, factors_for_zone[neighbor])
                if intensity is not None:
                    neighbor_intensities[neighbor] = intensity

        try:
            results.append(
                calculate(
                    target_zone,
                    hour,
                    production,
                    factors_for_zone[target_zone],
                    neighbor_imports,
                    neighbor_intensities,
                )
            )
        except CalculatorError as exc:
            logger.warning("calculator.hour_skipped", zone=target_zone, error=str(exc))

    return results
