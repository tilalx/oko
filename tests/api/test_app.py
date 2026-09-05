"""Tests for the FastAPI query layer (web UI + evcc) — no network access."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from oko.api.app import app
from oko.config import FLOW_TRACING_ZONES, Settings, get_settings
from oko.export import build_payload
from oko.forecast.features import FeatureRow
from oko.forecast.model import Prediction
from oko.history import HistoryRow, upsert_rows


def _payload(zone: str = "DE-LU") -> dict[str, Any]:
    predictions = [
        Prediction(
            timestamp=dt.datetime(2026, 9, 1, 7, tzinfo=dt.UTC),
            value_g_per_kwh=342.4,
            confidence="high",
        ),
        Prediction(
            timestamp=dt.datetime(2026, 9, 1, 8, tzinfo=dt.UTC),
            value_g_per_kwh=300.0,
            confidence="high",
        ),
    ]
    return build_payload(
        predictions,
        zone=zone,
        generated_at=dt.datetime(2026, 9, 1, 6, tzinfo=dt.UTC),
        model_version="0.1.0",
        source_repo_url="https://x",
        training_rows=0,
    )


@pytest.fixture
def client_with_export(tmp_path: Path) -> Iterator[TestClient]:
    export_path = tmp_path / "forecast_de.json"
    export_path.write_text(json.dumps(_payload()))

    def _settings_override() -> Settings:
        return Settings(export_path=export_path, _env_file=None)  # type: ignore[call-arg]

    app.dependency_overrides[get_settings] = _settings_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client_with_two_zones(tmp_path: Path) -> Iterator[TestClient]:
    """DE-LU (legacy path/name) and FR (generic per-zone naming) both exported."""
    export_path = tmp_path / "forecast_de.json"
    export_path.write_text(json.dumps(_payload("DE-LU")))
    (tmp_path / "forecast_FR.json").write_text(json.dumps(_payload("FR")))

    sqlite_path = tmp_path / "history.sqlite3"
    upsert_rows(
        sqlite_path,
        [
            HistoryRow(
                zone="DE-LU",
                features=FeatureRow(
                    timestamp=dt.datetime(2026, 9, 1, 4, tzinfo=dt.UTC),
                    hour_sin=0.0,
                    hour_cos=0.0,
                    dow_sin=0.0,
                    dow_cos=0.0,
                    month_sin=0.0,
                    month_cos=0.0,
                    residual_load_share=0.5,
                    horizon_hours=0,
                ),
                target_g_per_kwh=250.0,
                lifecycle_g_per_kwh=310.0,
                method="flow_trace",
                breakdown_percent={"wind": 60.0, "coal": 40.0},
                price_eur_per_mwh=78.42,
            ),
        ],
    )

    def _settings_override() -> Settings:
        return Settings(  # type: ignore[call-arg]
            export_path=export_path, sqlite_path=sqlite_path, _env_file=None
        )

    app.dependency_overrides[get_settings] = _settings_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


@pytest.fixture
def client_without_export(tmp_path: Path) -> Iterator[TestClient]:
    def _settings_override() -> Settings:
        return Settings(  # type: ignore[call-arg]
            export_path=tmp_path / "missing.json", _env_file=None
        )

    app.dependency_overrides[get_settings] = _settings_override
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


def test_de_json_returns_export_payload(client_with_export: TestClient) -> None:
    response = client_with_export.get("/de.json")
    assert response.status_code == 200
    assert response.json() == _payload()


def test_de_json_returns_503_when_export_missing(client_without_export: TestClient) -> None:
    response = client_without_export.get("/de.json")
    assert response.status_code == 503


def test_evcc_co2_shape_and_ordering(client_with_export: TestClient) -> None:
    response = client_with_export.get("/api/evcc/co2")
    assert response.status_code == 200
    assert response.json() == [
        {"start": "2026-09-01T07:00:00Z", "end": "2026-09-01T08:00:00Z", "value": 342},
        {"start": "2026-09-01T08:00:00Z", "end": "2026-09-01T09:00:00Z", "value": 300},
    ]


def test_evcc_co2_returns_503_when_export_missing(client_without_export: TestClient) -> None:
    response = client_without_export.get("/api/evcc/co2")
    assert response.status_code == 503


def test_healthz_returns_plain_ok() -> None:
    response = TestClient(app).get("/healthz")
    assert response.status_code == 200
    assert response.text == "ok"


def test_cors_header_present(client_with_export: TestClient) -> None:
    response = client_with_export.get("/de.json", headers={"Origin": "https://example.com"})
    assert response.headers["access-control-allow-origin"] == "*"


def test_index_html_served_at_root() -> None:
    response = TestClient(app).get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]


def test_built_frontend_js_bundle_served() -> None:
    """The web UI is now a Vite-built Svelte app (hashed `assets/*.js`
    filenames, not a fixed `/app.js`) -- discover the actual bundle name
    from `STATIC_DIR` rather than hardcoding one."""
    from oko.api.app import STATIC_DIR

    bundles = list((STATIC_DIR / "assets").glob("*.js"))
    assert bundles, (
        "expected a built JS bundle under static/assets/ -- run `cd frontend && npm run build`"
    )
    response = TestClient(app).get(f"/assets/{bundles[0].name}")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_zone_json_returns_export_payload_for_other_zone(
    client_with_two_zones: TestClient,
) -> None:
    response = client_with_two_zones.get("/FR.json")
    assert response.status_code == 200
    assert response.json() == _payload("FR")


def test_zone_json_returns_404_for_unknown_zone(client_with_two_zones: TestClient) -> None:
    response = client_with_two_zones.get("/XX.json")
    assert response.status_code == 404


def test_zone_json_returns_503_when_that_zones_export_missing(
    client_with_two_zones: TestClient,
) -> None:
    # AT is a valid published zone but has no export file in this fixture.
    response = client_with_two_zones.get("/AT.json")
    assert response.status_code == 503


def test_evcc_co2_for_zone(client_with_two_zones: TestClient) -> None:
    response = client_with_two_zones.get("/api/evcc/co2/FR")
    assert response.status_code == 200
    assert response.json()[0]["value"] == 342


def test_zones_endpoint_lists_every_published_zone(client_with_two_zones: TestClient) -> None:
    response = client_with_two_zones.get("/zones")
    assert response.status_code == 200
    body = response.json()
    zones = {entry["zone"]: entry["available"] for entry in body["zones"]}
    assert set(zones) == set(FLOW_TRACING_ZONES)
    assert zones["DE-LU"] is True
    assert zones["FR"] is True
    assert zones["AT"] is False


def test_history_endpoint_returns_seeded_points(client_with_two_zones: TestClient) -> None:
    response = client_with_two_zones.get("/history/DE-LU", params={"hours": 24 * 365})
    assert response.status_code == 200
    points = response.json()
    assert len(points) == 1
    assert points[0]["value"] == 250.0
    assert points[0]["value_lifecycle"] == 310.0
    assert points[0]["method"] == "flow_trace"
    assert points[0]["power_breakdown_percent"] == {"wind": 60.0, "coal": 40.0}
    assert points[0]["price_eur_per_mwh"] == 78.42


def test_history_endpoint_404_for_unknown_zone(client_with_two_zones: TestClient) -> None:
    response = client_with_two_zones.get("/history/XX")
    assert response.status_code == 404


def test_history_endpoint_rejects_non_positive_hours(client_with_two_zones: TestClient) -> None:
    response = client_with_two_zones.get("/history/DE-LU", params={"hours": 0})
    assert response.status_code == 400


def test_bulk_endpoint_returns_every_zone_with_forecast_and_history(
    client_with_two_zones: TestClient,
) -> None:
    response = client_with_two_zones.get("/api/bulk", params={"hours": 24 * 365})
    assert response.status_code == 200
    zones = response.json()["zones"]
    assert set(zones) == set(FLOW_TRACING_ZONES)

    assert zones["DE-LU"]["forecast"] == _payload("DE-LU")
    assert len(zones["DE-LU"]["history"]) == 1
    assert zones["DE-LU"]["history"][0]["value"] == 250.0

    assert zones["FR"]["forecast"] == _payload("FR")
    assert zones["FR"]["history"] == []

    assert zones["AT"]["forecast"] is None
    assert zones["AT"]["history"] == []


def test_bulk_endpoint_rejects_non_positive_hours(client_with_two_zones: TestClient) -> None:
    response = client_with_two_zones.get("/api/bulk", params={"hours": 0})
    assert response.status_code == 400


def test_zones_geojson_served_and_covers_every_published_zone() -> None:
    response = TestClient(app).get("/zones.geojson")
    assert response.status_code == 200
    geojson = response.json()
    zones_in_geojson = {feature["properties"]["zone"] for feature in geojson["features"]}
    assert zones_in_geojson == set(FLOW_TRACING_ZONES)


def test_exchanges_json_returns_snapshot(client_with_export: TestClient, tmp_path: Path) -> None:
    exchanges_payload = {
        "generated_at": "2026-09-01T06:00:00Z",
        "exchanges": [
            {
                "zone_from": "AT",
                "zone_to": "DE-LU",
                "timestamp": "2026-09-01T06:00:00Z",
                "net_flow_mw": 120,
            }
        ],
        "source": "https://x",
    }
    (tmp_path / "exchanges.json").write_text(json.dumps(exchanges_payload))

    response = client_with_export.get("/exchanges.json")
    assert response.status_code == 200
    assert response.json() == exchanges_payload


def test_exchanges_json_returns_503_when_missing(client_with_export: TestClient) -> None:
    response = client_with_export.get("/exchanges.json")
    assert response.status_code == 503


def test_openapi_has_real_response_schemas() -> None:
    spec = TestClient(app).get("/openapi.json").json()
    schema = spec["paths"]["/de.json"]["get"]["responses"]["200"]["content"]["application/json"][
        "schema"
    ]
    assert schema == {"$ref": "#/components/schemas/ForecastPayload"}
    assert "ForecastPayload" in spec["components"]["schemas"]
    assert "properties" in spec["components"]["schemas"]["ForecastPayload"]
