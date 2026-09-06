"""Deterministic unit tests for emission factors and the intensity calculator."""

from __future__ import annotations

import datetime as dt

import pytest

from oko.emissions import calculator, factors
from oko.emissions.backtest import compare
from oko.emissions.calculator import CarbonIntensity
from oko.fetchers.energy_charts import ReferencePoint
from oko.fetchers.entsoe import ExchangeRecord

HOUR = dt.datetime(2026, 8, 31, 12, tzinfo=dt.UTC)


# --------------------------------------------------------------------------
# factors.py
# --------------------------------------------------------------------------


def test_factors_for_zone_applies_de_lu_overrides() -> None:
    de_factors = factors.factors_for_zone("DE-LU")
    assert de_factors["coal"] == factors.DE_LU_DIRECT_FACTOR_OVERRIDES_G_PER_KWH["coal"]
    assert de_factors["coal"] != factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH["coal"]
    # Categories with no DE override fall back to the global table unchanged.
    assert de_factors["wind"] == factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH["wind"]
    assert de_factors["nuclear"] == factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH["nuclear"]


def test_factors_for_zone_applies_neighbour_overrides() -> None:
    fr_factors = factors.factors_for_zone("FR")
    assert fr_factors["coal"] == factors._OTHER_ZONE_DIRECT_FACTOR_OVERRIDES_G_PER_KWH["FR"]["coal"]
    assert fr_factors["coal"] != factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH["coal"]
    # Categories with no FR override (e.g. nuclear -- always ~0 direct) fall
    # back to the global table unchanged.
    assert fr_factors["nuclear"] == factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH["nuclear"]


def test_factors_for_zone_falls_back_to_global_for_unknown_zone() -> None:
    assert factors.factors_for_zone("ZZ") == factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH


def test_factors_do_not_mutate_shared_globals() -> None:
    result = factors.factors_for_zone("DE-LU")
    result["coal"] = -1.0
    assert factors.factors_for_zone("DE-LU")["coal"] != -1.0


def test_lifecycle_factors_apply_de_lu_overrides_and_differ_from_direct() -> None:
    lifecycle = factors.factors_for_zone("DE-LU", kind="lifecycle")
    assert lifecycle["coal"] == factors.DE_LU_LIFECYCLE_FACTOR_OVERRIDES_G_PER_KWH["coal"]
    # Unlike direct factors, lifecycle wind/nuclear/hydro/solar are NOT ~0.
    assert lifecycle["wind"] > 0
    assert lifecycle["nuclear"] > 0
    direct = factors.factors_for_zone("DE-LU", kind="direct")
    assert lifecycle["coal"] != direct["coal"]


def test_lifecycle_factors_fall_back_to_global_for_unknown_zone() -> None:
    assert (
        factors.factors_for_zone("ZZ", kind="lifecycle")
        == factors.GLOBAL_LIFECYCLE_FACTORS_G_PER_KWH
    )


def test_waste_is_a_distinct_category_from_biomass_with_a_nonzero_direct_factor() -> None:
    de_factors = factors.factors_for_zone("DE-LU")
    assert de_factors["waste"] > 0.0
    assert de_factors["biomass"] == 0.0
    assert "waste" in factors.CATEGORIES


def test_zones_missing_override_flags_zones_with_no_zone_specific_factors() -> None:
    missing = factors.zones_missing_override(("DE-LU", "FR", "ZZ"))
    assert missing == ["ZZ"]  # DE-LU and FR both have explicit overrides


def test_zones_missing_override_lifecycle_kind() -> None:
    missing = factors.zones_missing_override(("DE-LU", "ZZ"), kind="lifecycle")
    assert missing == ["ZZ"]


# --------------------------------------------------------------------------
# calculator.power_breakdown_percentages
# --------------------------------------------------------------------------


def test_power_breakdown_percentages_shape_and_shares() -> None:
    breakdown, renewable_pct, fossil_free_pct = calculator.power_breakdown_percentages(
        {"coal": 60.0, "wind": 30.0, "nuclear": 10.0}
    )
    assert breakdown == pytest.approx({"coal": 60.0, "wind": 30.0, "nuclear": 10.0})
    assert renewable_pct == pytest.approx(30.0)  # wind only
    assert fossil_free_pct == pytest.approx(40.0)  # wind + nuclear


def test_power_breakdown_percentages_ignores_negative_and_zero_entries() -> None:
    breakdown, _, _ = calculator.power_breakdown_percentages(
        {"coal": 100.0, "wind": -5.0, "solar": 0.0}
    )
    assert breakdown == pytest.approx({"coal": 100.0})


def test_power_breakdown_percentages_empty_for_zero_total() -> None:
    assert calculator.power_breakdown_percentages({}) == ({}, 0.0, 0.0)
    assert calculator.power_breakdown_percentages({"coal": 0.0}) == ({}, 0.0, 0.0)


# --------------------------------------------------------------------------
# calculator.emissions_weighted_breakdown_percentages
# --------------------------------------------------------------------------


def test_emissions_weighted_breakdown_drops_zero_factor_categories() -> None:
    # Equal MW, but only coal has a non-zero direct factor -- wind must
    # drop out of the emissions-share view entirely even though it's half
    # the MW-share breakdown.
    breakdown = calculator.emissions_weighted_breakdown_percentages(
        {"coal": 50.0, "wind": 50.0}, {"coal": 800.0, "wind": 0.0, "unknown": 500.0}
    )
    assert breakdown == pytest.approx({"coal": 100.0})


def test_emissions_weighted_breakdown_weights_by_factor_not_mw() -> None:
    # Gas has a third of coal's MW but the same total weighted emissions
    # once its higher factor is applied.
    breakdown = calculator.emissions_weighted_breakdown_percentages(
        {"coal": 30.0, "gas": 10.0}, {"coal": 100.0, "gas": 300.0, "unknown": 500.0}
    )
    assert breakdown == pytest.approx({"coal": 50.0, "gas": 50.0})


def test_emissions_weighted_breakdown_falls_back_to_unknown_factor() -> None:
    breakdown = calculator.emissions_weighted_breakdown_percentages(
        {"biomass": 10.0}, {"unknown": 500.0}
    )
    assert breakdown == pytest.approx({"biomass": 100.0})


def test_emissions_weighted_breakdown_empty_for_zero_production() -> None:
    assert calculator.emissions_weighted_breakdown_percentages({}, {"unknown": 500.0}) == {}


def test_emissions_weighted_breakdown_empty_when_all_factors_zero() -> None:
    breakdown = calculator.emissions_weighted_breakdown_percentages(
        {"wind": 100.0, "solar": 50.0}, {"wind": 0.0, "solar": 0.0, "unknown": 500.0}
    )
    assert breakdown == {}


# --------------------------------------------------------------------------
# calculator.production_intensity
# --------------------------------------------------------------------------


def test_production_intensity_weighted_average() -> None:
    mix = {"wind": 8000.0, "coal": 2000.0}
    result = calculator.production_intensity(mix, {"wind": 0.0, "coal": 800.0, "unknown": 500.0})
    # (8000*0 + 2000*800) / 10000 = 160
    assert result == pytest.approx(160.0)


def test_production_intensity_unknown_category_uses_fallback_factor() -> None:
    mix = {"marine": 100.0}  # not a known category
    result = calculator.production_intensity(mix, {"unknown": 575.0})
    assert result == pytest.approx(575.0)


def test_production_intensity_returns_none_for_zero_production() -> None:
    assert calculator.production_intensity({}, factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH) is None
    assert (
        calculator.production_intensity(
            {"wind": 0.0, "coal": -5.0}, factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH
        )
        is None
    )


def test_production_intensity_ignores_negative_values() -> None:
    # A small negative reading (measurement noise) shouldn't skew the average.
    mix = {"coal": 1000.0, "wind": -10.0}
    result = calculator.production_intensity(mix, {"coal": 800.0, "wind": 0.0, "unknown": 500.0})
    assert result == pytest.approx(800.0)


# --------------------------------------------------------------------------
# calculator.import_mw_into / other_zone
# --------------------------------------------------------------------------


def test_import_mw_into_positive_for_the_importing_side() -> None:
    record = ExchangeRecord(zone_from="DE-LU", zone_to="FR", timestamp=HOUR, net_flow_mw=500.0)
    assert calculator.import_mw_into("FR", record) == 500.0
    assert calculator.import_mw_into("DE-LU", record) == 0.0


def test_import_mw_into_zero_for_unrelated_zone() -> None:
    record = ExchangeRecord(zone_from="DE-LU", zone_to="FR", timestamp=HOUR, net_flow_mw=500.0)
    assert calculator.import_mw_into("CH", record) == 0.0


def test_other_zone() -> None:
    record = ExchangeRecord(zone_from="DE-LU", zone_to="FR", timestamp=HOUR, net_flow_mw=1.0)
    assert calculator.other_zone("DE-LU", record) == "FR"
    assert calculator.other_zone("FR", record) == "DE-LU"
    assert calculator.other_zone("CH", record) is None


# --------------------------------------------------------------------------
# calculator.calculate
# --------------------------------------------------------------------------


def test_calculate_domestic_only_no_imports() -> None:
    result = calculator.calculate(
        "DE-LU",
        HOUR,
        {"wind": 8000.0, "coal": 2000.0},
        {"wind": 0.0, "coal": 800.0, "unknown": 500.0},
        neighbor_imports_mw={},
        neighbor_domestic_intensities={},
    )
    assert result.domestic_g_per_kwh == pytest.approx(160.0)
    assert result.corrected_g_per_kwh == pytest.approx(160.0)
    assert result.import_share == pytest.approx(0.0)
    assert result.method == "one_hop_fallback"
    assert result.lifecycle_g_per_kwh is None


def test_calculate_blends_import_weighted_by_mw() -> None:
    # Domestic: 1000 MW at 100 g/kWh. Import: 1000 MW from FR at 50 g/kWh.
    # Expected blended: (1000*100 + 1000*50) / 2000 = 75.
    result = calculator.calculate(
        "DE-LU",
        HOUR,
        {"coal": 1000.0},
        {"coal": 100.0, "unknown": 500.0},
        neighbor_imports_mw={"FR": 1000.0},
        neighbor_domestic_intensities={"FR": 50.0},
    )
    assert result.corrected_g_per_kwh == pytest.approx(75.0)
    assert result.import_share == pytest.approx(0.5)


def test_calculate_ignores_export_flows() -> None:
    # DE-LU is a net EXPORTER to FR (negative import) -> must not affect the result.
    only_domestic = calculator.calculate(
        "DE-LU",
        HOUR,
        {"coal": 1000.0},
        {"coal": 100.0, "unknown": 500.0},
        neighbor_imports_mw={},
        neighbor_domestic_intensities={},
    )
    with_export = calculator.calculate(
        "DE-LU",
        HOUR,
        {"coal": 1000.0},
        {"coal": 100.0, "unknown": 500.0},
        neighbor_imports_mw={"FR": -400.0},  # net export, must be ignored
        neighbor_domestic_intensities={"FR": 9999.0},
    )
    assert with_export.corrected_g_per_kwh == pytest.approx(only_domestic.corrected_g_per_kwh)
    assert with_export.import_share == pytest.approx(0.0)


def test_calculate_skips_neighbor_with_missing_intensity() -> None:
    # FR has a positive import but no known intensity -> contributes nothing,
    # rather than crashing the whole calculation.
    result = calculator.calculate(
        "DE-LU",
        HOUR,
        {"coal": 1000.0},
        {"coal": 100.0, "unknown": 500.0},
        neighbor_imports_mw={"FR": 500.0},
        neighbor_domestic_intensities={},  # no entry for FR
    )
    assert result.corrected_g_per_kwh == pytest.approx(100.0)
    assert result.import_share == pytest.approx(0.0)


def test_calculate_raises_when_nothing_computable() -> None:
    with pytest.raises(calculator.CalculatorError):
        calculator.calculate(
            "DE-LU",
            HOUR,
            {},
            {"unknown": 500.0},
            neighbor_imports_mw={"FR": 100.0},
            neighbor_domestic_intensities={},  # unusable: no domestic, no known import intensity
        )


def test_calculate_all_import_no_domestic_production() -> None:
    result = calculator.calculate(
        "DE-LU",
        HOUR,
        {},
        {"unknown": 500.0},
        neighbor_imports_mw={"FR": 200.0},
        neighbor_domestic_intensities={"FR": 42.0},
    )
    assert result.domestic_g_per_kwh is None
    assert result.corrected_g_per_kwh == pytest.approx(42.0)
    assert result.import_share == pytest.approx(1.0)


# --------------------------------------------------------------------------
# calculator.calculate_series
# --------------------------------------------------------------------------


def test_calculate_series_joins_production_and_exchange_by_hour() -> None:
    hour0 = HOUR
    hour1 = HOUR + dt.timedelta(hours=1)

    production_by_zone = {
        "DE-LU": {
            hour0: {"coal": 1000.0},
            hour1: {"coal": 900.0},
        },
        "FR": {
            hour0: {"nuclear": 1000.0},
            hour1: {"nuclear": 1000.0},
        },
    }
    exchange_records = [
        ExchangeRecord(zone_from="DE-LU", zone_to="FR", timestamp=hour0, net_flow_mw=-1000.0),
        ExchangeRecord(zone_from="DE-LU", zone_to="FR", timestamp=hour1, net_flow_mw=-500.0),
    ]
    factors_for_zone = {
        "DE-LU": factors.factors_for_zone("DE-LU"),
        "FR": {**factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH, "nuclear": 0.0},
    }
    de_coal_factor = factors_for_zone["DE-LU"]["coal"]

    results = calculator.calculate_series(
        "DE-LU", production_by_zone, exchange_records, factors_for_zone
    )

    assert [r.timestamp for r in results] == [hour0, hour1]
    # hour0: 1000 MW domestic coal + 1000 MW import from FR (nuclear, 0 g/kWh)
    expected_hour0 = (1000.0 * de_coal_factor + 1000.0 * 0.0) / 2000.0
    assert results[0].corrected_g_per_kwh == pytest.approx(expected_hour0)
    # hour1: 900 MW domestic coal + 500 MW import from FR
    expected_hour1 = (900.0 * de_coal_factor + 500.0 * 0.0) / 1400.0
    assert results[1].corrected_g_per_kwh == pytest.approx(expected_hour1)


def test_calculate_series_skips_unusable_hours_without_raising() -> None:
    hour0 = HOUR
    production_by_zone: dict[str, dict[dt.datetime, dict[str, float]]] = {"DE-LU": {hour0: {}}}
    exchange_records: list[ExchangeRecord] = []
    factors_for_zone = {"DE-LU": factors.factors_for_zone("DE-LU")}

    results = calculator.calculate_series(
        "DE-LU", production_by_zone, exchange_records, factors_for_zone
    )
    assert results == []


def test_calculate_series_ignores_borders_not_touching_target_zone() -> None:
    hour0 = HOUR
    production_by_zone = {
        "DE-LU": {hour0: {"coal": 1000.0}},
        "FR": {hour0: {"nuclear": 1000.0}},
        "CH": {hour0: {"hydro": 1000.0}},
    }
    # FR<->CH border shouldn't influence DE-LU's result at all.
    exchange_records = [
        ExchangeRecord(zone_from="CH", zone_to="FR", timestamp=hour0, net_flow_mw=500.0),
    ]
    factors_for_zone = {
        "DE-LU": factors.factors_for_zone("DE-LU"),
        "FR": factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH,
        "CH": factors.GLOBAL_DIRECT_FACTORS_G_PER_KWH,
    }

    results = calculator.calculate_series(
        "DE-LU", production_by_zone, exchange_records, factors_for_zone
    )
    assert len(results) == 1
    assert results[0].import_share == pytest.approx(0.0)


# --------------------------------------------------------------------------
# backtest.compare
# --------------------------------------------------------------------------


def test_compare_computes_mae_bias_and_rmse() -> None:
    hour0 = HOUR
    hour1 = HOUR + dt.timedelta(hours=1)
    calculated = [
        CarbonIntensity(
            zone="DE-LU",
            timestamp=hour0,
            domestic_g_per_kwh=110.0,
            corrected_g_per_kwh=110.0,
            import_share=0.0,
        ),
        CarbonIntensity(
            zone="DE-LU",
            timestamp=hour1,
            domestic_g_per_kwh=90.0,
            corrected_g_per_kwh=90.0,
            import_share=0.0,
        ),
    ]
    reference = [
        ReferencePoint(timestamp=hour0, co2eq_g_per_kwh=100.0),  # error +10
        ReferencePoint(timestamp=hour1, co2eq_g_per_kwh=100.0),  # error -10
    ]

    report = compare(calculated, reference)

    assert report.matched_hours == 2
    assert report.mean_absolute_error == pytest.approx(10.0)
    assert report.mean_bias == pytest.approx(0.0)  # +10 and -10 cancel
    assert report.rmse == pytest.approx(10.0)


def test_compare_only_uses_overlapping_hours() -> None:
    hour0 = HOUR
    hour_unmatched = HOUR + dt.timedelta(hours=5)
    calculated = [
        CarbonIntensity(
            zone="DE-LU",
            timestamp=hour0,
            domestic_g_per_kwh=100.0,
            corrected_g_per_kwh=100.0,
            import_share=0.0,
        ),
        CarbonIntensity(
            zone="DE-LU",
            timestamp=hour_unmatched,
            domestic_g_per_kwh=999.0,
            corrected_g_per_kwh=999.0,
            import_share=0.0,
        ),
    ]
    reference = [ReferencePoint(timestamp=hour0, co2eq_g_per_kwh=100.0)]

    report = compare(calculated, reference)
    assert report.matched_hours == 1
    assert report.mean_absolute_error == pytest.approx(0.0)


def test_compare_raises_on_no_overlap() -> None:
    calculated = [
        CarbonIntensity(
            zone="DE-LU",
            timestamp=HOUR,
            domestic_g_per_kwh=100.0,
            corrected_g_per_kwh=100.0,
            import_share=0.0,
        )
    ]
    reference = [ReferencePoint(timestamp=HOUR + dt.timedelta(days=1), co2eq_g_per_kwh=100.0)]
    with pytest.raises(ValueError, match="overlapping"):
        compare(calculated, reference)
