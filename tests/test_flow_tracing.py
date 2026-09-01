"""Tests for the multi-hop flow-tracing engine.

The most important tests here aren't just "does it run" -- they verify
the linear-algebra derivation against two things we can compute by hand:
the trivial single-zone case (must equal ``production_intensity``) and
the two-zone case (must equal the existing, already-verified one-hop
``calculator.calculate``). If those two agree, the general N-zone matrix
construction is very likely correct, which is the actual point of these
tests: proving the derivation in the module docstring is right, not just
exercising code paths.
"""

from __future__ import annotations

import datetime as dt

import pytest

from oko.emissions import calculator, flow_tracing
from oko.emissions.factors import GLOBAL_DIRECT_FACTORS_G_PER_KWH
from oko.fetchers.entsoe import ExchangeRecord

HOUR = dt.datetime(2026, 8, 31, 12, tzinfo=dt.UTC)
FACTORS = {"coal": 800.0, "wind": 0.0, "nuclear": 0.0, "unknown": 500.0}


def test_isolated_zone_matches_production_intensity() -> None:
    production = {"A": {"coal": 1000.0}}
    factors = {"A": FACTORS}

    result = flow_tracing.trace_flows_for_hour(HOUR, production, [], factors)

    assert result["A"].corrected_g_per_kwh == pytest.approx(800.0)
    assert result["A"].domestic_g_per_kwh == pytest.approx(800.0)
    assert result["A"].import_share == pytest.approx(0.0)


def test_two_zone_matches_one_hop_calculator_exactly() -> None:
    # A exports 1000 MW to B. A has no imports of its own, so a two-node
    # flow trace must reduce exactly to the one-hop formula.
    production = {
        "A": {"wind": 2000.0},  # 0 g/kWh
        "B": {"coal": 1000.0},  # 800 g/kWh domestic
    }
    factors = {"A": FACTORS, "B": FACTORS}
    records = [ExchangeRecord(zone_from="A", zone_to="B", timestamp=HOUR, net_flow_mw=1000.0)]

    traced = flow_tracing.trace_flows_for_hour(HOUR, production, records, factors)

    one_hop = calculator.calculate(
        "B",
        HOUR,
        production["B"],
        FACTORS,
        neighbor_imports_mw={"A": 1000.0},
        neighbor_domestic_intensities={"A": 0.0},
    )

    assert traced["B"].corrected_g_per_kwh == pytest.approx(one_hop.corrected_g_per_kwh)
    assert traced["B"].import_share == pytest.approx(one_hop.import_share)
    assert traced["A"].corrected_g_per_kwh == pytest.approx(0.0)  # pure exporter, own mix only


def test_three_node_chain_propagates_multi_hop() -> None:
    # A (pure, 0 g/kWh) -> B (imports only from A, no own gen) -> C
    # (imports only from B). C's intensity must reflect A's mix through
    # B, even though B has no generation of its own to look "dirty" on a
    # naive domestic-only neighbor lookup.
    production = {
        "A": {"wind": 1000.0},
        "B": {},
        "C": {"coal": 500.0},
    }
    factors = {"A": FACTORS, "B": FACTORS, "C": FACTORS}
    records = [
        ExchangeRecord(zone_from="A", zone_to="B", timestamp=HOUR, net_flow_mw=1000.0),
        ExchangeRecord(zone_from="B", zone_to="C", timestamp=HOUR, net_flow_mw=1000.0),
    ]

    traced = flow_tracing.trace_flows_for_hour(HOUR, production, records, factors)

    assert traced["A"].corrected_g_per_kwh == pytest.approx(0.0)
    assert traced["B"].corrected_g_per_kwh == pytest.approx(0.0)  # B is 100% A's clean import
    # C: 500 MW own coal (800 g/kWh) + 1000 MW clean import from B -> (500*800+1000*0)/1500
    assert traced["C"].corrected_g_per_kwh == pytest.approx((500 * 800.0) / 1500.0)
    assert traced["C"].import_share == pytest.approx(1000.0 / 1500.0)


def test_three_node_chain_differs_from_naive_one_hop_via_b() -> None:
    # Same network as above, but compute C's intensity via the *naive*
    # one-hop method using B's own *domestic-only* intensity (which is
    # undefined/zero since B generates nothing) -- this is exactly the
    # gap real flow tracing closes: a one-hop lookup at C can't see that
    # B's import from A exists at all if it only asks "what does B
    # generate itself".
    b_domestic = calculator.production_intensity({}, FACTORS)
    assert b_domestic is None  # naive one-hop has *nothing* to offer here

    production = {"A": {"wind": 1000.0}, "B": {}, "C": {"coal": 500.0}}
    factors = {"A": FACTORS, "B": FACTORS, "C": FACTORS}
    records = [
        ExchangeRecord(zone_from="A", zone_to="B", timestamp=HOUR, net_flow_mw=1000.0),
        ExchangeRecord(zone_from="B", zone_to="C", timestamp=HOUR, net_flow_mw=1000.0),
    ]
    traced = flow_tracing.trace_flows_for_hour(HOUR, production, records, factors)
    # Flow tracing still produces a sensible answer where the naive
    # one-hop method would have to skip B's contribution entirely.
    assert traced["C"].corrected_g_per_kwh == pytest.approx((500 * 800.0) / 1500.0)


def test_three_node_cycle_solves_without_error() -> None:
    # A -> B -> C -> A, a genuine loop. A naive recursive substitution
    # would never terminate; the linear solve must handle it in one pass.
    production = {
        "A": {"coal": 300.0},
        "B": {"wind": 300.0},
        "C": {"nuclear": 300.0},
    }
    factors = {"A": FACTORS, "B": FACTORS, "C": FACTORS}
    records = [
        ExchangeRecord(zone_from="A", zone_to="B", timestamp=HOUR, net_flow_mw=100.0),
        ExchangeRecord(zone_from="B", zone_to="C", timestamp=HOUR, net_flow_mw=100.0),
        ExchangeRecord(zone_from="C", zone_to="A", timestamp=HOUR, net_flow_mw=100.0),
    ]

    traced = flow_tracing.trace_flows_for_hour(HOUR, production, records, factors)

    assert set(traced) == {"A", "B", "C"}
    for zone in traced.values():
        assert 0.0 <= zone.corrected_g_per_kwh <= 800.0  # sane bounds, no blow-up
        assert 0.0 <= zone.import_share <= 1.0


def test_missing_zone_is_simply_absent_not_zero() -> None:
    # C isn't in production_by_zone_hour at all (fetch failed that hour).
    production = {"A": {"coal": 1000.0}, "B": {"wind": 500.0}}
    factors = {"A": FACTORS, "B": FACTORS, "C": FACTORS}
    records = [
        ExchangeRecord(zone_from="A", zone_to="B", timestamp=HOUR, net_flow_mw=200.0),
        ExchangeRecord(zone_from="B", zone_to="C", timestamp=HOUR, net_flow_mw=100.0),
    ]

    traced = flow_tracing.trace_flows_for_hour(HOUR, production, records, factors)

    assert "C" not in traced
    assert "A" in traced and "B" in traced
    # B's inflow must not include the phantom edge to/from unmodeled C.
    assert traced["B"].import_share == pytest.approx(200.0 / (500.0 + 200.0))


def test_zero_inflow_zone_is_pruned() -> None:
    # C has no generation and no positive import (the only edge touching
    # it is an export away from it) -> must be dropped, not divide-by-zero.
    production = {"A": {"coal": 1000.0}, "C": {}}
    factors = {"A": FACTORS, "C": FACTORS}
    records = [ExchangeRecord(zone_from="C", zone_to="A", timestamp=HOUR, net_flow_mw=50.0)]

    traced = flow_tracing.trace_flows_for_hour(HOUR, production, records, factors)

    assert "C" not in traced
    assert "A" in traced
    # The edge from a pruned zone must not be treated as an import either.
    assert traced["A"].import_share == pytest.approx(0.0)


def test_no_zones_returns_empty_dict() -> None:
    assert flow_tracing.trace_flows_for_hour(HOUR, {}, [], {}) == {}


def test_singular_system_falls_back_to_one_hop(monkeypatch: pytest.MonkeyPatch) -> None:
    production = {
        "A": {"wind": 1000.0},
        "B": {"coal": 500.0},
    }
    factors = {"A": FACTORS, "B": FACTORS}
    records = [ExchangeRecord(zone_from="A", zone_to="B", timestamp=HOUR, net_flow_mw=500.0)]

    monkeypatch.setattr(flow_tracing, "_solve_hour", lambda *args, **kwargs: None)

    traced = flow_tracing.trace_flows_for_hour(HOUR, production, records, factors)

    # Fallback still produces a result via the one-hop calculator path.
    assert traced["B"].corrected_g_per_kwh == pytest.approx((500 * 800.0 + 500 * 0.0) / 1000.0)
    assert traced["A"].corrected_g_per_kwh == pytest.approx(0.0)


def test_trace_flows_series_groups_by_zone_sorted_by_time() -> None:
    hour0, hour1 = HOUR, HOUR + dt.timedelta(hours=1)
    production = {
        "A": {hour0: {"wind": 1000.0}, hour1: {"wind": 900.0}},
        "B": {hour0: {"coal": 500.0}, hour1: {"coal": 400.0}},
    }
    factors = {"A": FACTORS, "B": FACTORS}
    records = [
        ExchangeRecord(zone_from="A", zone_to="B", timestamp=hour0, net_flow_mw=200.0),
        ExchangeRecord(zone_from="A", zone_to="B", timestamp=hour1, net_flow_mw=150.0),
    ]

    series = flow_tracing.trace_flows_series(production, records, factors)

    assert [r.timestamp for r in series["B"]] == [hour0, hour1]
    assert [r.timestamp for r in series["A"]] == [hour0, hour1]


def test_uses_global_factors_table_shape() -> None:
    # Sanity: GLOBAL_DIRECT_FACTORS_G_PER_KWH must carry an "unknown" key
    # since _own_generation_and_emissions relies on it as a fallback.
    assert "unknown" in GLOBAL_DIRECT_FACTORS_G_PER_KWH


def test_method_is_flow_trace_on_the_linear_solve_path() -> None:
    production = {"A": {"coal": 1000.0}}
    factors = {"A": FACTORS}

    result = flow_tracing.trace_flows_for_hour(HOUR, production, [], factors)

    assert result["A"].method == "flow_trace"
    assert result["A"].lifecycle_g_per_kwh is None  # no lifecycle table given


def test_method_is_one_hop_fallback_on_the_singular_path(monkeypatch: pytest.MonkeyPatch) -> None:
    production = {"A": {"wind": 1000.0}, "B": {"coal": 500.0}}
    factors = {"A": FACTORS, "B": FACTORS}
    records = [ExchangeRecord(zone_from="A", zone_to="B", timestamp=HOUR, net_flow_mw=500.0)]

    monkeypatch.setattr(flow_tracing, "_solve_hour", lambda *args, **kwargs: None)

    traced = flow_tracing.trace_flows_for_hour(HOUR, production, records, factors)

    assert traced["A"].method == "one_hop_fallback"
    assert traced["B"].method == "one_hop_fallback"
    assert traced["B"].lifecycle_g_per_kwh is None  # fallback path never computes lifecycle


def test_lifecycle_solved_alongside_direct_with_its_own_factor_table() -> None:
    # Same two-zone network as test_two_zone_matches_one_hop_calculator_exactly,
    # but with a distinct lifecycle factor table so the two results must differ.
    production = {
        "A": {"wind": 2000.0},
        "B": {"coal": 1000.0},
    }
    direct_factors = {"A": FACTORS, "B": FACTORS}
    lifecycle_factors = {
        "A": {"coal": 800.0, "wind": 11.0, "nuclear": 12.0, "unknown": 500.0},
        "B": {"coal": 800.0, "wind": 11.0, "nuclear": 12.0, "unknown": 500.0},
    }
    records = [ExchangeRecord(zone_from="A", zone_to="B", timestamp=HOUR, net_flow_mw=1000.0)]

    traced = flow_tracing.trace_flows_for_hour(
        HOUR, production, records, direct_factors, lifecycle_factors
    )

    # B: 1000 MW own coal (800 direct / 800 lifecycle, same value here) +
    # 1000 MW imported wind from A (0 direct / 11 lifecycle) -- direct and
    # lifecycle must diverge because only wind's factor differs.
    assert traced["B"].corrected_g_per_kwh == pytest.approx((1000 * 800.0 + 1000 * 0.0) / 2000.0)
    assert traced["B"].lifecycle_g_per_kwh == pytest.approx((1000 * 800.0 + 1000 * 11.0) / 2000.0)
    assert traced["B"].corrected_g_per_kwh != traced["B"].lifecycle_g_per_kwh


def test_trace_flows_series_carries_lifecycle_through() -> None:
    hour0 = HOUR
    production = {"A": {hour0: {"coal": 1000.0}}}
    lifecycle_factors = {"A": {**FACTORS, "coal": 900.0}}

    series = flow_tracing.trace_flows_series(production, [], {"A": FACTORS}, lifecycle_factors)

    assert series["A"][0].lifecycle_g_per_kwh == pytest.approx(900.0)
