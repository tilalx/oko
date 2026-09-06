"""Deterministic tests for feature engineering, the LightGBM model, and the backtest.

No network access — the model tests train tiny real LightGBM models on
synthetic, deterministic data (fast: well under a second each).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import math
from pathlib import Path

import pytest

from oko.fetchers.noaa_gfs import WeatherPoint
from oko.forecast import backtest, features, model

HOUR = dt.datetime(2026, 8, 31, 12, tzinfo=dt.UTC)


# --------------------------------------------------------------------------
# features.py
# --------------------------------------------------------------------------


def test_cyclical_encoding_wraps_around() -> None:
    sin0, cos0 = features._cyclical_encoding(0, 24)
    sin24, cos24 = features._cyclical_encoding(24, 24)
    assert sin0 == pytest.approx(sin24, abs=1e-9)
    assert cos0 == pytest.approx(cos24, abs=1e-9)


def test_residual_load_share_from_production_basic() -> None:
    # 1000 MW load, 400 MW renewables -> 0.6 residual share.
    share = features.residual_load_share_from_production(
        {"wind": 300.0, "solar": 100.0, "coal": 600.0}, load_mw=1000.0
    )
    assert share == pytest.approx(0.6)


def test_residual_load_share_from_production_none_for_bad_load() -> None:
    assert features.residual_load_share_from_production({"wind": 100.0}, load_mw=0.0) is None
    assert features.residual_load_share_from_production({"wind": 100.0}, load_mw=-5.0) is None


def test_residual_load_share_from_production_clips_to_unit_interval() -> None:
    # Renewables exceeding load (net exporter hour) must not go negative.
    share = features.residual_load_share_from_production({"wind": 5000.0}, load_mw=1000.0)
    assert share == pytest.approx(0.0)


def test_residual_load_share_from_weather_extremes() -> None:
    calm_dark = features.residual_load_share_from_weather(wind_speed_10m_ms=0.0, dswrf_wm2=0.0)
    windy_sunny = features.residual_load_share_from_weather(
        wind_speed_10m_ms=features.WIND_SATURATION_MS, dswrf_wm2=features.DSWRF_SATURATION_WM2
    )
    assert calm_dark == pytest.approx(1.0)
    assert windy_sunny == pytest.approx(0.0)


def test_residual_load_share_from_weather_saturates_below_cutout() -> None:
    at_saturation = features.residual_load_share_from_weather(
        features.WIND_SATURATION_MS, features.DSWRF_SATURATION_WM2
    )
    # Still below WIND_CUTOUT_MS once extrapolated to hub height -> same
    # saturated (max-output) proxy as exactly at WIND_SATURATION_MS.
    a_bit_more = features.residual_load_share_from_weather(
        features.WIND_SATURATION_MS * 1.2, features.DSWRF_SATURATION_WM2 * 3
    )
    assert a_bit_more == pytest.approx(at_saturation)


def test_residual_load_share_from_weather_cuts_out_in_storm() -> None:
    # A wind speed whose hub-height extrapolation exceeds WIND_CUTOUT_MS
    # must NOT keep implying near-max wind output -- real turbines
    # feather/shut down, so the wind proxy should drop to 0.
    calm = features.residual_load_share_from_weather(0.0, 0.0)
    storm = features.residual_load_share_from_weather(features.WIND_SATURATION_MS * 3, 0.0)
    assert storm == pytest.approx(calm)


def test_residual_load_share_from_weather_uses_capacity_weighting_when_available() -> None:
    # Same weather, but installed capacity is almost all solar -> the
    # blended proxy should lean toward the solar signal instead of the
    # fixed 65/35 default.
    wind_only_weather = (features.WIND_SATURATION_MS, 0.0)  # max wind, no sun
    default_weighting = features.residual_load_share_from_weather(*wind_only_weather)
    solar_heavy_capacity = features.residual_load_share_from_weather(
        *wind_only_weather, wind_capacity_mw=10.0, solar_capacity_mw=990.0
    )
    # Default weighting gives wind (which is maxed) 65% weight -> lower
    # residual share than when wind capacity is negligible.
    assert solar_heavy_capacity > default_weighting


def test_feature_columns_matches_as_dict_keys() -> None:
    row = features.FeatureRow(
        timestamp=HOUR,
        hour_sin=0.1,
        hour_cos=0.2,
        dow_sin=0.3,
        dow_cos=0.4,
        month_sin=0.5,
        month_cos=0.6,
        residual_load_share=0.7,
        horizon_hours=3,
    )
    assert tuple(row.as_dict().keys()) == features.PRICE_FEATURE_COLUMNS
    assert tuple(row.as_dict().keys())[: len(features.FEATURE_COLUMNS)] == features.FEATURE_COLUMNS


def test_build_training_features_joins_and_skips_missing_hours() -> None:
    hour0, hour1, hour2 = HOUR, HOUR + dt.timedelta(hours=1), HOUR + dt.timedelta(hours=2)
    production = {
        hour0: {"wind": 300.0, "coal": 700.0},
        hour1: {"wind": 100.0, "coal": 900.0},
        hour2: {"wind": 500.0, "coal": 500.0},  # no matching load -> must be skipped
    }
    load = {hour0: 1000.0, hour1: 1000.0}

    rows = features.build_training_features(production, load)

    assert [r.timestamp for r in rows] == [hour0, hour1]
    assert all(r.horizon_hours == 0 for r in rows)
    assert rows[0].residual_load_share == pytest.approx(0.7)
    assert rows[1].residual_load_share == pytest.approx(0.9)


def test_build_forecast_features_computes_horizon_and_sorts() -> None:
    reference = HOUR
    points = [
        WeatherPoint(
            valid_time=HOUR + dt.timedelta(hours=2),
            wind_speed_10m_ms=5.0,
            dswrf_wm2=100.0,
            temperature_2m_c=15.0,
        ),
        WeatherPoint(
            valid_time=HOUR + dt.timedelta(hours=1),
            wind_speed_10m_ms=5.0,
            dswrf_wm2=100.0,
            temperature_2m_c=15.0,
        ),
    ]
    rows = features.build_forecast_features(points, reference)
    assert [r.horizon_hours for r in rows] == [1, 2]
    assert [r.timestamp for r in rows] == [
        HOUR + dt.timedelta(hours=1),
        HOUR + dt.timedelta(hours=2),
    ]


def test_with_price_lag_fills_from_matching_hour() -> None:
    row = features.FeatureRow(
        timestamp=HOUR,
        hour_sin=0.1,
        hour_cos=0.2,
        dow_sin=0.3,
        dow_cos=0.4,
        month_sin=0.5,
        month_cos=0.6,
        residual_load_share=0.7,
        horizon_hours=0,
    )
    price_by_hour = {HOUR - dt.timedelta(hours=features.PRICE_LAG_HOURS): 42.5}

    [lagged] = features.with_price_lag([row], price_by_hour)

    assert lagged.price_lag_168h == 42.5
    # Every other field is untouched.
    assert lagged.timestamp == row.timestamp
    assert lagged.residual_load_share == row.residual_load_share


def test_with_price_lag_leaves_none_when_hour_missing() -> None:
    row = features.FeatureRow(
        timestamp=HOUR,
        hour_sin=0.1,
        hour_cos=0.2,
        dow_sin=0.3,
        dow_cos=0.4,
        month_sin=0.5,
        month_cos=0.6,
        residual_load_share=0.7,
        horizon_hours=0,
    )

    [lagged] = features.with_price_lag([row], {})

    assert lagged.price_lag_168h is None
    assert math.isnan(lagged.as_dict()["price_lag_168h"])


# --------------------------------------------------------------------------
# model.py
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("horizon", "expected"),
    [(1, "high"), (24, "high"), (25, "medium"), (72, "medium"), (73, "low"), (120, "low")],
)
def test_confidence_for_horizon(horizon: int, expected: str) -> None:
    assert model.confidence_for_horizon(horizon) == expected


def test_confidence_for_horizon_accepts_custom_thresholds() -> None:
    # A caller with measured per-horizon error (e.g. from a backtest) can
    # override the default fixed 24h/72h buckets.
    assert model.confidence_for_horizon(10, high_max_hours=6, medium_max_hours=20) == "medium"
    assert model.confidence_for_horizon(6, high_max_hours=6, medium_max_hours=20) == "high"
    assert model.confidence_for_horizon(21, high_max_hours=6, medium_max_hours=20) == "low"


def _synthetic_rows(n: int) -> tuple[list[features.FeatureRow], list[float]]:
    rows = []
    targets = []
    for i in range(n):
        share = (i % 10) / 10.0
        rows.append(
            features.FeatureRow(
                timestamp=HOUR + dt.timedelta(hours=i),
                hour_sin=math.sin(i),
                hour_cos=math.cos(i),
                dow_sin=0.0,
                dow_cos=0.0,
                month_sin=0.0,
                month_cos=0.0,
                residual_load_share=share,
                horizon_hours=0,
            )
        )
        targets.append(100.0 + share * 500.0)
    return rows, targets


def test_train_rejects_mismatched_lengths() -> None:
    rows, targets = _synthetic_rows(10)
    with pytest.raises(ValueError, match="length mismatch"):
        model.CarbonIntensityModel.train(rows, targets[:-1])


def test_train_rejects_empty_dataset() -> None:
    with pytest.raises(ValueError, match="empty"):
        model.CarbonIntensityModel.train([], [])


def test_predict_empty_input_returns_empty() -> None:
    rows, targets = _synthetic_rows(50)
    trained = model.CarbonIntensityModel.train(rows, targets)
    assert trained.predict([]) == []


def test_train_uses_early_stopping_and_does_not_use_every_boost_round() -> None:
    # Enough rows to trigger the chronological-split path (see
    # MIN_ROWS_FOR_EARLY_STOPPING); a clean, easily-learnable relationship
    # should converge and stop well before MAX_NUM_BOOST_ROUND.
    rows, targets = _synthetic_rows(500)
    trained = model.CarbonIntensityModel.train(rows, targets)
    assert trained._booster.best_iteration < model.MAX_NUM_BOOST_ROUND


def test_train_falls_back_to_fixed_rounds_below_early_stopping_threshold() -> None:
    rows, targets = _synthetic_rows(model.MIN_ROWS_FOR_EARLY_STOPPING - 1)
    # Must not raise despite too few rows for a validation split.
    trained = model.CarbonIntensityModel.train(rows, targets)
    assert trained.predict(rows[:1])[0].value_g_per_kwh >= 0.0


def test_train_predict_learns_the_relationship() -> None:
    rows, targets = _synthetic_rows(500)
    trained = model.CarbonIntensityModel.train(rows, targets)

    low_share_row = dataclasses.replace(rows[0], residual_load_share=0.0, horizon_hours=1)
    high_share_row = dataclasses.replace(rows[0], residual_load_share=0.9, horizon_hours=1)
    predictions = trained.predict([low_share_row, high_share_row])

    assert predictions[0].value_g_per_kwh < predictions[1].value_g_per_kwh
    assert predictions[0].confidence == "high"


def test_predict_clamps_negative_predictions_to_zero() -> None:
    class _StubBooster:
        def predict(self, matrix: object) -> list[float]:
            return [-5.0, 10.0]

    stub_model = model.CarbonIntensityModel(_StubBooster())  # type: ignore[arg-type]
    row = features.FeatureRow(
        timestamp=HOUR,
        hour_sin=0,
        hour_cos=0,
        dow_sin=0,
        dow_cos=0,
        month_sin=0,
        month_cos=0,
        residual_load_share=0.5,
        horizon_hours=1,
    )
    predictions = stub_model.predict([row, row])
    assert predictions[0].value_g_per_kwh == 0.0
    assert predictions[1].value_g_per_kwh == 10.0


def test_save_load_roundtrip_produces_identical_predictions(tmp_path: Path) -> None:
    rows, targets = _synthetic_rows(300)
    trained = model.CarbonIntensityModel.train(rows, targets)
    query_row = dataclasses.replace(rows[0], horizon_hours=10)

    before = trained.predict([query_row])[0].value_g_per_kwh

    save_path = tmp_path / "model.txt"
    trained.save(save_path)
    loaded = model.CarbonIntensityModel.load(save_path)
    after = loaded.predict([query_row])[0].value_g_per_kwh

    assert before == pytest.approx(after)


def _synthetic_price_rows(n: int) -> tuple[list[features.FeatureRow], list[float]]:
    rows, _ = _synthetic_rows(n)
    # A relationship with a genuinely negative range, unlike carbon
    # intensity -- price can and does go negative on the day-ahead market.
    targets = [-50.0 + row.residual_load_share * 200.0 for row in rows]
    return rows, targets


def test_price_model_train_rejects_mismatched_lengths() -> None:
    rows, targets = _synthetic_price_rows(10)
    with pytest.raises(ValueError, match="length mismatch"):
        model.PriceModel.train(rows, targets[:-1])


def test_price_model_train_rejects_empty_dataset() -> None:
    with pytest.raises(ValueError, match="empty"):
        model.PriceModel.train([], [])


def test_price_model_predict_empty_input_returns_empty() -> None:
    rows, targets = _synthetic_price_rows(50)
    trained = model.PriceModel.train(rows, targets)
    assert trained.predict([]) == []


def test_price_model_predict_does_not_clamp_negative_values() -> None:
    class _StubBooster:
        def predict(self, matrix: object) -> list[float]:
            return [-5.0, 10.0]

    stub_model = model.PriceModel(_StubBooster())  # type: ignore[arg-type]
    row = features.FeatureRow(
        timestamp=HOUR,
        hour_sin=0,
        hour_cos=0,
        dow_sin=0,
        dow_cos=0,
        month_sin=0,
        month_cos=0,
        residual_load_share=0.5,
        horizon_hours=1,
    )
    predictions = stub_model.predict([row, row])
    assert predictions[0].price_eur_per_mwh == -5.0
    assert predictions[1].price_eur_per_mwh == 10.0


def test_price_model_train_predict_learns_the_relationship() -> None:
    rows, targets = _synthetic_price_rows(500)
    trained = model.PriceModel.train(rows, targets)

    low_share_row = dataclasses.replace(rows[0], residual_load_share=0.0, horizon_hours=1)
    high_share_row = dataclasses.replace(rows[0], residual_load_share=0.9, horizon_hours=1)
    predictions = trained.predict([low_share_row, high_share_row])

    assert predictions[0].price_eur_per_mwh < predictions[1].price_eur_per_mwh
    assert predictions[0].confidence == "high"


def test_price_model_save_load_roundtrip_produces_identical_predictions(tmp_path: Path) -> None:
    rows, targets = _synthetic_price_rows(300)
    trained = model.PriceModel.train(rows, targets)
    query_row = dataclasses.replace(rows[0], horizon_hours=10)

    before = trained.predict([query_row])[0].price_eur_per_mwh

    save_path = tmp_path / "price_model.txt"
    trained.save(save_path)
    loaded = model.PriceModel.load(save_path)
    after = loaded.predict([query_row])[0].price_eur_per_mwh

    assert before == pytest.approx(after)


def _synthetic_breakdowns(n: int) -> tuple[list[features.FeatureRow], list[dict[str, float]]]:
    rows, _ = _synthetic_rows(n)
    breakdowns = [
        {
            "wind": 20.0 + row.residual_load_share * 10.0,
            "coal": 80.0 - row.residual_load_share * 10.0,
        }
        for row in rows
    ]
    return rows, breakdowns


def test_breakdown_model_train_rejects_mismatched_lengths() -> None:
    rows, breakdowns = _synthetic_breakdowns(10)
    with pytest.raises(ValueError, match="length mismatch"):
        model.BreakdownModel.train(rows, breakdowns[:-1])


def test_breakdown_model_train_rejects_empty_dataset() -> None:
    with pytest.raises(ValueError, match="empty"):
        model.BreakdownModel.train([], [])


def test_breakdown_model_predict_empty_input_returns_empty() -> None:
    rows, breakdowns = _synthetic_breakdowns(50)
    trained = model.BreakdownModel.train(rows, breakdowns, categories=("wind", "coal"))
    assert trained.predict([]) == []


def test_breakdown_model_predict_sums_to_100_and_covers_every_category() -> None:
    rows, breakdowns = _synthetic_breakdowns(200)
    trained = model.BreakdownModel.train(rows, breakdowns, categories=("wind", "coal", "solar"))

    predictions = trained.predict([dataclasses.replace(rows[0], horizon_hours=1)])

    assert len(predictions) == 1
    percent = predictions[0].power_breakdown_percent
    assert set(percent) == {"wind", "coal", "solar"}
    # "solar" was never in any training breakdown -> its booster predicts ~0.
    assert percent["solar"] == pytest.approx(0.0, abs=1e-6)
    assert sum(percent.values()) == pytest.approx(100.0)


def test_breakdown_model_save_load_roundtrip_produces_identical_predictions(
    tmp_path: Path,
) -> None:
    rows, breakdowns = _synthetic_breakdowns(200)
    trained = model.BreakdownModel.train(rows, breakdowns, categories=("wind", "coal"))
    query_row = dataclasses.replace(rows[0], horizon_hours=5)

    before = trained.predict([query_row])[0].power_breakdown_percent

    save_dir = tmp_path / "breakdown"
    trained.save(save_dir)
    loaded = model.BreakdownModel.load(save_dir, categories=("wind", "coal"))
    after = loaded.predict([query_row])[0].power_breakdown_percent

    assert before == pytest.approx(after)


# --------------------------------------------------------------------------
# backtest.py
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "expected_day"),
    [
        (1, 1),
        (24, 1),
        (25, 2),
        (48, 2),
        (49, 3),
        (72, 3),
        (73, 4),
        (96, 4),
        (97, 5),
        (120, 5),
        (200, 5),
    ],
)
def test_day_of_horizon(hour: int, expected_day: int) -> None:
    assert backtest.day_of_horizon(hour) == expected_day


def test_walk_forward_backtest_model_beats_naive_on_learnable_signal() -> None:
    # Build a long synthetic series where intensity depends purely on
    # residual_load_share, which itself follows a slow ~5-day cycle (not
    # 24h) -> 24h-ago persistence is a poor predictor, but the model
    # (which sees residual_load_share directly) should do much better.
    origin = HOUR
    n_hours = 400
    actual_series: dict[dt.datetime, float] = {}
    feature_series: dict[dt.datetime, features.FeatureRow] = {}
    training_rows = []
    training_targets = []

    for i in range(n_hours):
        ts = origin + dt.timedelta(hours=i)
        share = 0.5 + 0.4 * math.sin(2 * math.pi * i / (5 * 24))
        value = 100.0 + share * 500.0
        row = features.FeatureRow(
            timestamp=ts,
            hour_sin=0.0,
            hour_cos=0.0,
            dow_sin=0.0,
            dow_cos=0.0,
            month_sin=0.0,
            month_cos=0.0,
            residual_load_share=share,
            horizon_hours=0,
        )
        actual_series[ts] = value
        feature_series[ts] = row
        training_rows.append(row)
        training_targets.append(value)

    trained = model.CarbonIntensityModel.train(training_rows, training_targets)

    origins = [origin + dt.timedelta(hours=h) for h in (0, 12, 24, 36, 48)]
    results = backtest.walk_forward_backtest(trained, actual_series, feature_series, origins)

    assert results  # produced at least one day's worth of metrics
    for day_metric in results:
        assert day_metric.model_mae < day_metric.naive_mae
        assert day_metric.n > 0


def test_walk_forward_backtest_skips_hours_missing_from_either_series() -> None:
    origin = HOUR
    actual_series = {origin + dt.timedelta(hours=1): 100.0}
    # No feature row for that hour at all -> should be silently skipped, not raise.
    feature_series: dict[dt.datetime, features.FeatureRow] = {}
    rows, targets = _synthetic_rows(20)
    trained = model.CarbonIntensityModel.train(rows, targets)

    results = backtest.walk_forward_backtest(trained, actual_series, feature_series, [origin])
    assert results == []
