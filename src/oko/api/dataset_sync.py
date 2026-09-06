"""Sync published data from `oko-dataset` straight into the container.

Clones the dataset repo with Git LFS support to properly resolve LFS-tracked
files (oko.sqlite3, forecast_*.json). See `app.py`'s `lifespan` for how
this gets scheduled on container startup.
"""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
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


def _prune(repo_path: Path) -> None:
    """Drop LFS/git objects superseded by the fetch just applied.

    `--depth 1` + `reset --hard` only points refs at the new commit; it
    doesn't reclaim the old commit's now-unreachable git objects or the
    LFS blobs (the multi-GB `oko.sqlite3`, mainly) they reference, which
    otherwise accumulate in `.dataset-cache/.git` forever -- across a
    year of hourly syncs of a growing multi-GB sqlite file, that adds up
    to several GB of dead weight this repo will never read again.
    """
    subprocess.run(
        ["git", "-C", str(repo_path), "lfs", "prune", "--force"],
        check=False,
        capture_output=True,
        timeout=120,
    )
    subprocess.run(
        ["git", "-C", str(repo_path), "gc", "--prune=now", "--quiet"],
        check=False,
        capture_output=True,
        timeout=120,
    )


def _files_equal(source: Path, target: Path) -> bool:
    """Cheaply check whether ``target`` already holds ``source``'s content.

    Sizes differing is the common case and settles it with two `stat`
    calls. Only a same-size pair falls through to a streamed hash
    comparison -- for the multi-GB sqlite file, that avoids reading both
    copies fully into memory (as a plain byte-for-byte comparison would)
    just to conclude they already match.
    """
    if not target.exists():
        return False
    source_stat = source.stat()
    target_stat = target.stat()
    if source_stat.st_size != target_stat.st_size:
        return False

    def _hash(path: Path) -> bytes:
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(4 * 1024 * 1024), b""):
                digest.update(chunk)
        return digest.digest()

    return _hash(source) == _hash(target)


def _sync_dataset_blocking(settings: Settings) -> None:
    """Do the actual (blocking) clone/fetch/copy work; see ``sync_dataset``."""
    repo_url = f"https://github.com/{settings.dataset_repo}.git"
    target_files = _dataset_files(settings)
    repo_path = settings.data_dir / ".dataset-cache"

    # The cache dir is shared across serve's worker processes -- serialize
    # the whole sync (clone/fetch/lfs-pull *and* the file copy below) with
    # a cross-process lock so concurrent workers don't race to clone into
    # the same directory, or each pay the cost of hashing/copying the
    # multi-GB sqlite file at once, on every tick.
    repo_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = repo_path.parent / ".dataset-sync.lock"
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        try:
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
            _prune(repo_path)

            for name, target in target_files.items():
                source = repo_path / name
                if not source.exists():
                    logger.debug("dataset_sync.file_not_found", file=name)
                    continue
                if _files_equal(source, target):
                    continue

                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
                logger.info("dataset_sync.updated", file=name, bytes=source.stat().st_size)
                if target == settings.sqlite_path:
                    reset_query_connection()
        finally:
            fcntl.flock(lock_file, fcntl.LOCK_UN)


async def sync_dataset(settings: Settings, client: httpx.AsyncClient) -> None:
    """Update the local dataset checkout (with Git LFS) and copy files to their targets.

    Reuses a persistent local clone (`settings.data_dir / ".dataset-cache"`)
    across calls instead of a throwaway tempdir, so `git`/`git lfs` only
    transfer objects that actually changed since the last sync -- not the
    full multi-GB LFS-tracked dataset on every tick.

    All of the actual work (subprocesses, file hashing/copying) is
    synchronous and can take minutes on a big dataset, so it runs in a
    worker thread rather than inline -- otherwise it would block this
    process's entire asyncio event loop, and every in-flight HTTP request,
    for the duration of every sync tick.
    """
    await asyncio.to_thread(_sync_dataset_blocking, settings)


async def _main() -> None:
    async with httpx.AsyncClient() as client:
        await sync_dataset(Settings(), client)


if __name__ == "__main__":
    asyncio.run(_main())
