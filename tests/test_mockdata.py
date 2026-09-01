"""Deterministic tests for the mock-data dev tool — no network access."""

from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

from oko.config import EXCHANGE_BORDERS, FLOW_TRACING_ZONES
from oko.forecast.model import confidence_for_horizon
from oko.mockdata import (
    generate_exchanges_payload,
    generate_payload,
    generate_predictions,
    main,
)

REFERENCE_TIME = dt.datetime(2026, 9, 1, 6, tzinfo=dt.UTC)


def test_generate_predictions_returns_requested_count() -> None:
    predictions = generate_predictions(reference_time=REFERENCE_TIME, hours=48, seed=1)
    assert len(predictions) == 48


def test_generate_predictions_hourly_timestamps_ahead_of_reference() -> None:
    predictions = generate_predictions(reference_time=REFERENCE_TIME, hours=5, seed=1)
    assert [p.timestamp for p in predictions] == [
        REFERENCE_TIME + dt.timedelta(hours=h) for h in range(1, 6)
    ]


def test_generate_predictions_confidence_matches_horizon_rule() -> None:
    predictions = generate_predictions(reference_time=REFERENCE_TIME, hours=120, seed=1)
    for horizon_hours, prediction in enumerate(predictions, start=1):
        assert prediction.confidence == confidence_for_horizon(horizon_hours)


def test_generate_predictions_values_never_negative() -> None:
    predictions = generate_predictions(reference_time=REFERENCE_TIME, hours=120, seed=7)
    assert all(p.value_g_per_kwh >= 0.0 for p in predictions)


def test_generate_predictions_is_deterministic_given_seed() -> None:
    first = generate_predictions(reference_time=REFERENCE_TIME, hours=24, seed=42)
    second = generate_predictions(reference_time=REFERENCE_TIME, hours=24, seed=42)
    assert first == second


def test_generate_predictions_includes_synthetic_lifecycle_value() -> None:
    predictions = generate_predictions(reference_time=REFERENCE_TIME, hours=3, seed=1)
    for prediction in predictions:
        assert prediction.value_lifecycle_g_per_kwh == pytest.approx(
            prediction.value_g_per_kwh * 1.25
        )


def test_generate_predictions_includes_synthetic_breakdown_summing_to_100() -> None:
    predictions = generate_predictions(reference_time=REFERENCE_TIME, hours=5, seed=1)
    for prediction in predictions:
        assert prediction.power_breakdown_percent is not None
        assert sum(prediction.power_breakdown_percent.values()) == pytest.approx(100.0)


def test_generate_payload_includes_current_block_and_zone() -> None:
    payload = generate_payload(
        "FR", reference_time=REFERENCE_TIME, hours=2, seed=1, model_version="mock-0.1.0"
    )
    assert payload["zone"] == "FR"
    assert payload["current"]["renewable_percent"] > 0  # type: ignore[index]
    assert payload["forecast"][0]["value_lifecycle"] is not None  # type: ignore[index]


def test_generate_payload_current_includes_emissions_breakdown() -> None:
    payload = generate_payload(
        "DE-LU", reference_time=REFERENCE_TIME, hours=1, seed=1, model_version="mock-0.1.0"
    )
    emissions_breakdown = payload["current"]["emissions_breakdown_percent"]  # type: ignore[index]
    assert emissions_breakdown
    assert "wind" not in emissions_breakdown  # zero direct factor, drops out
    assert "coal" in emissions_breakdown


def test_generate_exchanges_payload_covers_every_configured_border() -> None:
    payload = generate_exchanges_payload(reference_time=REFERENCE_TIME, seed=1)
    borders = {(e["zone_from"], e["zone_to"]) for e in payload["exchanges"]}
    assert borders == set(EXCHANGE_BORDERS)


def test_generate_exchanges_payload_is_deterministic_given_seed() -> None:
    first = generate_exchanges_payload(reference_time=REFERENCE_TIME, seed=9)
    second = generate_exchanges_payload(reference_time=REFERENCE_TIME, seed=9)
    assert first == second


def test_main_writes_valid_export_schema_with_mock_model_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_path = tmp_path / "forecast_de.json"
    monkeypatch.setattr(
        sys, "argv", ["oko.mockdata", "--out", str(out_path), "--hours", "6", "--seed", "3"]
    )

    main()

    payload = json.loads(out_path.read_text())
    assert payload["zone"] == "DE"
    assert payload["unit"] == "gCO2eq/kWh"
    assert payload["model_version"].startswith("mock-")
    assert len(payload["forecast"]) == 6


def test_main_with_zone_arg_writes_that_zones_export(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_path = tmp_path / "forecast_fr.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["oko.mockdata", "--out", str(out_path), "--zone", "FR", "--hours", "2", "--seed", "1"],
    )

    main()

    assert json.loads(out_path.read_text())["zone"] == "FR"


def test_main_all_zones_writes_every_zone_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        ["oko.mockdata", "--all-zones", "--out-dir", str(tmp_path), "--hours", "2", "--seed", "1"],
    )

    main()

    assert (tmp_path / "forecast_de.json").exists()  # DE-LU keeps the legacy filename
    for zone in FLOW_TRACING_ZONES:
        if zone == "DE-LU":
            continue
        assert (tmp_path / f"forecast_{zone}.json").exists()
        assert json.loads((tmp_path / f"forecast_{zone}.json").read_text())["zone"] == zone

    exchanges = json.loads((tmp_path / "exchanges.json").read_text())
    assert len(exchanges["exchanges"]) == len(EXCHANGE_BORDERS)


def test_main_single_zone_does_not_write_exchanges(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out_path = tmp_path / "forecast_de.json"
    monkeypatch.setattr(
        sys, "argv", ["oko.mockdata", "--out", str(out_path), "--hours", "2", "--seed", "1"]
    )

    main()

    assert not (tmp_path / "exchanges.json").exists()


def test_main_all_zones_requires_out_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["oko.mockdata", "--all-zones"])
    with pytest.raises(SystemExit):
        main()


def test_main_requires_out_or_all_zones(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["oko.mockdata"])
    with pytest.raises(SystemExit):
        main()
