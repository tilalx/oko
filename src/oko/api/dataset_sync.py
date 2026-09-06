"""Sync published data from `oko-dataset` straight into the container.

Clones the dataset repo with Git LFS support to properly resolve LFS-tracked
files (oko.sqlite3, forecast_*.json). See `app.py`'s `lifespan` for how
this gets scheduled on container startup.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import tempfile
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


async def sync_dataset(settings: Settings, client: httpx.AsyncClient) -> None:
    """Clone dataset repo with Git LFS and copy files to their targets."""
    repo_url = f"https://github.com/{settings.dataset_repo}.git"
    target_files = _dataset_files(settings)

    with tempfile.TemporaryDirectory() as tmpdir:
        repo_path = Path(tmpdir) / "dataset"
        try:
            subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    settings.dataset_ref,
                    repo_url,
                    str(repo_path),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )
            subprocess.run(
                ["git", "-C", str(repo_path), "lfs", "install", "--local"],
                check=True,
                capture_output=True,
                timeout=30,
            )
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
