"""Sync published data from `oko-dataset` straight into the container.

Clones the dataset repo with Git LFS support to properly resolve LFS-tracked
files (oko.sqlite3, forecast_*.json). See `app.py`'s `lifespan` for how
this gets scheduled on container startup.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import httpx
import structlog

from oko.config import FLOW_TRACING_ZONES, TARGET_ZONE, Settings
from oko.history import reset_query_connection

logger = structlog.get_logger(__name__)


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


# Checkout operations (clone, reset --hard) run the LFS smudge filter inline,
# which would try to download the (multi-GB) LFS content synchronously and
# blow past these commands' short timeouts. Skip smudging here and let the
# dedicated `git lfs pull` step (its own, longer timeout) fetch the content.
_NO_SMUDGE_ENV = {**os.environ, "GIT_LFS_SKIP_SMUDGE": "1"}


def _clone(repo_url: str, ref: str, repo_path: Path) -> None:
    shutil.rmtree(repo_path, ignore_errors=True)
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", ref, repo_url, str(repo_path)],
        check=True,
        capture_output=True,
        timeout=60,
        env=_NO_SMUDGE_ENV,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "lfs", "install", "--local"],
        check=True,
        capture_output=True,
        timeout=30,
    )


def _fetch(ref: str, repo_path: Path) -> None:
    subprocess.run(
        ["git", "-C", str(repo_path), "fetch", "--depth", "1", "origin", ref],
        check=True,
        capture_output=True,
        timeout=240,
        env=_NO_SMUDGE_ENV,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "reset", "--hard", "FETCH_HEAD"],
        check=True,
        capture_output=True,
        timeout=30,
        env=_NO_SMUDGE_ENV,
    )


async def sync_dataset(settings: Settings, client: httpx.AsyncClient) -> None:
    """Update the local dataset checkout (with Git LFS) and copy files to their targets.

    Reuses a persistent local clone (`settings.data_dir / ".dataset-cache"`)
    across calls instead of a throwaway tempdir, so `git`/`git lfs` only
    transfer objects that actually changed since the last sync -- not the
    full multi-GB LFS-tracked dataset on every tick.
    """
    repo_url = f"https://github.com/{settings.dataset_repo}.git"
    target_files = _dataset_files(settings)
    repo_path = settings.data_dir / ".dataset-cache"

    try:
        if (repo_path / ".git").exists():
            try:
                _fetch(settings.dataset_ref, repo_path)
            except (subprocess.CalledProcessError, FileNotFoundError):
                _clone(repo_url, settings.dataset_ref, repo_path)
        else:
            _clone(repo_url, settings.dataset_ref, repo_path)
        subprocess.run(
            ["git", "-C", str(repo_path), "lfs", "pull"],
            check=True,
            capture_output=True,
            timeout=300,
        )
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        logger.warning("dataset_sync.clone_failed", error=str(exc))
        return

    for name, target in target_files.items():
        source = repo_path / name
        if not source.exists():
            logger.debug("dataset_sync.file_not_found", file=name)
            continue

        content = source.read_bytes()
        if target.exists() and target.read_bytes() == content:
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        logger.info("dataset_sync.updated", file=name, bytes=len(content))
        if target == settings.sqlite_path:
            reset_query_connection()


async def _main() -> None:
    async with httpx.AsyncClient() as client:
        await sync_dataset(Settings(), client)


if __name__ == "__main__":
    asyncio.run(_main())
