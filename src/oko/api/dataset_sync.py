"""Sync published data from `oko-dataset` straight into the container.

Replaces the host-side `git clone` + cron `git pull` + read-only bind mount
described in README's "Deployment" section: `oko-serve` downloads
`oko.sqlite3`, each zone's `forecast_*.json`, and `exchanges.json` directly
from the (public) dataset repo's raw file URLs on a schedule, so no volume
is needed at all. See `app.py`'s `lifespan` for how this gets scheduled.

Only plain `raw.githubusercontent.com` GETs are used — never the
`api.github.com` Contents API — because the zone set is already known
statically (`FLOW_TRACING_ZONES`), so there's nothing to list, and raw file
downloads aren't subject to the unauthenticated API's 60 requests/hour quota.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import httpx
import structlog

from oko.config import FLOW_TRACING_ZONES, TARGET_ZONE, Settings
from oko.history import reset_query_connection

logger = structlog.get_logger(__name__)

RAW_BASE = "https://raw.githubusercontent.com"


def _forecast_filename(zone: str) -> str:
    return "forecast_de.json" if zone == TARGET_ZONE else f"forecast_{zone}.json"


def _dataset_files(settings: Settings) -> dict[str, Path]:
    """Map each remote filename in the dataset repo to its local target path."""
    export_dir = settings.export_path.parent
    files: dict[str, Path] = {
        "oko.sqlite3": settings.sqlite_path,
        "exchanges.json": export_dir / "exchanges.json",
    }
    for zone in FLOW_TRACING_ZONES:
        name = _forecast_filename(zone)
        files[name] = settings.export_path if zone == TARGET_ZONE else export_dir / name
    return files


async def _download_one(
    client: httpx.AsyncClient, settings: Settings, name: str, target: Path
) -> None:
    url = f"{RAW_BASE}/{settings.dataset_repo}/{settings.dataset_ref}/{name}"
    try:
        response = await client.get(url, timeout=settings.http_timeout_seconds)
    except httpx.HTTPError as exc:
        logger.warning("dataset_sync.fetch_failed", file=name, error=str(exc))
        return

    if response.status_code == 404:
        logger.debug("dataset_sync.not_published_yet", file=name)
        return
    if response.status_code != 200:
        logger.warning("dataset_sync.unexpected_status", file=name, status=response.status_code)
        return

    content = response.content
    if target.exists() and target.read_bytes() == content:
        return

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        os.replace(tmp_name, target)
    except BaseException:
        os.unlink(tmp_name)
        raise

    logger.info("dataset_sync.updated", file=name, bytes=len(content))
    if target == settings.sqlite_path:
        reset_query_connection()


async def sync_dataset(settings: Settings, client: httpx.AsyncClient) -> None:
    """Fetch every published dataset file that's newer than what's on disk."""
    for name, target in _dataset_files(settings).items():
        await _download_one(client, settings, name, target)
