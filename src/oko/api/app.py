"""FastAPI query layer: serves the forecast export for the web UI and evcc.

Reads a zone's export file live on every request rather than holding any
state in memory — each file is small (a few KB) and the pipeline is its
sole writer, so re-reading it per request needs no caching layer at this
traffic level. See README's "Deployment" section for what runs this app
in production (a single small container, no other job, no auth — the
project's "public, keyless" binding constraint).

Every route attaches a Pydantic ``response_model`` (see ``oko.api.schemas``)
so ``/openapi.json``/``/docs`` describe real response shapes rather than
opaque ``dict``/``list`` schemas.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from oko.api import schemas
from oko.api.evcc import to_evcc_rates
from oko.config import FLOW_TRACING_ZONES, TARGET_ZONE, Settings, get_settings
from oko.history import MAX_HISTORY_QUERY_HOURS, query_recent
from oko.isoformat import format_iso_z

#: Bundled web UI (see index.html) — plain static assets, no build step.
STATIC_DIR = Path(__file__).parent / "static"

#: Default window for GET /history/{zone} when ``hours`` isn't given.
DEFAULT_HISTORY_HOURS = 48

app = FastAPI(
    title="OKO forecast API",
    description="Self-hosted, keyless CO2 intensity forecast for the DE-LU flow-tracing network.",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
)

SettingsDep = Annotated[Settings, Depends(get_settings)]


def _export_path_for_zone(zone: str, settings: Settings) -> Path:
    """Map a zone to its export file path.

    ``TARGET_ZONE`` (DE-LU) keeps the legacy ``settings.export_path``
    exactly (back-compat, see ``oko.pipeline``); every other published
    zone lives alongside it as ``forecast_{zone}.json``.
    """
    if zone == TARGET_ZONE:
        return settings.export_path
    return settings.export_path.parent / f"forecast_{zone}.json"


def _read_export(path: Path) -> dict[str, Any]:
    """Read and parse a forecast export, or raise a 503 if it's missing.

    A missing export means bootstrap (not enough accumulated history yet
    for that zone) or every data-source fetch failing that hour — the
    same conditions the Jenkins deploy stage already treats as an
    expected, non-fatal state.

    Args:
        path: the export file to read (see ``_export_path_for_zone``).

    Returns:
        The parsed export payload.

    Raises:
        HTTPException: 503, if ``path`` doesn't exist yet.
    """
    if not path.exists():
        raise HTTPException(
            status_code=503,
            detail="No forecast has been produced yet for this zone (bootstrap, or every fetch "
            "failed this run) -- try again later.",
        )
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _validate_zone(zone: str) -> None:
    """Raise a 404 unless ``zone`` is one OKO publishes a forecast for."""
    if zone not in FLOW_TRACING_ZONES:
        raise HTTPException(status_code=404, detail=f"Unknown zone {zone!r}. See GET /zones.")


@app.get("/de.json", response_model=schemas.ForecastPayload)
def get_forecast(settings: SettingsDep) -> dict[str, Any]:
    """Return DE-LU's forecast export — see README's "API schema"."""
    return _read_export(settings.export_path)


@app.get("/exchanges.json", response_model=schemas.ExchangesPayload)
def get_exchanges(settings: SettingsDep) -> dict[str, Any]:
    """Return the network's latest cross-border physical flow snapshot.

    Registered *before* the ``/{zone}.json`` catch-all below -- FastAPI
    matches routes in registration order, and ``"exchanges"`` isn't a
    valid zone code, so this must win the match first.
    """
    return _read_export(settings.export_path.parent / "exchanges.json")


@app.get("/{zone}.json", response_model=schemas.ForecastPayload)
def get_forecast_for_zone(zone: str, settings: SettingsDep) -> dict[str, Any]:
    """Return one zone's forecast export. See GET /zones for the published list."""
    _validate_zone(zone)
    return _read_export(_export_path_for_zone(zone, settings))


@app.get("/api/evcc/co2", response_model=list[schemas.EvccRate])
def get_evcc_co2(settings: SettingsDep) -> list[dict[str, Any]]:
    """Return DE-LU's forecast reshaped into evcc's custom co2-tariff rate format."""
    return to_evcc_rates(_read_export(settings.export_path))


@app.get("/api/evcc/co2/{zone}", response_model=list[schemas.EvccRate])
def get_evcc_co2_for_zone(zone: str, settings: SettingsDep) -> list[dict[str, Any]]:
    """Return one zone's forecast reshaped into evcc's custom co2-tariff rate format."""
    _validate_zone(zone)
    return to_evcc_rates(_read_export(_export_path_for_zone(zone, settings)))


@app.get("/history/{zone}", response_model=list[schemas.HistoryPoint])
def get_history(
    zone: str, settings: SettingsDep, hours: int = DEFAULT_HISTORY_HOURS
) -> list[schemas.HistoryPoint]:
    """Return a zone's recent raw observed history.

    ``hours`` is clamped to ``MAX_HISTORY_QUERY_HOURS`` — this is a
    public, keyless endpoint, so the query window can't be unbounded.
    """
    _validate_zone(zone)
    if hours <= 0:
        raise HTTPException(status_code=400, detail="hours must be positive")
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=min(hours, MAX_HISTORY_QUERY_HOURS))
    points = query_recent(settings.sqlite_path, zone, since)
    return [
        schemas.HistoryPoint(
            timestamp=format_iso_z(point.timestamp),
            value=point.value_g_per_kwh,
            value_lifecycle=point.value_lifecycle_g_per_kwh,
            method=point.method,  # type: ignore[arg-type]
            power_breakdown_percent=point.breakdown_percent,
        )
        for point in points
    ]


@app.get("/zones", response_model=schemas.ZonesResponse)
def get_zones(settings: SettingsDep) -> schemas.ZonesResponse:
    """List every zone OKO publishes a forecast for, and whether each has data yet."""
    return schemas.ZonesResponse(
        zones=[
            schemas.ZoneStatus(zone=zone, available=_export_path_for_zone(zone, settings).exists())
            for zone in FLOW_TRACING_ZONES
        ]
    )


@app.get("/healthz")
def healthz() -> PlainTextResponse:
    """Plain liveness check, matching the previous nginx config's contract."""
    return PlainTextResponse("ok")


# Mounted last so it only ever catches requests the routes above didn't.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
