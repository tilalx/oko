"""Tests for oko-serve's dataset sync — no real network access (respx-mocked)."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from pathlib import Path

import httpx
import respx

from oko.api.dataset_sync import RAW_BASE, sync_dataset
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


def _url(settings: Settings, name: str) -> str:
    return f"{RAW_BASE}/{settings.dataset_repo}/{settings.dataset_ref}/{name}"


async def _sync(settings: Settings) -> None:
    async with httpx.AsyncClient() as client:
        await sync_dataset(settings, client)


@respx.mock
def test_sync_downloads_sqlite_and_forecast_files(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    respx.get(_url(settings, "oko.sqlite3")).mock(
        return_value=httpx.Response(200, content=b"sqlite-bytes")
    )
    respx.get(_url(settings, "forecast_de.json")).mock(
        return_value=httpx.Response(200, content=b'{"zone": "DE"}')
    )
    respx.get(_url(settings, "exchanges.json")).mock(
        return_value=httpx.Response(200, content=b'{"exchanges": []}')
    )
    # Every other zone: not published yet.
    respx.get(url__regex=r".*/forecast_.*\.json$").mock(return_value=httpx.Response(404))

    _run(_sync(settings))

    assert settings.sqlite_path.read_bytes() == b"sqlite-bytes"
    assert settings.export_path.read_bytes() == b'{"zone": "DE"}'
    assert (tmp_path / "exchanges.json").read_bytes() == b'{"exchanges": []}'


@respx.mock
def test_sync_skips_404_files_without_error(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))

    _run(_sync(settings))  # must not raise

    assert not settings.sqlite_path.exists()
    assert not settings.export_path.exists()


@respx.mock
def test_sync_skips_rewrite_when_content_unchanged(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    settings.export_path.write_bytes(b'{"zone": "DE"}')
    before_mtime = settings.export_path.stat().st_mtime_ns

    respx.get(_url(settings, "forecast_de.json")).mock(
        return_value=httpx.Response(200, content=b'{"zone": "DE"}')
    )
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    respx.get(_url(settings, "forecast_de.json")).mock(
        return_value=httpx.Response(200, content=b'{"zone": "DE"}')
    )

    _run(_sync(settings))

    assert settings.export_path.stat().st_mtime_ns == before_mtime


@respx.mock
def test_sync_resets_cached_query_connection_after_sqlite_replace(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    init_db(settings.sqlite_path)  # valid, empty sqlite file
    _get_query_connection(settings.sqlite_path)  # populate the module-level cache
    try:
        respx.get(_url(settings, "oko.sqlite3")).mock(
            return_value=httpx.Response(200, content=b"new-sqlite-bytes")
        )
        respx.get(url__regex=r".*").mock(return_value=httpx.Response(404))
        respx.get(_url(settings, "oko.sqlite3")).mock(
            return_value=httpx.Response(200, content=b"new-sqlite-bytes")
        )

        _run(_sync(settings))

        import oko.history as history_module

        assert history_module._query_conn is None
        assert settings.sqlite_path.read_bytes() == b"new-sqlite-bytes"
    finally:
        reset_query_connection()
