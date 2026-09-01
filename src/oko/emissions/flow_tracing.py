"""Multi-hop cross-border carbon-intensity flow tracing.

Implements the classical "proportional sharing" / average-participation
method (Bialek 1996, "Tracing the flow of electricity"; the same
well-mixed assumption underlies published real-time carbon-accounting
methods for European electricity markets, e.g. Tranberg et al. 2019) over
the extended zone network in ``oko.config.FLOW_TRACING_ZONES`` /
``EXCHANGE_BORDERS``. This is **not** adopted from electricitymaps-contrib
-- their public repository contains only parsers and static
configuration, not their production flow-tracing engine (verified by
inspecting the repo directly; see ATTRIBUTION.md) -- so this module is
OKO-original, implementing a well-published academic method.

The core assumption is the same one-hop ``oko.emissions.calculator``
already documents and uses: energy leaving a node (whether re-exported or
consumed locally) carries the same average mix as everything that entered
it. Flow tracing applies that assumption *simultaneously* across the whole
network by solving a linear system, rather than stopping after one hop --
so a zone's computed intensity correctly reflects what its neighbours
themselves imported, and their neighbours', and so on, including through
loops in the network (a plain one-hop lookup or naive recursive
substitution can't resolve loops; a linear solve can, in one pass).

For one hour, with zones i = 1..n:

    P_i = G_i + sum_j F(j->i)        total inflow: own generation + imports
    c_i * P_i = E_i + sum_j F(j->i) * c_j

which is a linear system ``M c = E`` where ``M[i][i] = P_i``,
``M[i][j] = -F(j->i)`` for j != i, and ``E_i`` is zone i's own emission
rate (generation-weighted, same as ``calculator.production_intensity``'s
numerator). Solving gives every zone's consumption-based intensity in one
pass. This reduces exactly to ``production_intensity`` for a zone with no
imports, and exactly to ``calculator.calculate``'s one-hop formula for a
two-zone system where the exporter has no imports of its own -- both
checked directly in the test suite, which is how the derivation was
verified rather than trusted from memory alone.

Zones with non-positive total inflow that hour (no generation and no
positive imports -- a data gap, not a real physical state) are pruned
from the system, along with their incident edges, before solving; a zone
excluded this way simply has no result for that hour. If the resulting
linear system is singular anyway (rare -- e.g. an isolated disconnected
component), the whole hour falls back to ``oko.emissions.calculator``'s
one-hop method per zone as a safety net, logged so the degradation is
visible rather than silent.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence

import numpy as np
import structlog

from oko.emissions.calculator import (
    CalculatorError,
    CarbonIntensity,
    calculate,
    import_mw_into,
    other_zone,
)
from oko.fetchers.entsoe import ExchangeRecord

logger = structlog.get_logger(__name__)


def _own_generation_and_emissions(
    zone: str,
    production_by_category: Mapping[str, float],
    direct_factors: Mapping[str, float],
    lifecycle_factors: Mapping[str, float] | None,
) -> tuple[float, float, float | None]:
    unknown_direct = direct_factors["unknown"]
    generation = 0.0
    direct_emissions = 0.0
    lifecycle_emissions: float | None = 0.0 if lifecycle_factors is not None else None
    for category, mw in production_by_category.items():
        if mw <= 0:
            continue
        generation += mw
        direct_emissions += mw * direct_factors.get(category, unknown_direct)
        if lifecycle_factors is not None:
            lifecycle_emissions = (lifecycle_emissions or 0.0) + mw * lifecycle_factors.get(
                category, lifecycle_factors["unknown"]
            )
    return generation, direct_emissions, lifecycle_emissions


def _prune_to_positive_inflow(
    candidate_zones: set[str],
    production_by_zone_hour: Mapping[str, Mapping[str, float]],
    factors_for_zone: Mapping[str, Mapping[str, float]],
    records: Sequence[ExchangeRecord],
    lifecycle_factors_for_zone: Mapping[str, Mapping[str, float]] | None = None,
) -> tuple[dict[str, float], dict[str, float], dict[str, float] | None, dict[str, float]]:
    surviving = set(candidate_zones)
    while True:
        generation: dict[str, float] = {}
        emissions: dict[str, float] = {}
        lifecycle_emissions: dict[str, float] | None = (
            {} if lifecycle_factors_for_zone is not None else None
        )
        for zone in surviving:
            zone_lifecycle_factors = (
                lifecycle_factors_for_zone[zone] if lifecycle_factors_for_zone is not None else None
            )
            gen, direct, lifecycle = _own_generation_and_emissions(
                zone, production_by_zone_hour[zone], factors_for_zone[zone], zone_lifecycle_factors
            )
            generation[zone] = gen
            emissions[zone] = direct
            if lifecycle_emissions is not None:
                lifecycle_emissions[zone] = lifecycle or 0.0
        inflow = dict(generation)
        for record in records:
            if record.zone_from not in surviving or record.zone_to not in surviving:
                continue
            if record.net_flow_mw > 0:
                inflow[record.zone_to] += record.net_flow_mw
            elif record.net_flow_mw < 0:
                inflow[record.zone_from] += -record.net_flow_mw

        to_drop = {zone for zone in surviving if inflow[zone] <= 0}
        if not to_drop:
            return generation, emissions, lifecycle_emissions, inflow
        surviving -= to_drop


def _solve_hour(
    zones: list[str],
    generation: Mapping[str, float],
    emissions: Mapping[str, float],
    inflow: Mapping[str, float],
    records: Sequence[ExchangeRecord],
    lifecycle_emissions: Mapping[str, float] | None = None,
) -> tuple[dict[str, float], dict[str, float] | None] | None:
    index = {zone: i for i, zone in enumerate(zones)}
    matrix = np.diag([inflow[zone] for zone in zones])
    for record in records:
        if record.zone_from not in index or record.zone_to not in index:
            continue
        importer, flow_mw = (
            (record.zone_to, record.net_flow_mw)
            if record.net_flow_mw > 0
            else (record.zone_from, -record.net_flow_mw)
            if record.net_flow_mw < 0
            else (None, 0.0)
        )
        if importer is None:
            continue
        exporter = record.zone_from if importer == record.zone_to else record.zone_to
        matrix[index[importer], index[exporter]] -= flow_mw

    if lifecycle_emissions is not None:
        rhs = np.column_stack(
            [
                [emissions[zone] for zone in zones],
                [lifecycle_emissions[zone] for zone in zones],
            ]
        )
    else:
        rhs = np.array([emissions[zone] for zone in zones])
    try:
        solution = np.linalg.solve(matrix, rhs)
    except np.linalg.LinAlgError:
        return None
    if not np.all(np.isfinite(solution)):
        return None

    if lifecycle_emissions is not None:
        direct_solution = dict(zip(zones, (float(c) for c in solution[:, 0]), strict=True))
        lifecycle_solution = dict(zip(zones, (float(c) for c in solution[:, 1]), strict=True))
        return direct_solution, lifecycle_solution
    return dict(zip(zones, (float(c) for c in solution), strict=True)), None


def trace_flows_for_hour(
    timestamp: dt.datetime,
    production_by_zone_hour: Mapping[str, Mapping[str, float]],
    records: Sequence[ExchangeRecord],
    factors_for_zone: Mapping[str, Mapping[str, float]],
    lifecycle_factors_for_zone: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, CarbonIntensity]:
    hour_records = [r for r in records if r.timestamp == timestamp]
    generation, emissions, lifecycle_emissions, inflow = _prune_to_positive_inflow(
        set(production_by_zone_hour),
        production_by_zone_hour,
        factors_for_zone,
        hour_records,
        lifecycle_factors_for_zone,
    )
    zones = sorted(generation)
    if not zones:
        return {}

    solved = _solve_hour(zones, generation, emissions, inflow, hour_records, lifecycle_emissions)
    if solved is None:
        logger.warning(
            "flow_tracing.singular_system_fallback",
            timestamp=timestamp.isoformat(),
            zones=zones,
        )
        return _one_hop_fallback(
            timestamp, zones, production_by_zone_hour, hour_records, factors_for_zone
        )
    direct_solution, lifecycle_solution = solved

    results: dict[str, CarbonIntensity] = {}
    for zone in zones:
        import_share = max(inflow[zone] - generation[zone], 0.0) / inflow[zone]
        domestic = emissions[zone] / generation[zone] if generation[zone] > 0 else None
        results[zone] = CarbonIntensity(
            zone=zone,
            timestamp=timestamp,
            domestic_g_per_kwh=domestic,
            corrected_g_per_kwh=max(direct_solution[zone], 0.0),
            import_share=import_share,
            lifecycle_g_per_kwh=(
                max(lifecycle_solution[zone], 0.0) if lifecycle_solution is not None else None
            ),
            method="flow_trace",
        )
    return results


def _one_hop_fallback(
    timestamp: dt.datetime,
    zones: list[str],
    production_by_zone_hour: Mapping[str, Mapping[str, float]],
    hour_records: Sequence[ExchangeRecord],
    factors_for_zone: Mapping[str, Mapping[str, float]],
) -> dict[str, CarbonIntensity]:
    results: dict[str, CarbonIntensity] = {}
    zone_set = set(zones)
    for zone in zones:
        neighbor_imports: dict[str, float] = {}
        neighbor_intensities: dict[str, float] = {}
        for record in hour_records:
            if zone not in (record.zone_from, record.zone_to):
                continue
            neighbor = other_zone(zone, record)
            if neighbor is None or neighbor not in zone_set:
                continue
            neighbor_imports[neighbor] = import_mw_into(zone, record)
            neighbor_generation, neighbor_emissions, _ = _own_generation_and_emissions(
                neighbor, production_by_zone_hour[neighbor], factors_for_zone[neighbor], None
            )
            if neighbor_generation > 0:
                neighbor_intensities[neighbor] = neighbor_emissions / neighbor_generation
        try:
            results[zone] = calculate(
                zone,
                timestamp,
                production_by_zone_hour[zone],
                factors_for_zone[zone],
                neighbor_imports,
                neighbor_intensities,
            )
        except CalculatorError:
            logger.warning(
                "flow_tracing.fallback_zone_unresolvable",
                zone=zone,
                timestamp=timestamp.isoformat(),
            )
    return results


def trace_flows_series(
    production_by_zone: Mapping[str, Mapping[dt.datetime, Mapping[str, float]]],
    exchange_records: Sequence[ExchangeRecord],
    factors_for_zone: Mapping[str, Mapping[str, float]],
    lifecycle_factors_for_zone: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, list[CarbonIntensity]]:
    hours = sorted({hour for zone_hours in production_by_zone.values() for hour in zone_hours})
    by_zone: dict[str, list[CarbonIntensity]] = {}
    for hour in hours:
        production_this_hour = {
            zone: hours_map[hour]
            for zone, hours_map in production_by_zone.items()
            if hour in hours_map
        }
        for zone, intensity in trace_flows_for_hour(
            hour,
            production_this_hour,
            exchange_records,
            factors_for_zone,
            lifecycle_factors_for_zone,
        ).items():
            by_zone.setdefault(zone, []).append(intensity)
    return by_zone
