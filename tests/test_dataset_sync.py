"""Tests for oko-serve's dataset sync — uses git clone with LFS."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path
from unittest import mock

import httpx
import pytest

from oko.api.dataset_sync import sync_dataset
from oko.config import Settings
from oko.history import _get_query_connection, init_db, reset_query_connection


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "oko.sqlite3",
        export_path=tmp_path / "forecast_de.json",
        dataset_repo="tilalx/oko-dataset",
        dataset_ref="main",
        _env_file=None,  # type: ignore[call-arg]
    )


async def _sync(settings: Settings) -> None:
    async with httpx.AsyncClient() as client:
        await sync_dataset(settings, client)


def _repo_cache_dir(settings: Settings) -> Path:
    return settings.data_dir / ".dataset-cache"


def _populate_on_clone(files: dict[str, bytes]):
    """Build a subprocess.run stub that materializes `files` at the clone destination."""

    def mock_run(cmd, *args, **kwargs):
        if cmd[:2] == ["git", "clone"]:
            dest = Path(cmd[-1])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir(exist_ok=True)
            for name, content in files.items():
                (dest / name).write_bytes(content)
        return mock.MagicMock(returncode=0)

    return mock_run


def test_sync_clones_repo_and_pulls_lfs(tmp_path: Path, mocker: pytest.MockerFixture) -> None:
    """Verify sync clones repo and runs git lfs pull."""
    settings = _settings(tmp_path)

    mock_run = _populate_on_clone(
        {
            "oko.sqlite3": b"sqlite-bytes",
            "forecast_de.json": b'{"zone": "DE"}',
            "exchanges.json": b'{"exchanges": []}',
        }
    )
    mocker.patch("oko.api.dataset_sync.subprocess.run", side_effect=mock_run)

    _run(_sync(settings))

    assert settings.sqlite_path.read_bytes() == b"sqlite-bytes"
    assert settings.export_path.read_bytes() == b'{"zone": "DE"}'
    assert (tmp_path / "exchanges.json").read_bytes() == b'{"exchanges": []}'


def test_sync_reuses_cached_clone_on_second_run(
    tmp_path: Path, mocker: pytest.MockerFixture
) -> None:
    """Second sync should fetch/reset the persistent clone, not re-clone from scratch."""
    settings = _settings(tmp_path)

    mock_run = _populate_on_clone({"oko.sqlite3": b"sqlite-bytes"})
    mocked = mocker.patch("oko.api.dataset_sync.subprocess.run", side_effect=mock_run)

    _run(_sync(settings))
    assert _repo_cache_dir(settings).exists()
    clone_calls = [c for c in mocked.call_args_list if c.args[0][:2] == ["git", "clone"]]
    assert len(clone_calls) == 1

    _run(_sync(settings))
    clone_calls = [c for c in mocked.call_args_list if c.args[0][:2] == ["git", "clone"]]
    fetch_calls = [c for c in mocked.call_args_list if "fetch" in c.args[0]]
    assert len(clone_calls) == 1  # no re-clone
    assert len(fetch_calls) == 1


def test_sync_handles_clone_failure_gracefully(
    tmp_path: Path, mocker: pytest.MockerFixture
) -> None:
    """Verify sync handles git clone failure without raising."""
    settings = _settings(tmp_path)

    def mock_run(*args, **kwargs):
        raise FileNotFoundError("git command not found")

    mocker.patch("oko.api.dataset_sync.subprocess.run", side_effect=mock_run)

    _run(_sync(settings))  # must not raise

    assert not settings.sqlite_path.exists()
    assert not settings.export_path.exists()


def test_sync_resets_cached_query_connection_after_sqlite_replace(
    tmp_path: Path, mocker: pytest.MockerFixture
) -> None:
    """Verify query connection is reset when sqlite3 is updated."""
    settings = _settings(tmp_path)
    init_db(settings.sqlite_path)
    _get_query_connection(settings.sqlite_path)

    mock_run = _populate_on_clone({"oko.sqlite3": b"new-sqlite-bytes"})
    mocker.patch("oko.api.dataset_sync.subprocess.run", side_effect=mock_run)

    try:
        _run(_sync(settings))

        import oko.history as history_module

        assert history_module._query_conn is None
        assert settings.sqlite_path.read_bytes() == b"new-sqlite-bytes"
    finally:
        reset_query_connection()
