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
        sqlite_path=tmp_path / "oko.sqlite3",
        export_path=tmp_path / "forecast_de.json",
        dataset_repo="tilalx/oko-dataset",
        dataset_ref="main",
        _env_file=None,  # type: ignore[call-arg]
    )


async def _sync(settings: Settings) -> None:
    async with httpx.AsyncClient() as client:
        await sync_dataset(settings, client)


def test_sync_clones_repo_and_pulls_lfs(tmp_path: Path, mocker: pytest.MockerFixture) -> None:
    """Verify sync clones repo and runs git lfs pull."""
    settings = _settings(tmp_path)
    repo_path = tmp_path / "repo"
    repo_path.mkdir()

    # Mock subprocess.run to create dummy files in the clone dir
    def mock_run(*args, **kwargs):
        cmd = args[0] if args else kwargs.get("args", [])
        if "clone" in cmd:
            repo_path.mkdir(exist_ok=True)
            (repo_path / "oko.sqlite3").write_bytes(b"sqlite-bytes")
            (repo_path / "forecast_de.json").write_bytes(b'{"zone": "DE"}')
            (repo_path / "exchanges.json").write_bytes(b'{"exchanges": []}')
        return mock.MagicMock(returncode=0)

    mocker.patch("subprocess.run", side_effect=mock_run)
    mocker.patch("tempfile.TemporaryDirectory")

    # Mock tempfile.TemporaryDirectory to use our tmp_path
    def mock_tmpdir():
        class TmpDirCtx:
            def __enter__(self):
                return str(tmp_path)

            def __exit__(self, *args):
                pass

        return TmpDirCtx()

    mocker.patch("oko.api.dataset_sync.tempfile.TemporaryDirectory", mock_tmpdir)

    _run(_sync(settings))

    assert settings.sqlite_path.read_bytes() == b"sqlite-bytes"
    assert settings.export_path.read_bytes() == b'{"zone": "DE"}'
    assert (tmp_path / "exchanges.json").read_bytes() == b'{"exchanges": []}'


def test_sync_handles_missing_files(tmp_path: Path, mocker: pytest.MockerFixture) -> None:
    """Verify sync handles missing forecast files gracefully."""
    settings = _settings(tmp_path)
    repo_path = tmp_path / "repo"

    def mock_run(*args, **kwargs):
        repo_path.mkdir(exist_ok=True)
        # Only create sqlite3, not forecast files
        (repo_path / "oko.sqlite3").write_bytes(b"sqlite-bytes")
        return mock.MagicMock(returncode=0)

    mocker.patch("subprocess.run", side_effect=mock_run)

    def mock_tmpdir():
        class TmpDirCtx:
            def __enter__(self):
                return str(tmp_path)

            def __exit__(self, *args):
                pass

        return TmpDirCtx()

    mocker.patch("oko.api.dataset_sync.tempfile.TemporaryDirectory", mock_tmpdir)

    _run(_sync(settings))  # must not raise

    assert settings.sqlite_path.read_bytes() == b"sqlite-bytes"
    assert not settings.export_path.exists()


def test_sync_resets_cached_query_connection_after_sqlite_replace(
    tmp_path: Path, mocker: pytest.MockerFixture
) -> None:
    """Verify query connection is reset when sqlite3 is updated."""
    settings = _settings(tmp_path)
    init_db(settings.sqlite_path)
    _get_query_connection(settings.sqlite_path)
    repo_path = tmp_path / "repo"

    def mock_run(*args, **kwargs):
        repo_path.mkdir(exist_ok=True)
        (repo_path / "oko.sqlite3").write_bytes(b"new-sqlite-bytes")
        return mock.MagicMock(returncode=0)

    mocker.patch("subprocess.run", side_effect=mock_run)

    def mock_tmpdir():
        class TmpDirCtx:
            def __enter__(self):
                return str(tmp_path)

            def __exit__(self, *args):
                pass

        return TmpDirCtx()

    mocker.patch("oko.api.dataset_sync.tempfile.TemporaryDirectory", mock_tmpdir)

    try:
        _run(_sync(settings))

        import oko.history as history_module

        assert history_module._query_conn is None
        assert settings.sqlite_path.read_bytes() == b"new-sqlite-bytes"
    finally:
        reset_query_connection()
