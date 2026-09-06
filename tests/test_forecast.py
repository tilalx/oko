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
    assert (
        tuple(row.as_dict().keys())[: len(features.CARBON_FEATURE_COLUMNS)]
        == features.CARBON_FEATURE_COLUMNS
    )


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


def test_build_training_features_populates_wind_and_solar_shares() -> None:
    hour0 = HOUR
    production = {hour0: {"wind": 300.0, "solar": 100.0, "coal": 600.0}}
    load = {hour0: 1000.0}

    [row] = features.build_training_features(production, load)

    assert row.wind_share == pytest.approx(0.3)
    assert row.solar_share == pytest.approx(0.1)


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


def test_build_forecast_features_populates_wind_and_solar_shares() -> None:
    point = WeatherPoint(
        valid_time=HOUR + dt.timedelta(hours=1),
        wind_speed_10m_ms=features.WIND_SATURATION_MS,
        dswrf_wm2=0.0,
        temperature_2m_c=15.0,
    )
    [row] = features.build_forecast_features([point], HOUR)

    assert row.wind_share == pytest.approx(1.0)
    assert row.solar_share == pytest.approx(0.0)


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


def test_with_intensity_lag_fills_from_matching_hour() -> None:
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
    intensity_by_hour = {HOUR - dt.timedelta(hours=features.INTENSITY_LAG_HOURS): 300.0}

    [lagged] = features.with_intensity_lag([row], intensity_by_hour)

    assert lagged.intensity_lag_168h == 300.0
    assert lagged.timestamp == row.timestamp
    assert lagged.residual_load_share == row.residual_load_share


def test_with_intensity_lag_leaves_none_when_hour_missing() -> None:
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

    [lagged] = features.with_intensity_lag([row], {})

    assert lagged.intensity_lag_168h is None
    assert math.isnan(lagged.as_dict()["intensity_lag_168h"])


def test_with_intensity_lag_fills_24h_lag_from_matching_hour() -> None:
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
    intensity_by_hour = {HOUR - dt.timedelta(hours=features.INTENSITY_LAG_HOURS_SHORT): 250.0}

    [lagged] = features.with_intensity_lag([row], intensity_by_hour)

    assert lagged.intensity_lag_24h == 250.0


def test_with_intensity_lag_24h_none_when_hour_missing() -> None:
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

    [lagged] = features.with_intensity_lag([row], {})

    assert lagged.intensity_lag_24h is None
    assert math.isnan(lagged.as_dict()["intensity_lag_24h"])


def test_with_intensity_lag_fills_both_lags_independently() -> None:
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
    intensity_by_hour = {
        HOUR - dt.timedelta(hours=features.INTENSITY_LAG_HOURS_SHORT): 250.0,
        HOUR - dt.timedelta(hours=features.INTENSITY_LAG_HOURS): 300.0,
    }

    [lagged] = features.with_intensity_lag([row], intensity_by_hour)

    assert lagged.intensity_lag_24h == 250.0
    assert lagged.intensity_lag_168h == 300.0


def test_residual_load_share_from_weather_temperature_none_unchanged() -> None:
    without_temp = features.residual_load_share_from_weather(5.0, 200.0)
    with_none_temp = features.residual_load_share_from_weather(5.0, 200.0, temperature_2m_c=None)
    assert without_temp == pytest.approx(with_none_temp)


def test_residual_load_share_from_weather_cold_raises_share() -> None:
    baseline = features.residual_load_share_from_weather(5.0, 200.0)
    cold = features.residual_load_share_from_weather(5.0, 200.0, temperature_2m_c=-5.0)
    assert cold > baseline


def test_residual_load_share_from_weather_hot_raises_share() -> None:
    baseline = features.residual_load_share_from_weather(5.0, 200.0)
    hot = features.residual_load_share_from_weather(5.0, 200.0, temperature_2m_c=35.0)
    assert hot > baseline


def test_residual_load_share_from_weather_at_balance_point_unchanged() -> None:
    baseline = features.residual_load_share_from_weather(5.0, 200.0)
    at_balance = features.residual_load_share_from_weather(
        5.0, 200.0, temperature_2m_c=features.TEMPERATURE_BALANCE_POINT_C
    )
    assert at_balance == pytest.approx(baseline)


def test_residual_load_share_from_weather_temperature_adjustment_is_bounded() -> None:
    extreme_cold = features.residual_load_share_from_weather(
        features.WIND_SATURATION_MS, features.DSWRF_SATURATION_WM2, temperature_2m_c=-100.0
    )
    assert extreme_cold <= 1.0


# --------------------------------------------------------------------------
# model.py
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("horizon", "expected"),
    [(1, "high"), (24, "high"), (25, "medium"), (72, "medium"), (73, "low"), (120, "low")],
)
def test_confidence_for_horizon(horizon: int, expected: str) -> None:
    assert model.confidence_for_horizon(horizon) == expected


def test_derive_confidence_thresholds_empty() -> None:
    assert backtest.derive_confidence_thresholds([]) is None


def test_derive_confidence_thresholds_model_never_beats_naive() -> None:
    metrics = [
        backtest.DayMetric(day=1, model_mae=100.0, naive_mae=90.0, n=10),
        backtest.DayMetric(day=2, model_mae=110.0, naive_mae=100.0, n=10),
    ]
    assert backtest.derive_confidence_thresholds(metrics) is None


def test_derive_confidence_thresholds_clear_degradation() -> None:
    metrics = [
        backtest.DayMetric(day=1, model_mae=50.0, naive_mae=100.0, n=24),
        backtest.DayMetric(day=2, model_mae=60.0, naive_mae=100.0, n=24),
        backtest.DayMetric(day=3, model_mae=85.0, naive_mae=100.0, n=24),
        backtest.DayMetric(day=4, model_mae=95.0, naive_mae=100.0, n=24),
    ]
    high, medium = backtest.derive_confidence_thresholds(metrics)
    assert medium == 4 * backtest.HOURS_PER_DAY
    assert high in (2 * backtest.HOURS_PER_DAY, 3 * backtest.HOURS_PER_DAY)


def test_confidence_for_horizon_accepts_custom_thresholds() -> None:
    assert model.confidence_for_horizon(10, high_max_hours=6, medium_max_hours=20) == "medium"
    assert model.confidence_for_horizon(6, high_max_hours=6, medium_max_hours=20) == "high"
    assert model.confidence_for_horizon(21, high_max_hours=6, medium_max_hours=20) == "low"


def test_carbon_model_predict_respects_custom_thresholds() -> None:
    rows, targets = _synthetic_rows(50)
    trained = model.CarbonIntensityModel.train(rows, targets)
    query_row = dataclasses.replace(rows[0], horizon_hours=20)

    default_pred = trained.predict([query_row])[0]
    assert default_pred.confidence in ("high", "medium", "low")

    custom_pred = trained.predict([query_row], high_max_hours=10, medium_max_hours=15)[0]
    assert custom_pred.confidence == "low"


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


def test_scaled_lgb_params_small_dataset_uses_default_bounds() -> None:
    params = model._scaled_lgb_params(100)
    assert params["num_leaves"] == 15
    assert params["min_data_in_leaf"] == 20


def test_scaled_lgb_params_increases_capacity_with_more_rows() -> None:
    small = model._scaled_lgb_params(24 * 30)
    medium = model._scaled_lgb_params(24 * 180)
    large = model._scaled_lgb_params(24 * 400)

    assert medium["num_leaves"] > small["num_leaves"]
    assert large["num_leaves"] > medium["num_leaves"]
    assert medium["min_data_in_leaf"] >= small["min_data_in_leaf"]
    assert large["min_data_in_leaf"] >= medium["min_data_in_leaf"]


def test_explicit_params_bypass_scaling() -> None:
    rows, targets = _synthetic_rows(model.MIN_ROWS_FOR_EARLY_STOPPING - 1)
    custom_params = {**model.DEFAULT_LGB_PARAMS, "num_leaves": 3}
    trained = model.CarbonIntensityModel.train(rows, targets, params=custom_params)
    # Just proving it doesn't raise and the explicit params path still works;
    # the booster internals aren't otherwise inspectable from here.
    assert trained.predict(rows[:1])[0].value_g_per_kwh >= 0.0


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


def test_train_predict_uses_intensity_lag_feature() -> None:
    # residual_load_share is noise, uncorrelated with the target -- the
    # only learnable signal is intensity_lag_168h. If the model actually
    # uses CARBON_FEATURE_COLUMNS (which includes the lag), it must pick
    # up on this; if the lag were silently ignored, predictions would be
    # roughly constant regardless of the lag value.
    rows = []
    targets = []
    for i in range(300):
        lag_value = 100.0 + (i % 20) * 20.0
        rows.append(
            dataclasses.replace(
                features.FeatureRow(
                    timestamp=HOUR + dt.timedelta(hours=i),
                    hour_sin=math.sin(i),
                    hour_cos=math.cos(i),
                    dow_sin=0.0,
                    dow_cos=0.0,
                    month_sin=0.0,
                    month_cos=0.0,
                    residual_load_share=0.5,
                    horizon_hours=0,
                ),
                intensity_lag_168h=lag_value,
            )
        )
        targets.append(lag_value)

    trained = model.CarbonIntensityModel.train(rows, targets)

    low_lag_row = dataclasses.replace(rows[0], intensity_lag_168h=100.0, horizon_hours=1)
    high_lag_row = dataclasses.replace(rows[0], intensity_lag_168h=480.0, horizon_hours=1)
    predictions = trained.predict([low_lag_row, high_lag_row])

    assert predictions[0].value_g_per_kwh < predictions[1].value_g_per_kwh


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


def test_train_defaults_to_log_target() -> None:
    rows, targets = _synthetic_rows(300)
    trained = model.CarbonIntensityModel.train(rows, targets)
    assert trained._log_target is True


def test_log_target_predictions_are_sane_and_non_negative() -> None:
    rows, targets = _synthetic_rows(300)
    trained = model.CarbonIntensityModel.train(rows, targets, log_target=True)

    predictions = trained.predict(rows[:5])

    assert all(p.value_g_per_kwh >= 0.0 for p in predictions)
    # Predicted values should stay in a sane range relative to the
    # training targets, not blow up from a mishandled expm1 inversion.
    assert all(p.value_g_per_kwh < max(targets) * 2 for p in predictions)


def test_log_target_false_is_still_supported() -> None:
    rows, targets = _synthetic_rows(300)
    trained = model.CarbonIntensityModel.train(rows, targets, log_target=False)
    assert trained._log_target is False
    predictions = trained.predict(rows[:5])
    assert all(p.value_g_per_kwh >= 0.0 for p in predictions)


def test_save_load_roundtrip_preserves_log_target_flag(tmp_path: Path) -> None:
    rows, targets = _synthetic_rows(300)
    trained = model.CarbonIntensityModel.train(rows, targets, log_target=True)
    query_row = dataclasses.replace(rows[0], horizon_hours=10)
    before = trained.predict([query_row])[0].value_g_per_kwh

    save_path = tmp_path / "model.txt"
    trained.save(save_path)
    loaded = model.CarbonIntensityModel.load(save_path)

    assert loaded._log_target is True
    after = loaded.predict([query_row])[0].value_g_per_kwh
    assert before == pytest.approx(after)


def test_load_without_meta_sidecar_defaults_log_target_false(tmp_path: Path) -> None:
    rows, targets = _synthetic_rows(300)
    trained = model.CarbonIntensityModel.train(rows, targets, log_target=False)
    save_path = tmp_path / "model.txt"
    trained.save(save_path)
    save_path.with_suffix(save_path.suffix + ".meta.json").unlink()  # simulate a legacy model

    loaded = model.CarbonIntensityModel.load(save_path)

    assert loaded._log_target is False


def test_recency_weights_newest_row_has_full_weight() -> None:
    rows, _ = _synthetic_rows(100)
    weights = model._recency_weights(rows)
    assert weights[-1] == pytest.approx(1.0)


def test_recency_weights_decay_with_age() -> None:
    rows, _ = _synthetic_rows(100)
    weights = model._recency_weights(rows)
    assert weights[0] < weights[50] < weights[-1]


def test_recency_weights_half_life() -> None:
    origin = HOUR
    half_life_hours = model.RECENCY_HALF_LIFE_DAYS * 24
    rows = [
        features.FeatureRow(
            timestamp=origin,
            hour_sin=0.0,
            hour_cos=0.0,
            dow_sin=0.0,
            dow_cos=0.0,
            month_sin=0.0,
            month_cos=0.0,
            residual_load_share=0.5,
            horizon_hours=0,
        ),
        features.FeatureRow(
            timestamp=origin + dt.timedelta(hours=half_life_hours),
            hour_sin=0.0,
            hour_cos=0.0,
            dow_sin=0.0,
            dow_cos=0.0,
            month_sin=0.0,
            month_cos=0.0,
            residual_load_share=0.5,
            horizon_hours=0,
        ),
    ]
    weights = model._recency_weights(rows)
    assert weights[0] == pytest.approx(0.5, rel=1e-6)
    assert weights[1] == pytest.approx(1.0)


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


def test_breakdown_model_predict_uses_breakdown_lag_feature() -> None:
    # Every calendar/residual_load_share feature is held constant; wind's
    # share ramps with a 24h period, and 168h (BREAKDOWN_LAG_HOURS) is an
    # exact multiple of 24h, so wind[i-168] == wind[i] for i >= 168 --
    # the *only* learnable signal for predicting wind's share is its own
    # 168h-ago lag. If the lag column were silently ignored, predictions
    # would be roughly constant regardless of the supplied history.
    n = 300
    rows = []
    breakdowns = []
    for i in range(n):
        rows.append(
            features.FeatureRow(
                timestamp=HOUR + dt.timedelta(hours=i),
                hour_sin=0.0,
                hour_cos=0.0,
                dow_sin=0.0,
                dow_cos=0.0,
                month_sin=0.0,
                month_cos=0.0,
                residual_load_share=0.5,
                horizon_hours=0,
            )
        )
        wind_share = 20.0 + 60.0 * ((i % 24) / 24.0)
        breakdowns.append({"wind": wind_share, "coal": 100.0 - wind_share})

    trained = model.BreakdownModel.train(rows, breakdowns, categories=("wind", "coal"))

    target_timestamp = HOUR + dt.timedelta(hours=n + 1)
    query_row = features.FeatureRow(
        timestamp=target_timestamp,
        hour_sin=0.0,
        hour_cos=0.0,
        dow_sin=0.0,
        dow_cos=0.0,
        month_sin=0.0,
        month_cos=0.0,
        residual_load_share=0.5,
        horizon_hours=1,
    )
    lag_timestamp = target_timestamp - dt.timedelta(hours=168)
    low_history = {lag_timestamp: {"wind": 20.0, "coal": 80.0}}
    high_history = {lag_timestamp: {"wind": 75.0, "coal": 25.0}}

    low_prediction = trained.predict([query_row], breakdown_history=low_history)[0]
    high_prediction = trained.predict([query_row], breakdown_history=high_history)[0]

    assert (
        low_prediction.power_breakdown_percent["wind"]
        < high_prediction.power_breakdown_percent["wind"]
    )


def test_breakdown_model_predict_without_history_defaults_to_nan_lag() -> None:
    rows, breakdowns = _synthetic_breakdowns(200)
    trained = model.BreakdownModel.train(rows, breakdowns, categories=("wind", "coal"))
    query_row = dataclasses.replace(rows[0], horizon_hours=1)

    # Must not raise despite no breakdown_history supplied.
    predictions = trained.predict([query_row])
    assert len(predictions) == 1


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
