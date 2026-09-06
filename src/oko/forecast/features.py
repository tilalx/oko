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

#: Wind speed (m/s) above which real turbines feather/shut down for
#: structural safety — the proxy must NOT keep implying near-max output
#: during a storm just because it's above ``WIND_SATURATION_MS``.
WIND_CUTOUT_MS = 25.0

#: DSWRF (W/m²) at which the solar proxy signal saturates — roughly clear
#: -sky midday irradiance in central Europe.
DSWRF_SATURATION_WM2 = 800.0

#: NOAA GFS reports wind at 10 m; modern turbine hubs sit much higher.
#: Extrapolated via the logarithmic wind profile (see
#: ``_wind_speed_at_hub_height``) before feeding the power-curve proxy —
#: near-surface wind systematically understates hub-height wind due to
#: surface friction (wind shear).
WIND_MEASUREMENT_HEIGHT_M = 10.0
TURBINE_HUB_HEIGHT_M = 100.0

#: Surface roughness length (m) for the log-law extrapolation — a
#: generic "open/agricultural terrain with scattered obstacles" value
#: (Davenport/Wieringa classification), not calibrated per zone.
SURFACE_ROUGHNESS_LENGTH_M = 0.1

#: Fallback wind/solar weighting used when installed capacity isn't
#: available (e.g. a zone with no cached ENTSO-E capacity data yet) —
#: the same fixed 65/35 split this module previously always used.
DEFAULT_WIND_WEIGHT = 0.65

#: How far back the carbon-intensity autocorrelation feature looks —
#: same 168h (one week prior) choice and same reason as
#: ``PRICE_LAG_HOURS``: greater than OKO's 120h forecast horizon, so the
#: lookup always resolves to an already-observed hour, never one that
#: would itself need to be forecast recursively.
INTENSITY_LAG_HOURS = 168

#: A second, shorter intensity autocorrelation lookback -- daily rather
#: than weekly autocorrelation. Unlike ``INTENSITY_LAG_HOURS``, this is
#: *not* always greater than the 120h forecast horizon, so it resolves
#: to NaN (handled natively by LightGBM) for any forecast row more than
#: 24h out -- still populated for every training row, and for exactly
#: the short-horizon forecast rows where the model is already most
#: trusted (see ``CONFIDENCE_HIGH_MAX_HOURS``).
INTENSITY_LAG_HOURS_SHORT = 24

#: Degree-day "balance point" temperature (°C) — the standard reference
#: below/above which heating/cooling demand is assumed to start rising.
#: A generic value, not calibrated per zone.
TEMPERATURE_BALANCE_POINT_C = 18.0

#: Degree-day deviation (°C from the balance point) at which the demand
#: adjustment below saturates — deliberately simple, uncalibrated MVP
#: proxy, same spirit as ``WIND_SATURATION_MS``/``DSWRF_SATURATION_WM2``.
TEMPERATURE_DEGREE_DAY_SATURATION_C = 15.0

#: Maximum upward adjustment to the weather-proxy residual load share
#: from temperature-driven demand (heating/cooling), applied at/above
#: ``TEMPERATURE_DEGREE_DAY_SATURATION_C`` degree-days — bounded so
#: temperature can only push the wind/solar-driven proxy up, never
#: invert or dominate it.
TEMPERATURE_DEMAND_ADJUSTMENT_MAX = 0.15


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
        intensity_lag_24h, intensity_lag_168h: the carbon intensity 24h /
            ``INTENSITY_LAG_HOURS`` (168h) before this row's timestamp,
            if known -- ``CarbonIntensityModel``-only features (see
            ``CARBON_FEATURE_COLUMNS``); ``None``/unset for every other
            model, and for rows where that lag isn't yet in history
            (bootstrap window, or -- for the 24h lag only -- a forecast
            horizon beyond 24h, where the lagged hour is still in the
            future).
        wind_share, solar_share: each variable-renewable source's own,
            unblended contribution -- ``CarbonIntensityModel``-only
            features (see ``CARBON_FEATURE_COLUMNS``) alongside the
            already-blended ``residual_load_share``, letting the model
            learn the wind/solar interaction itself instead of relying
            solely on a fixed capacity-weighted blend. Default ``0.0``
            (not ``None``) since they're always computable, never a
            "not yet available" case like the lag features.
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
    intensity_lag_168h: float | None = None
    intensity_lag_24h: float | None = None
    wind_share: float = 0.0
    solar_share: float = 0.0
    price_lag_168h: float | None = None

    def as_dict(self) -> dict[str, float]:
        """Return the model-input columns (excludes ``timestamp``) as a plain dict.

        ``intensity_lag_24h``/``intensity_lag_168h``/``price_lag_168h``
        are emitted as NaN when unset -- LightGBM handles missing values
        natively, and NaN (unlike ``0.0``) can't be mistaken for a real
        observed value.
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
            "intensity_lag_168h": (
                self.intensity_lag_168h if self.intensity_lag_168h is not None else math.nan
            ),
            "intensity_lag_24h": (
                self.intensity_lag_24h if self.intensity_lag_24h is not None else math.nan
            ),
            "wind_share": self.wind_share,
            "solar_share": self.solar_share,
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

#: ``CarbonIntensityModel``'s column set -- ``FEATURE_COLUMNS`` plus the
#: intensity autocorrelation features (see ``with_intensity_lag``) and
#: the unblended wind/solar shares (see ``wind_share``/``solar_share``).
CARBON_FEATURE_COLUMNS: tuple[str, ...] = (
    *FEATURE_COLUMNS,
    "intensity_lag_168h",
    "intensity_lag_24h",
    "wind_share",
    "solar_share",
)

#: ``PriceModel``'s column set -- ``CARBON_FEATURE_COLUMNS`` plus the
#: price-lag autocorrelation feature (see ``with_price_lag``). Also the
#: full, in-order column set ``FeatureRow.as_dict()`` emits.
PRICE_FEATURE_COLUMNS: tuple[str, ...] = (*CARBON_FEATURE_COLUMNS, "price_lag_168h")


def _cyclical_encoding(value: float, period: float) -> tuple[float, float]:
    """Encode a periodic value as ``(sin, cos)`` so e.g. hour 23 sits next to hour 0."""
    angle = 2 * math.pi * value / period
    return math.sin(angle), math.cos(angle)


def _calendar_features(timestamp: dt.datetime) -> tuple[float, float, float, float, float, float]:
    hour_sin, hour_cos = _cyclical_encoding(timestamp.hour, 24)
    dow_sin, dow_cos = _cyclical_encoding(timestamp.weekday(), 7)
    month_sin, month_cos = _cyclical_encoding(timestamp.month - 1, 12)
    return hour_sin, hour_cos, dow_sin, dow_cos, month_sin, month_cos


def _component_shares_from_production(
    production_by_category: Mapping[str, float], load_mw: float
) -> tuple[float, float] | None:
    """Compute each variable-renewable source's own share of load, unblended.

    Args:
        production_by_category: that hour's actual generation mix, MW
            (as returned by ``oko.fetchers.entsoe.fetch_production``).
        load_mw: that hour's actual total system load, MW.

    Returns:
        ``(wind_mw / load_mw, solar_mw / load_mw)``, each independently
        clipped to ``[0, 1]``, or ``None`` if ``load_mw`` isn't usable
        (zero/negative — can't form a ratio).
    """
    if load_mw <= 0:
        return None
    wind_share = min(max(production_by_category.get("wind", 0.0), 0.0) / load_mw, 1.0)
    solar_share = min(max(production_by_category.get("solar", 0.0), 0.0) / load_mw, 1.0)
    return wind_share, solar_share


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
    shares = _component_shares_from_production(production_by_category, load_mw)
    if shares is None:
        return None
    wind_share, solar_share = shares
    return min(max(1.0 - wind_share - solar_share, 0.0), 1.0)


def _wind_speed_at_hub_height(wind_speed_10m_ms: float) -> float:
    """Extrapolate 10 m wind speed to turbine hub height via the log wind profile.

    ``v(h) = v(10m) * ln(h / z0) / ln(10m / z0)`` — standard boundary-layer
    approximation; not a substitute for real hub-height reanalysis, but a
    documented correction rather than silently using near-surface wind
    (which systematically understates turbine-height wind due to surface
    friction/shear).
    """
    if wind_speed_10m_ms <= 0.0:
        return 0.0
    ratio = math.log(TURBINE_HUB_HEIGHT_M / SURFACE_ROUGHNESS_LENGTH_M) / math.log(
        WIND_MEASUREMENT_HEIGHT_M / SURFACE_ROUGHNESS_LENGTH_M
    )
    return wind_speed_10m_ms * ratio


def _wind_solar_proxies(wind_speed_10m_ms: float, dswrf_wm2: float) -> tuple[float, float]:
    """Raw, unblended wind/solar output proxies from a NOAA GFS forecast point.

    See ``residual_load_share_from_weather`` for the full derivation of
    each proxy. Split out so callers that want the two components
    separately (e.g. ``wind_share``/``solar_share`` in ``FeatureRow``,
    letting the model learn their interaction itself) don't have to
    duplicate this math, and so ``residual_load_share_from_weather``'s
    capacity-weighted blend stays a thin wrapper around the same values.
    """
    hub_height_wind_ms = _wind_speed_at_hub_height(wind_speed_10m_ms)
    wind_proxy = min(hub_height_wind_ms, WIND_SATURATION_MS) ** 3 / WIND_SATURATION_MS**3
    if hub_height_wind_ms > WIND_CUTOUT_MS:
        wind_proxy = 0.0
    solar_proxy = min(max(dswrf_wm2, 0.0), DSWRF_SATURATION_WM2) / DSWRF_SATURATION_WM2
    return wind_proxy, solar_proxy


def _temperature_demand_adjustment(temperature_2m_c: float | None) -> float:
    """Bounded upward nudge to the residual-load-share proxy from temperature.

    Deliberately simple, uncalibrated MVP proxy (see
    ``TEMPERATURE_BALANCE_POINT_C`` / ``TEMPERATURE_DEGREE_DAY_SATURATION_C``):
    the further temperature strays from the balance point in either
    direction, the more heating/cooling demand is assumed to add on top
    of the wind/solar-driven supply proxy -- capped at
    ``TEMPERATURE_DEMAND_ADJUSTMENT_MAX`` so it can only push the result
    up, never invert or dominate the supply-side signal. Returns 0.0 when
    temperature isn't available (backward compatible with callers that
    don't have it).
    """
    if temperature_2m_c is None:
        return 0.0
    degree_days = abs(temperature_2m_c - TEMPERATURE_BALANCE_POINT_C)
    fraction = min(degree_days, TEMPERATURE_DEGREE_DAY_SATURATION_C) / (
        TEMPERATURE_DEGREE_DAY_SATURATION_C
    )
    return fraction * TEMPERATURE_DEMAND_ADJUSTMENT_MAX


def residual_load_share_from_weather(
    wind_speed_10m_ms: float,
    dswrf_wm2: float,
    *,
    wind_capacity_mw: float | None = None,
    solar_capacity_mw: float | None = None,
    temperature_2m_c: float | None = None,
) -> float:
    """Approximate the same normalised quantity from a NOAA GFS forecast point.

    Deliberately simple, monotonic proxies — not a power-curve or
    capacity-factor model (that would need per-turbine/per-panel data
    OKO doesn't have in the MVP):

    - wind proxy: 10 m wind extrapolated to hub height (see
      ``_wind_speed_at_hub_height``), cubed (power in freestream air
      scales with ``v^3``, the standard qualitative shape of a turbine
      power curve below rated speed), clipped once it exceeds
      ``WIND_SATURATION_MS``, normalised to ``[0, 1]``, and forced to 0
      above ``WIND_CUTOUT_MS`` (real turbines feather/shut down in a
      storm rather than keep producing at the saturated maximum).
    - solar proxy: DSWRF linearly clipped at ``DSWRF_SATURATION_WM2``,
      normalised to ``[0, 1]``.

    The two proxies are blended by installed-capacity share when
    ``wind_capacity_mw``/``solar_capacity_mw`` are given (see
    ``oko.emissions.capacity``), instead of a value fixed for all time —
    as a zone's wind/solar buildout mix shifts (e.g. Germany's solar
    capacity growing faster than wind), the same wind speed should
    contribute proportionally less to the blended proxy. Falls back to
    ``DEFAULT_WIND_WEIGHT`` when capacity isn't available (e.g. not yet
    fetched for this zone).

    Args:
        wind_speed_10m_ms: zone-averaged 10 m wind speed, m/s.
        dswrf_wm2: zone-averaged downward shortwave radiation flux, W/m².
        wind_capacity_mw: zone's installed wind capacity, MW, if known.
        solar_capacity_mw: zone's installed solar capacity, MW, if known.
        temperature_2m_c: zone-averaged 2 m air temperature, °C, if
            known -- adds a bounded heating/cooling demand adjustment on
            top of the supply-side proxy (see
            ``_temperature_demand_adjustment``); omitted/``None`` leaves
            behavior unchanged from before this parameter existed.

    Returns:
        The proxy ``residual_load_share``, in ``[0, 1]``.
    """
    wind_proxy, solar_proxy = _wind_solar_proxies(wind_speed_10m_ms, dswrf_wm2)

    wind_weight = DEFAULT_WIND_WEIGHT
    if wind_capacity_mw is not None and solar_capacity_mw is not None:
        wind_capacity_mw = max(wind_capacity_mw, 0.0)
        total_capacity_mw = wind_capacity_mw + max(solar_capacity_mw, 0.0)
        if total_capacity_mw > 0:
            wind_weight = wind_capacity_mw / total_capacity_mw
    renewable_proxy = wind_weight * wind_proxy + (1.0 - wind_weight) * solar_proxy
    share = 1.0 - renewable_proxy + _temperature_demand_adjustment(temperature_2m_c)
    return min(max(share, 0.0), 1.0)


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


def with_intensity_lag(
    rows: Sequence[FeatureRow], intensity_by_hour: Mapping[dt.datetime, float]
) -> list[FeatureRow]:
    """Return copies of ``rows`` with both intensity lags filled from ``intensity_by_hour``.

    Args:
        rows: feature rows to attach lags to (training or forecast rows).
        intensity_by_hour: observed carbon intensity, hour -> g CO2eq/kWh
            -- typically every value already persisted in history, so a
            lookup at ``row.timestamp`` minus either lag resolves
            whenever that hour has been observed.

    Returns:
        One ``FeatureRow`` per input row, same order, with
        ``intensity_lag_24h`` and ``intensity_lag_168h`` each set (or
        left ``None`` if that hour isn't in ``intensity_by_hour`` --
        expected for ``intensity_lag_24h`` on any forecast row more than
        24h past ``reference_time``, see ``INTENSITY_LAG_HOURS_SHORT``).
    """
    lag_168h = dt.timedelta(hours=INTENSITY_LAG_HOURS)
    lag_24h = dt.timedelta(hours=INTENSITY_LAG_HOURS_SHORT)
    return [
        replace(
            row,
            intensity_lag_168h=intensity_by_hour.get(row.timestamp - lag_168h),
            intensity_lag_24h=intensity_by_hour.get(row.timestamp - lag_24h),
        )
        for row in rows
    ]


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
        load_mw = load_by_hour[timestamp]
        shares = _component_shares_from_production(production_by_hour[timestamp], load_mw)
        if shares is None:
            continue
        wind_share, solar_share = shares
        share = min(max(1.0 - wind_share - solar_share, 0.0), 1.0)
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
                wind_share=wind_share,
                solar_share=solar_share,
            )
        )
    return rows


def build_forecast_features(
    weather_points: Sequence[WeatherPoint],
    reference_time: dt.datetime,
    *,
    wind_capacity_mw: float | None = None,
    solar_capacity_mw: float | None = None,
) -> list[FeatureRow]:
    """Build inference-time feature rows from a NOAA GFS forecast.

    Args:
        weather_points: hourly zone-averaged weather forecast, e.g. from
            ``oko.fetchers.noaa_gfs.fetch_forecast``.
        reference_time: the forecast's issue time (used to compute each
            point's ``horizon_hours``); typically the GFS cycle time.
        wind_capacity_mw: zone's installed wind capacity, MW, if known
            (see ``oko.emissions.capacity``) — passed through to
            ``residual_load_share_from_weather`` for every row (capacity
            doesn't change within one forecast horizon).
        solar_capacity_mw: zone's installed solar capacity, MW, if known.

    Returns:
        One ``FeatureRow`` per weather point, sorted by timestamp.
    """
    rows = []
    for point in sorted(weather_points, key=lambda p: p.valid_time):
        hour_sin, hour_cos, dow_sin, dow_cos, month_sin, month_cos = _calendar_features(
            point.valid_time
        )
        horizon = round((point.valid_time - reference_time).total_seconds() / 3600)
        wind_proxy, solar_proxy = _wind_solar_proxies(point.wind_speed_10m_ms, point.dswrf_wm2)
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
                    point.wind_speed_10m_ms,
                    point.dswrf_wm2,
                    wind_capacity_mw=wind_capacity_mw,
                    solar_capacity_mw=solar_capacity_mw,
                    temperature_2m_c=point.temperature_2m_c,
                ),
                horizon_hours=horizon,
                wind_share=wind_proxy,
                solar_share=solar_proxy,
            )
        )
    return rows
