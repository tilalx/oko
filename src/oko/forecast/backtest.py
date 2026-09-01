"""Backtest the forecast model: MAE per forecast day vs. a naive baseline.

A walk-forward evaluation: for a series of historical "origin" hours, treat
each origin as if it were issuing a fresh 120-hour forecast, score every
predicted hour against what actually happened, and bin the error by which
day of the horizon it falls in (day 1 = hours 1-24, ..., day 5 = hours
97-120). Reported alongside a naive persistence baseline
(``predicted(t) = actual(t - 24h)``), per the project's Phase 3 acceptance
criterion — the model needs to visibly beat that, not just exist.

This runs in **perfect prognosis** mode (see ``oko.forecast.features``):
the "forecast" features fed to the model at each origin are actually
computed from what really happened at each target hour, not from an
archived weather forecast issued at that origin (OKO doesn't have access
to historical GFS forecast archives in this environment). That measures
the model's regression skill given ideal future weather knowledge, not the
full end-to-end error including NOAA's forecast error — a real limitation
of this backtest, stated rather than hidden, consistent with the rest of
the project's validation approach.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import statistics
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from oko.forecast.features import FeatureRow
from oko.forecast.model import CarbonIntensityModel

#: Forecast day boundaries in hours, matching the confidence tiers in model.py.
HOURS_PER_DAY = 24
NUM_FORECAST_DAYS = 5


@dataclass(frozen=True, slots=True)
class DayMetric:
    """Aggregated error for one day-of-horizon across every backtest origin.

    Attributes:
        day: 1-5, which day of the forecast horizon this covers (day 1 =
            hours 1-24 ahead, day 5 = hours 97-120 ahead).
        model_mae: mean absolute error of the model's predictions,
            g CO2eq/kWh.
        naive_mae: mean absolute error of the naive 24h-ago persistence
            baseline over the same target hours, g CO2eq/kWh.
        n: number of scored (origin, target hour) pairs behind this row.
    """

    day: int
    model_mae: float
    naive_mae: float
    n: int


def day_of_horizon(horizon_hours: int) -> int:
    """Map an hour offset (1-120) to its forecast day (1-5)."""
    return min((horizon_hours - 1) // HOURS_PER_DAY + 1, NUM_FORECAST_DAYS)


def walk_forward_backtest(
    model: CarbonIntensityModel,
    actual_series: Mapping[dt.datetime, float],
    feature_series: Mapping[dt.datetime, FeatureRow],
    origins: Sequence[dt.datetime],
    *,
    horizon_hours: int = NUM_FORECAST_DAYS * HOURS_PER_DAY,
) -> list[DayMetric]:
    """Run the walk-forward backtest and aggregate error by forecast day.

    Args:
        model: a trained ``CarbonIntensityModel``.
        actual_series: timestamp -> observed carbon intensity (ground
            truth), e.g. from ``oko.emissions.calculator.calculate_series``.
        feature_series: timestamp -> perfect-prog ``FeatureRow`` for that
            hour (``horizon_hours`` on each row is ignored — overwritten
            per origin).
        origins: historical hours to treat as forecast issue times.
        horizon_hours: how far ahead each origin "forecasts", matching
            the model's intended horizon.

    Returns:
        One ``DayMetric`` per forecast day (1-5) that had at least one
        scored hour. Days with no data (e.g. too few origins near the end
        of the series) are simply omitted, not reported as zero.
    """
    model_errors_by_day: dict[int, list[float]] = {}
    naive_errors_by_day: dict[int, list[float]] = {}

    for origin in origins:
        for h in range(1, horizon_hours + 1):
            target_time = origin + dt.timedelta(hours=h)
            actual = actual_series.get(target_time)
            row = feature_series.get(target_time)
            if actual is None or row is None:
                continue

            prediction_row = dataclasses.replace(row, horizon_hours=h)
            predicted = model.predict([prediction_row])[0].value_g_per_kwh
            day = day_of_horizon(h)
            model_errors_by_day.setdefault(day, []).append(abs(predicted - actual))

            naive_reference = actual_series.get(target_time - dt.timedelta(hours=24))
            if naive_reference is not None:
                naive_errors_by_day.setdefault(day, []).append(abs(naive_reference - actual))

    results = []
    for day in sorted(model_errors_by_day):
        model_errors = model_errors_by_day[day]
        naive_errors = naive_errors_by_day.get(day, [])
        if not naive_errors:
            continue
        results.append(
            DayMetric(
                day=day,
                model_mae=statistics.fmean(model_errors),
                naive_mae=statistics.fmean(naive_errors),
                n=len(model_errors),
            )
        )
    return results
