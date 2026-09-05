"""In-memory data cache for forecast/exchange payloads, refreshed on pipeline publish."""

from __future__ import annotations

import hashlib
import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class DataStore:
    """Thread-safe singleton cache for forecast/exchange payloads.

    Loads all `forecast_*.json` and `exchanges.json` files from disk once,
    then watches the output directory's mtime. On mtime change (pipeline
    publish), reloads the data. Between refreshes, serves from memory
    (negligible latency, zero disk I/O per request).

    Provides `ETag` values (content hash) and `Last-Modified` timestamps
    so clients can use HTTP conditional requests (If-None-Match) to avoid
    re-fetching unchanged data over the network.
    """

    def __init__(self, output_dir: Path) -> None:
        """Initialize the data store.

        Args:
            output_dir: Directory where pipeline writes forecast_*.json and exchanges.json.
        """
        self.output_dir = Path(output_dir)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}
        self._etags: dict[str, str] = {}
        self._last_modified: dict[str, str] = {}
        self._generation_id: str | None = None
        self._dir_mtime: float | None = None
        self._refresh()

    def _refresh(self) -> None:
        """Reload data from disk if output directory changed.

        Thread-safe: held under lock. Only re-reads files if the directory
        mtime or file set changed.
        """
        with self._lock:
            try:
                current_mtime = os.stat(self.output_dir).st_mtime
            except FileNotFoundError:
                logger.warning("output_dir not found", path=self.output_dir)
                return

            if self._dir_mtime is not None and self._dir_mtime == current_mtime:
                return

            self._dir_mtime = current_mtime

            new_data: dict[str, Any] = {}
            new_etags: dict[str, str] = {}
            new_lm: dict[str, str] = {}

            for json_file in sorted(self.output_dir.glob("forecast_*.json")) + sorted(
                self.output_dir.glob("exchanges.json")
            ):
                try:
                    content = json_file.read_text(encoding="utf-8")
                    payload = json.loads(content)

                    etag = hashlib.sha256(content.encode()).hexdigest()[:16]
                    mtime = datetime.fromtimestamp(json_file.stat().st_mtime)
                    lm = mtime.strftime("%a, %d %b %Y %H:%M:%S GMT")

                    key = json_file.stem
                    new_data[key] = payload
                    new_etags[key] = etag
                    new_lm[key] = lm

                except Exception as e:
                    logger.error(
                        "failed to load export file",
                        path=json_file,
                        error=str(e),
                    )

            self._data = new_data
            self._etags = new_etags
            self._last_modified = new_lm
            gen_id = hashlib.sha256(str(sorted(new_data.keys())).encode()).hexdigest()[:8]
            if self._generation_id != gen_id:
                self._generation_id = gen_id
                logger.info(
                    "data refreshed",
                    generation=gen_id,
                    zones=len(new_data),
                )

    def get(self, key: str) -> tuple[Any, str, str] | tuple[None, None, None]:
        """Fetch a payload by key (e.g. 'forecast_de', 'exchanges').

        Returns:
            (payload, etag, last_modified) or (None, None, None) if not found.
        """
        self._refresh()
        with self._lock:
            if key not in self._data:
                return None, None, None
            return (
                self._data[key],
                self._etags[key],
                self._last_modified[key],
            )

    def etag(self, key: str) -> str | None:
        """Get the ETag for a key without fetching the full payload."""
        self._refresh()
        with self._lock:
            return self._etags.get(key)

    def keys(self) -> set[str]:
        """Return all available keys."""
        self._refresh()
        with self._lock:
            return set(self._data.keys())
