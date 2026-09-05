"""FastAPI query layer: serves forecast/exchange payloads with in-memory caching.

Loads all zone forecast and exchange payloads into memory once per pipeline
publish (hourly), watches the output directory mtime to detect updates, and
serves from memory (near-zero latency). Provides ETag/Cache-Control headers
and HTTP 304 Not Modified for clients using conditional requests.

Rate-limited per IP (60 req/min default) to protect against abuse. Stateless
design allows horizontal scaling — multiple workers/replicas can run
identically, each syncing the same published dataset independently (see
`dataset_sync.py`) or against a shared read-only output volume.

See README's "Deployment" section for production setup.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any

import httpx
import structlog
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from limits import parse
from limits.storage import MemoryStorage
from limits.strategies import MovingWindowRateLimiter

from oko.api import schemas
from oko.api.dataset_sync import sync_dataset
from oko.api.evcc import to_evcc_rates
from oko.api.store import DataStore
from oko.config import FLOW_TRACING_ZONES, TARGET_ZONE, Settings, get_settings
from oko.history import MAX_HISTORY_QUERY_HOURS, HistoryPoint, query_recent
from oko.isoformat import format_iso_z

logger = structlog.get_logger(__name__)

#: Bundled web UI (see index.html) — plain static assets, no build step.
STATIC_DIR = Path(__file__).parent / "static"

#: Default window for GET /history/{zone} when ``hours`` isn't given.
DEFAULT_HISTORY_HOURS = 48

#: Per-IP rate limit: requests per minute on JSON endpoints.
RATE_LIMIT_SPEC = "60/minute"

_rate_limiter = MovingWindowRateLimiter(MemoryStorage())


def _check_rate_limit(request: Request) -> None:
    """Check if the client (by IP) has exceeded the rate limit.

    Raises HTTPException 429 if limit exceeded.
    """
    client_id = request.client.host if request.client else "unknown"
    if not _rate_limiter.hit(parse(RATE_LIMIT_SPEC), client_id):
        raise HTTPException(status_code=429, detail="rate limit exceeded")


def _seconds_until_next_boundary(interval_seconds: float) -> float:
    """Seconds from now until the next wall-clock multiple of ``interval_seconds``."""
    return interval_seconds - (time.time() % interval_seconds)


async def _dataset_sync_loop(settings: Settings) -> None:
    """Re-sync the dataset on a fixed wall-clock cadence until cancelled."""
    async with httpx.AsyncClient() as client:
        while True:
            interval = settings.dataset_sync_interval_seconds
            await asyncio.sleep(_seconds_until_next_boundary(interval))
            try:
                await sync_dataset(settings, client)
            except Exception:
                logger.exception("dataset_sync.tick_failed")


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """On startup, bootstrap the dataset and start syncing it periodically."""
    settings = get_settings()
    sync_task: asyncio.Task[None] | None = None
    if settings.dataset_sync_enabled:
        async with httpx.AsyncClient() as client:
            await sync_dataset(settings, client)
        sync_task = asyncio.create_task(_dataset_sync_loop(settings))
    try:
        yield
    finally:
        if sync_task is not None:
            sync_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await sync_task


app = FastAPI(
    title="OKO forecast API",
    description="Self-hosted, keyless CO2 intensity forecast for the DE-LU flow-tracing network.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "OPTIONS"],
)

SettingsDep = Annotated[Settings, Depends(get_settings)]

_data_store: DataStore | None = None
_data_store_dir: Path | None = None


def get_data_store(settings: SettingsDep) -> DataStore:
    """Lazy-initialize the data store singleton, rebuilding it if the export dir changes."""
    global _data_store, _data_store_dir
    export_dir = settings.export_path.parent
    if _data_store is None or _data_store_dir != export_dir:
        _data_store = DataStore(export_dir)
        _data_store_dir = export_dir
    return _data_store


StoreDep = Annotated[DataStore, Depends(get_data_store)]


def _key_for_zone(zone: str) -> str:
    """Map a zone to its DataStore key.

    ``TARGET_ZONE`` (DE-LU) uses 'forecast_de', other zones use 'forecast_{zone}'.
    """
    if zone == TARGET_ZONE:
        return "forecast_de"
    return f"forecast_{zone}"


def _get_export_with_cache(
    request: Request, store: DataStore, key: str
) -> JSONResponse | dict[str, Any]:
    """Fetch export from store and handle conditional requests (If-None-Match).

    Args:
        request: FastAPI request (may include If-None-Match header).
        store: The data store.
        key: The export key (e.g. 'forecast_de', 'exchanges').

    Returns:
        JSONResponse with 200/304 status, or raises HTTPException 503.
    """
    payload, etag, last_modified = store.get(key)
    if payload is None:
        raise HTTPException(
            status_code=503,
            detail="No forecast has been produced yet for this zone (bootstrap, or every fetch "
            "failed this run) -- try again later.",
        )

    headers: dict[str, str] = {
        "ETag": f'"{etag}"',
        "Cache-Control": "public, max-age=300",
    }
    if last_modified:
        headers["Last-Modified"] = last_modified

    if_none_match = request.headers.get("If-None-Match", "").strip('"')
    if if_none_match == etag:
        return JSONResponse(content={}, status_code=304, headers=headers)

    return JSONResponse(content=payload, headers=headers)


def _validate_zone(zone: str) -> None:
    """Raise a 404 unless ``zone`` is one OKO publishes a forecast for."""
    if zone not in FLOW_TRACING_ZONES:
        raise HTTPException(status_code=404, detail=f"Unknown zone {zone!r}. See GET /zones.")


@app.get("/de.json", response_model=schemas.ForecastPayload)
def get_forecast(
    settings: SettingsDep,
    store: StoreDep,
) -> JSONResponse:
    """Return DE-LU's forecast export — see README's "API schema"."""
    payload, etag, last_modified = store.get("forecast_de")
    if payload is None:
        raise HTTPException(
            status_code=503,
            detail="No forecast has been produced yet for this zone (bootstrap, or every fetch "
            "failed this run) -- try again later.",
        )
    headers: dict[str, str] = {
        "ETag": f'"{etag}"',
        "Cache-Control": "public, max-age=300",
    }
    if last_modified:
        headers["Last-Modified"] = last_modified
    return JSONResponse(content=payload, headers=headers)


@app.get("/exchanges.json", response_model=schemas.ExchangesPayload)
def get_exchanges(
    settings: SettingsDep,
    store: StoreDep,
) -> JSONResponse:
    """Return cross-border physical flow snapshot."""
    payload, etag, last_modified = store.get("exchanges")
    if payload is None:
        raise HTTPException(status_code=503, detail="Exchanges data not available yet.")
    headers: dict[str, str] = {
        "ETag": f'"{etag}"',
        "Cache-Control": "public, max-age=300",
    }
    if last_modified:
        headers["Last-Modified"] = last_modified
    return JSONResponse(content=payload, headers=headers)


@app.get("/{zone}.json", response_model=schemas.ForecastPayload)
def get_forecast_for_zone(
    zone: str,
    settings: SettingsDep,
    store: StoreDep,
) -> JSONResponse:
    """Return one zone's forecast export. See GET /zones for the published list."""
    _validate_zone(zone)
    payload, etag, last_modified = store.get(_key_for_zone(zone))
    if payload is None:
        raise HTTPException(status_code=503, detail="Forecast data not available yet.")
    headers: dict[str, str] = {
        "ETag": f'"{etag}"',
        "Cache-Control": "public, max-age=300",
    }
    if last_modified:
        headers["Last-Modified"] = last_modified
    return JSONResponse(content=payload, headers=headers)


@app.get("/api/evcc/co2")
def get_evcc_co2(
    settings: SettingsDep,
    store: StoreDep,
) -> JSONResponse:
    """Return DE-LU's forecast reshaped into evcc's custom co2-tariff rate format."""
    payload, _, _ = store.get("forecast_de")
    if payload is None:
        raise HTTPException(status_code=503, detail="Forecast data not available yet.")
    return JSONResponse(
        content=to_evcc_rates(payload),
        headers={
            "Cache-Control": "public, max-age=300",
        },
    )


@app.get("/api/evcc/co2/{zone}")
def get_evcc_co2_for_zone(
    zone: str,
    settings: SettingsDep,
    store: StoreDep,
) -> JSONResponse:
    """Return one zone's forecast reshaped into evcc's custom co2-tariff rate format."""
    _validate_zone(zone)
    payload, _, _ = store.get(_key_for_zone(zone))
    if payload is None:
        raise HTTPException(status_code=503, detail="Forecast data not available yet.")
    return JSONResponse(
        content=to_evcc_rates(payload),
        headers={
            "Cache-Control": "public, max-age=300",
        },
    )


def _serialize_history_points(points: list[HistoryPoint]) -> list[dict[str, Any]]:
    return [
        {
            "timestamp": format_iso_z(point.timestamp),
            "value": point.value_g_per_kwh,
            "value_lifecycle": point.value_lifecycle_g_per_kwh,
            "method": point.method,
            "power_breakdown_percent": point.breakdown_percent,
            "price_eur_per_mwh": point.price_eur_per_mwh,
        }
        for point in points
    ]


@app.get("/history/{zone}")
def get_history(
    zone: str,
    settings: SettingsDep,
    hours: int = DEFAULT_HISTORY_HOURS,
) -> JSONResponse:
    """Return a zone's recent raw observed history.

    ``hours`` clamped to ``MAX_HISTORY_QUERY_HOURS`` for bounded requests.
    """
    _validate_zone(zone)
    if hours <= 0:
        raise HTTPException(status_code=400, detail="hours must be positive")
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=min(hours, MAX_HISTORY_QUERY_HOURS))
    points = query_recent(settings.sqlite_path, zone, since)
    return JSONResponse(
        content=_serialize_history_points(points),
        headers={
            "Cache-Control": "public, max-age=300",
        },
    )


@app.get("/api/bulk", response_model=schemas.BulkResponse)
def get_bulk(
    settings: SettingsDep,
    store: StoreDep,
    hours: int = DEFAULT_HISTORY_HOURS,
) -> JSONResponse:
    """Every published zone's forecast + recent history in one response.

    The frontend's startup load otherwise fires 2 requests per zone (2*N
    total) -- individually cheap, but browsers cap concurrent connections
    per origin, so with dozens of zones most of those requests just queue.
    One response with everything already in memory (forecasts) or a fast
    local query (history) sidesteps that entirely.
    """
    if hours <= 0:
        raise HTTPException(status_code=400, detail="hours must be positive")
    since = dt.datetime.now(dt.UTC) - dt.timedelta(hours=min(hours, MAX_HISTORY_QUERY_HOURS))
    zones_payload: dict[str, dict[str, Any]] = {}
    for zone in FLOW_TRACING_ZONES:
        payload, _, _ = store.get(_key_for_zone(zone))
        points = query_recent(settings.sqlite_path, zone, since)
        zones_payload[zone] = {
            "forecast": payload,
            "history": _serialize_history_points(points),
        }
    return JSONResponse(
        content={"zones": zones_payload},
        headers={"Cache-Control": "public, max-age=300"},
    )


@app.get("/zones")
def get_zones(
    settings: SettingsDep,
    store: StoreDep,
) -> JSONResponse:
    """List every zone OKO publishes a forecast for, and whether each has data yet."""
    available_keys = store.keys()
    zones_response = {
        "zones": [
            {
                "zone": zone,
                "available": _key_for_zone(zone) in available_keys,
            }
            for zone in FLOW_TRACING_ZONES
        ]
    }
    return JSONResponse(
        content=zones_response,
        headers={
            "Cache-Control": "public, max-age=300",
        },
    )


@app.get("/healthz")
def healthz() -> PlainTextResponse:
    """Plain liveness check, matching the previous nginx config's contract."""
    return PlainTextResponse("ok")


# Mounted last so it only ever catches requests the routes above didn't.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
