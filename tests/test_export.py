"""Deterministic tests for the export JSON schema — no network access."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

from oko.export import (
    ATTRIBUTION,
    CurrentBreakdown,
    build_exchanges_payload,
    build_payload,
    write_json,
)
from oko.fetchers.entsoe import ExchangeRecord
from oko.forecast.model import Prediction

GENERATED_AT = dt.datetime(2026, 9, 1, 6, 0, tzinfo=dt.UTC)


def test_build_payload_matches_binding_schema() -> None:
    predictions = [
        Prediction(
            timestamp=dt.datetime(2026, 9, 1, 7, tzinfo=dt.UTC),
            value_g_per_kwh=342.4,
            confidence="high",
            value_lifecycle_g_per_kwh=410.2,
            power_breakdown_percent={"coal": 40.0, "wind": 60.0},
            price_eur_per_mwh=78.421,
        )
    ]
    payload = build_payload(
        predictions,
        zone="DE-LU",
        generated_at=GENERATED_AT,
        model_version="0.1.0",
        source_repo_url="https://github.com/tilalx/oko",
        training_rows=2016,
    )

    assert payload == {
        "zone": "DE",
        "generated_at": "2026-09-01T06:00:00Z",
        "model_version": "0.1.0",
        "unit": "gCO2eq/kWh",
        "training_rows": 2016,
        "current": None,
        "forecast": [
            {
                "timestamp": "2026-09-01T07:00:00Z",
                "value": 342,
                "value_lifecycle": 410,
                "confidence": "high",
                "power_breakdown_percent": {"coal": 40.0, "wind": 60.0},
                "price_eur_per_mwh": 78.42,
            },
        ],
        "attribution": list(ATTRIBUTION),
        "source": "https://github.com/tilalx/oko",
    }


def test_build_payload_maps_other_zones_to_their_own_key() -> None:
    payload = build_payload(
        [],
        zone="FR",
        generated_at=GENERATED_AT,
        model_version="0.1.0",
        source_repo_url="https://x",
        training_rows=0,
    )
    assert payload["zone"] == "FR"


def test_build_payload_includes_current_breakdown_when_given() -> None:
    current = CurrentBreakdown(
        timestamp=GENERATED_AT,
        power_breakdown_percent={"coal": 40.0, "wind": 60.0},
        renewable_percent=60.0,
        fossil_free_percent=60.0,
        emissions_breakdown_percent={"coal": 100.0},
    )
    payload = build_payload(
        [],
        zone="DE-LU",
        generated_at=GENERATED_AT,
        model_version="0.1.0",
        source_repo_url="https://x",
        training_rows=0,
        current=current,
    )
    assert payload["current"] == {
        "timestamp": "2026-09-01T06:00:00Z",
        "power_breakdown_percent": {"coal": 40.0, "wind": 60.0},
        "renewable_percent": 60.0,
        "fossil_free_percent": 60.0,
        "emissions_breakdown_percent": {"coal": 100.0},
    }


def test_current_breakdown_emissions_field_defaults_to_empty() -> None:
    current = CurrentBreakdown(
        timestamp=GENERATED_AT,
        power_breakdown_percent={"wind": 100.0},
        renewable_percent=100.0,
        fossil_free_percent=100.0,
    )
    assert current.emissions_breakdown_percent == {}


def test_build_exchanges_payload_picks_latest_timestamp_per_border() -> None:
    older = GENERATED_AT - dt.timedelta(hours=1)
    records = [
        ExchangeRecord(zone_from="AT", zone_to="DE-LU", timestamp=older, net_flow_mw=100.0),
        ExchangeRecord(zone_from="AT", zone_to="DE-LU", timestamp=GENERATED_AT, net_flow_mw=150.4),
        ExchangeRecord(zone_from="BE", zone_to="NL", timestamp=older, net_flow_mw=-50.0),
    ]
    payload = build_exchanges_payload(
        records, generated_at=GENERATED_AT, source_repo_url="https://x"
    )
    assert payload == {
        "generated_at": "2026-09-01T06:00:00Z",
        "exchanges": [
            {
                "zone_from": "AT",
                "zone_to": "DE-LU",
                "timestamp": "2026-09-01T06:00:00Z",
                "net_flow_mw": 150,
            },
            {
                "zone_from": "BE",
                "zone_to": "NL",
                "timestamp": "2026-09-01T05:00:00Z",
                "net_flow_mw": -50,
            },
        ],
        "source": "https://x",
    }


def test_build_exchanges_payload_empty_when_no_records() -> None:
    payload = build_exchanges_payload([], generated_at=GENERATED_AT, source_repo_url="https://x")
    assert payload["exchanges"] == []


def test_build_payload_lifecycle_null_when_not_predicted() -> None:
    predictions = [
        Prediction(timestamp=GENERATED_AT, value_g_per_kwh=100.0, confidence="high"),
    ]
    payload = build_payload(
        predictions,
        zone="DE-LU",
        generated_at=GENERATED_AT,
        model_version="0.1.0",
        source_repo_url="https://x",
        training_rows=0,
    )
    assert payload["forecast"][0]["value_lifecycle"] is None
    assert payload["forecast"][0]["power_breakdown_percent"] is None
    assert payload["forecast"][0]["price_eur_per_mwh"] is None


def test_build_payload_rounds_value_to_int() -> None:
    predictions = [
        Prediction(timestamp=GENERATED_AT, value_g_per_kwh=99.6, confidence="low"),
    ]
    payload = build_payload(
        predictions,
        zone="DE-LU",
        generated_at=GENERATED_AT,
        model_version="0.1.0",
        source_repo_url="https://x",
        training_rows=0,
    )
    assert payload["forecast"][0]["value"] == 100
    assert isinstance(payload["forecast"][0]["value"], int)


def test_build_payload_empty_forecast_list() -> None:
    payload = build_payload(
        [],
        zone="DE-LU",
        generated_at=GENERATED_AT,
        model_version="0.1.0",
        source_repo_url="https://x",
        training_rows=0,
    )
    assert payload["forecast"] == []


def test_write_json_produces_parseable_matching_file(tmp_path: Path) -> None:
    predictions = [
        Prediction(timestamp=GENERATED_AT, value_g_per_kwh=250.0, confidence="high"),
    ]
    payload = build_payload(
        predictions,
        zone="DE-LU",
        generated_at=GENERATED_AT,
        model_version="0.1.0",
        source_repo_url="https://x",
        training_rows=0,
    )
    out_path = tmp_path / "forecast_de.json"

    write_json(payload, out_path)

    assert json.loads(out_path.read_text()) == payload


def test_write_json_creates_parent_directories(tmp_path: Path) -> None:
    out_path = tmp_path / "nested" / "dir" / "forecast_de.json"
    write_json({"zone": "DE"}, out_path)
    assert out_path.exists()


def test_write_json_overwrites_existing_file_not_writable_by_current_user(
    tmp_path: Path,
) -> None:
    """Regression: a stale file owned/locked-down by a different writer
    (e.g. left behind by a host-run pipeline before a containerized run
    with a different UID) must not block subsequent exports, since
    write_json replaces via rename rather than truncating in place."""
    out_path = tmp_path / "forecast_de.json"
    out_path.write_text("stale")
    out_path.chmod(0o000)

    try:
        write_json({"zone": "DE"}, out_path)
    finally:
        out_path.chmod(0o644)

    assert json.loads(out_path.read_text()) == {"zone": "DE"}


def test_write_json_output_is_world_readable(tmp_path: Path) -> None:
    """Regression: the export is read by another container/UID (e.g. the
    `serve` stage's non-root user on a shared bind mount) -- mkstemp's
    default 0600 must not survive the atomic rename into place."""
    out_path = tmp_path / "forecast_de.json"

    write_json({"zone": "DE"}, out_path)

    mode = out_path.stat().st_mode
    assert mode & 0o044 == 0o044


def test_build_payload_handles_non_utc_timezone_input() -> None:
    # A timestamp in a different (but real) offset must still render as Zulu.
    berlin_time = dt.datetime(2026, 9, 1, 8, 0, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    payload = build_payload(
        [],
        zone="DE-LU",
        generated_at=berlin_time,
        model_version="0.1.0",
        source_repo_url="https://x",
        training_rows=0,
    )
    assert payload["generated_at"] == "2026-09-01T06:00:00Z"
