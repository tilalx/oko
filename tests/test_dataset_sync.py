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

    # Create a mock repo directory with files
    repo_root = tmp_path / "mock_repo"
    repo_root.mkdir()
    (repo_root / "dataset").mkdir()
    (repo_root / "dataset" / "oko.sqlite3").write_bytes(b"sqlite-bytes")
    (repo_root / "dataset" / "forecast_de.json").write_bytes(b'{"zone": "DE"}')
    (repo_root / "dataset" / "exchanges.json").write_bytes(b'{"exchanges": []}')

    def mock_tmpdir_contextmanager():
        class MockContextManager:
            def __enter__(self):
                return str(repo_root)

            def __exit__(self, *args):
                pass

        return MockContextManager()

    def mock_run(*args, **kwargs):
        # Simulate successful git commands
        return mock.MagicMock(returncode=0)

    mocker.patch("oko.api.dataset_sync.tempfile.TemporaryDirectory", mock_tmpdir_contextmanager)
    mocker.patch("oko.api.dataset_sync.subprocess.run", side_effect=mock_run)

    _run(_sync(settings))

    assert settings.sqlite_path.read_bytes() == b"sqlite-bytes"
    assert settings.export_path.read_bytes() == b'{"zone": "DE"}'
    assert (tmp_path / "exchanges.json").read_bytes() == b'{"exchanges": []}'


def test_sync_handles_clone_failure_gracefully(
    tmp_path: Path, mocker: pytest.MockerFixture
) -> None:
    """Verify sync handles git clone failure without raising."""
    settings = _settings(tmp_path)

    def mock_run(*args, **kwargs):
        raise FileNotFoundError("git command not found")

    def mock_tmpdir_contextmanager():
        class MockContextManager:
            def __enter__(self):
                return str(tmp_path)

            def __exit__(self, *args):
                pass

        return MockContextManager()

    mocker.patch("oko.api.dataset_sync.tempfile.TemporaryDirectory", mock_tmpdir_contextmanager)
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

    # Create mock repo with new sqlite content
    repo_root = tmp_path / "mock_repo"
    repo_root.mkdir()
    (repo_root / "dataset").mkdir()
    (repo_root / "dataset" / "oko.sqlite3").write_bytes(b"new-sqlite-bytes")

    def mock_tmpdir_contextmanager():
        class MockContextManager:
            def __enter__(self):
                return str(repo_root)

            def __exit__(self, *args):
                pass

        return MockContextManager()

    def mock_run(*args, **kwargs):
        return mock.MagicMock(returncode=0)

    mocker.patch("oko.api.dataset_sync.tempfile.TemporaryDirectory", mock_tmpdir_contextmanager)
    mocker.patch("oko.api.dataset_sync.subprocess.run", side_effect=mock_run)

    try:
        _run(_sync(settings))

        import oko.history as history_module

        assert history_module._query_conn is None
        assert settings.sqlite_path.read_bytes() == b"new-sqlite-bytes"
    finally:
        reset_query_connection()
