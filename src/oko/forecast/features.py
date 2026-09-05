"""Feature engineering for the carbon-intensity forecast model.

The model is trained on **historical, realised** conditions (actual
generation mix, actual load — everything Phase 2's data already gives us)
but has to run inference on a **120-hour weather forecast**, because
ENTSO-E's own day-ahead load and wind/solar-generation forecasts only
reach ~24-48h out (the day-ahead market horizon) — nowhere near OKO's
5-day target. NOAA GFS is the only one of the project's three data sources
that actually forecasts 120 hours ahead, which is exactly why it exists in
this pipeline (see ``oko.fetchers.noaa_gfs``).

That gap is bridged with a *perfect prognosis* approach, a standard
technique in operational weather-driven forecasting: train the model on
engineered features computed from what actually happened, then feed it
structurally equivalent features computed from the weather forecast at
inference time, accepting that NOAA's forecast error (which grows with
lead time) becomes part of the model's effective input noise. That's also
*why* forecast confidence should degrade with horizon — the input itself
gets less certain, not just the model's extrapolation.

To make the two feature paths genuinely comparable (not just similarly
named), both are expressed as the same normalised quantity:
``residual_load_share`` — the estimated fraction of demand *not* covered
by variable renewables (wind + solar), roughly in ``[0, 1]``. Low values
mean "renewables-heavy, likely low carbon intensity"; high values mean
"renewables-light, likely high carbon intensity". Training computes it
from actual MW; inference approximates it from raw NOAA wind speed /
DSWRF via simple, clearly-labelled monotonic proxies (no claim to be an
accurate power-curve/capacity model — that's out of scope for the MVP).
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace

from oko.fetchers.noaa_gfs import WeatherPoint

#: How far back the day-ahead price autocorrelation feature looks --
#: chosen as 168h (same weekday+hour, one week prior) rather than 24h
#: because 168h > OKO's 120h forecast horizon, so the lookup always
#: resolves to a real, already-observed price -- never a value that
#: would itself need to be forecast recursively.
PRICE_LAG_HOURS = 168

#: Wind speed (m/s) at which the proxy signal saturates — roughly a
#: turbine's rated wind speed, above which additional speed adds little
#: extra output before cut-out. Not a real capacity/power curve; a
#: deliberately simple normalisation constant for the MVP proxy.
WIND_SATURATION_MS = 12.0

#: DSWRF (W/m²) at which the solar proxy signal saturates — roughly clear
#: -sky midday irradiance in central Europe.
DSWRF_SATURATION_WM2 = 800.0


@dataclass(frozen=True, slots=True)
class FeatureRow:
    """One hour's model input features.

    Attributes:
        timestamp: UTC hour this row describes.
        hour_sin, hour_cos: cyclical encoding of hour-of-day (period 24).
        dow_sin, dow_cos: cyclical encoding of day-of-week (period 7).
        month_sin, month_cos: cyclical encoding of month (period 12).
        residual_load_share: see module docstring — low means
            renewables-heavy.
        horizon_hours: hours ahead of the forecast's reference time this
            row is for; ``0`` for historical training rows (not a
            forecast at all, so there's no horizon to speak of).
        price_lag_168h: the day-ahead price ``PRICE_LAG_HOURS`` before
            this row's timestamp, if known -- ``PriceModel``-only feature
            (see ``PRICE_FEATURE_COLUMNS``); ``None``/unset for every
            other model, and for price rows where that lag isn't yet in
            history (bootstrap window).
    """

    timestamp: dt.datetime
    hour_sin: float
    hour_cos: float
    dow_sin: float
    dow_cos: float
    month_sin: float
    month_cos: float
    residual_load_share: float
    horizon_hours: int
    price_lag_168h: float | None = None

    def as_dict(self) -> dict[str, float]:
        """Return the model-input columns (excludes ``timestamp``) as a plain dict.

        ``price_lag_168h`` is emitted as NaN when unset -- LightGBM
        handles missing values natively, and NaN (unlike ``0.0``) can't
        be mistaken for a real observed price.
        """
        return {
            "hour_sin": self.hour_sin,
            "hour_cos": self.hour_cos,
            "dow_sin": self.dow_sin,
            "dow_cos": self.dow_cos,
            "month_sin": self.month_sin,
            "month_cos": self.month_cos,
            "residual_load_share": self.residual_load_share,
            "horizon_hours": float(self.horizon_hours),
            "price_lag_168h": (
                self.price_lag_168h if self.price_lag_168h is not None else math.nan
            ),
        }


#: The model-input column names, in the fixed order LightGBM is trained
#: and queried with. Keep in sync with ``FeatureRow.as_dict``.
FEATURE_COLUMNS: tuple[str, ...] = (
    "hour_sin",
    "hour_cos",
    "dow_sin",
    "dow_cos",
    "month_sin",
    "month_cos",
    "residual_load_share",
    "horizon_hours",
)

#: ``PriceModel``'s column set -- ``FEATURE_COLUMNS`` plus the price-lag
#: autocorrelation feature (see ``with_price_lag``).
PRICE_FEATURE_COLUMNS: tuple[str, ...] = (*FEATURE_COLUMNS, "price_lag_168h")


def _cyclical_encoding(value: float, period: float) -> tuple[float, float]:
    """Encode a periodic value as ``(sin, cos)`` so e.g. hour 23 sits next to hour 0."""
    angle = 2 * math.pi * value / period
    return math.sin(angle), math.cos(angle)


def _calendar_features(timestamp: dt.datetime) -> tuple[float, float, float, float, float, float]:
    hour_sin, hour_cos = _cyclical_encoding(timestamp.hour, 24)
    dow_sin, dow_cos = _cyclical_encoding(timestamp.weekday(), 7)
    month_sin, month_cos = _cyclical_encoding(timestamp.month - 1, 12)
    return hour_sin, hour_cos, dow_sin, dow_cos, month_sin, month_cos


def residual_load_share_from_production(
    production_by_category: Mapping[str, float], load_mw: float
) -> float | None:
    """Compute the training-time renewable-adjusted load share from actual data.

    Args:
        production_by_category: that hour's actual generation mix, MW
            (as returned by ``oko.fetchers.entsoe.fetch_production``).
        load_mw: that hour's actual total system load, MW.

    Returns:
        ``1 - (wind + solar) / load``, clipped to ``[0, 1]``, or ``None``
        if ``load_mw`` isn't usable (zero/negative — can't form a ratio).
    """
    if load_mw <= 0:
        return None
    renewable_mw = production_by_category.get("wind", 0.0) + production_by_category.get(
        "solar", 0.0
    )
    share = 1.0 - max(renewable_mw, 0.0) / load_mw
    return min(max(share, 0.0), 1.0)


def residual_load_share_from_weather(wind_speed_10m_ms: float, dswrf_wm2: float) -> float:
    """Approximate the same normalised quantity from a NOAA GFS forecast point.

    Deliberately simple, monotonic proxies — not a power-curve or
    capacity-factor model (that would need per-turbine/per-panel data
    OKO doesn't have in the MVP):

    - wind proxy: wind speed cubed (power in freestream air scales with
      ``v^3``, which is the standard qualitative shape of a turbine power
      curve below rated speed) then clipped once it exceeds
      ``WIND_SATURATION_MS``, normalised to ``[0, 1]``.
    - solar proxy: DSWRF linearly clipped at ``DSWRF_SATURATION_WM2``,
      normalised to ``[0, 1]``.

    Args:
        wind_speed_10m_ms: DE-averaged 10 m wind speed, m/s.
        dswrf_wm2: DE-averaged downward shortwave radiation flux, W/m².

    Returns:
        The proxy ``residual_load_share``, in ``[0, 1]``.
    """
    wind_proxy = min(wind_speed_10m_ms, WIND_SATURATION_MS) ** 3 / WIND_SATURATION_MS**3
    solar_proxy = min(max(dswrf_wm2, 0.0), DSWRF_SATURATION_WM2) / DSWRF_SATURATION_WM2
    # Weighted 65/35 toward wind: wind is the larger and steadier share of
    # DE's variable renewable generation. A fixed, documented weighting —
    # not fit from data — consistent with the MVP's "no full capacity
    # model" boundary.
    renewable_proxy = 0.65 * wind_proxy + 0.35 * solar_proxy
    return min(max(1.0 - renewable_proxy, 0.0), 1.0)


def with_price_lag(
    rows: Sequence[FeatureRow], price_by_hour: Mapping[dt.datetime, float]
) -> list[FeatureRow]:
    """Return copies of ``rows`` with ``price_lag_168h`` filled from ``price_by_hour``.

    Args:
        rows: feature rows to attach a lag to (training or forecast rows).
        price_by_hour: observed day-ahead prices, hour -> EUR/MWh --
            typically every price already persisted in history, so the
            lookup at ``row.timestamp - PRICE_LAG_HOURS`` hours resolves
            whenever that hour has been observed.

    Returns:
        One ``FeatureRow`` per input row, same order, with
        ``price_lag_168h`` set (or left ``None`` if that hour isn't in
        ``price_by_hour``).
    """
    lag = dt.timedelta(hours=PRICE_LAG_HOURS)
    return [replace(row, price_lag_168h=price_by_hour.get(row.timestamp - lag)) for row in rows]


def build_training_features(
    production_by_hour: Mapping[dt.datetime, Mapping[str, float]],
    load_by_hour: Mapping[dt.datetime, float],
) -> list[FeatureRow]:
    """Build training-time feature rows from historical, realised data.

    Args:
        production_by_hour: hour -> that hour's actual generation mix, MW.
        load_by_hour: hour -> that hour's actual total load, MW.

    Returns:
        One ``FeatureRow`` per hour present in both inputs with a usable
        load value, sorted by timestamp. ``horizon_hours`` is always 0
        (these are observations, not a forecast at any lead time).
    """
    rows = []
    for timestamp in sorted(set(production_by_hour) & set(load_by_hour)):
        share = residual_load_share_from_production(
            production_by_hour[timestamp], load_by_hour[timestamp]
        )
        if share is None:
            continue
        hour_sin, hour_cos, dow_sin, dow_cos, month_sin, month_cos = _calendar_features(timestamp)
        rows.append(
            FeatureRow(
                timestamp=timestamp,
                hour_sin=hour_sin,
                hour_cos=hour_cos,
                dow_sin=dow_sin,
                dow_cos=dow_cos,
                month_sin=month_sin,
                month_cos=month_cos,
                residual_load_share=share,
                horizon_hours=0,
            )
        )
    return rows


def build_forecast_features(
    weather_points: Sequence[WeatherPoint], reference_time: dt.datetime
) -> list[FeatureRow]:
    """Build inference-time feature rows from a NOAA GFS forecast.

    Args:
        weather_points: hourly DE-averaged weather forecast, e.g. from
            ``oko.fetchers.noaa_gfs.fetch_forecast``.
        reference_time: the forecast's issue time (used to compute each
            point's ``horizon_hours``); typically the GFS cycle time.

    Returns:
        One ``FeatureRow`` per weather point, sorted by timestamp.
    """
    rows = []
    for point in sorted(weather_points, key=lambda p: p.valid_time):
        hour_sin, hour_cos, dow_sin, dow_cos, month_sin, month_cos = _calendar_features(
            point.valid_time
        )
        horizon = round((point.valid_time - reference_time).total_seconds() / 3600)
        rows.append(
            FeatureRow(
                timestamp=point.valid_time,
                hour_sin=hour_sin,
                hour_cos=hour_cos,
                dow_sin=dow_sin,
                dow_cos=dow_cos,
                month_sin=month_sin,
                month_cos=month_cos,
                residual_load_share=residual_load_share_from_weather(
                    point.wind_speed_10m_ms, point.dswrf_wm2
                ),
                horizon_hours=horizon,
            )
        )
    return rows
