"""SQLite-backed training history, swappable for Postgres later."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import structlog

from oko.forecast.features import FeatureRow, with_price_lag

logger = structlog.get_logger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intensity_history (
    zone TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    hour_sin REAL NOT NULL,
    hour_cos REAL NOT NULL,
    dow_sin REAL NOT NULL,
    dow_cos REAL NOT NULL,
    month_sin REAL NOT NULL,
    month_cos REAL NOT NULL,
    residual_load_share REAL NOT NULL,
    target_g_per_kwh REAL NOT NULL,
    PRIMARY KEY (zone, timestamp)
);
"""

_MIGRATION_COLUMNS: tuple[tuple[str, str], ...] = (
    ("lifecycle_g_per_kwh", "REAL"),
    ("method", "TEXT"),
    ("breakdown_percent_json", "TEXT"),
    ("price_eur_per_mwh", "REAL"),
)


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """One historical observation with features and targets."""

    zone: str
    features: FeatureRow
    target_g_per_kwh: float
    lifecycle_g_per_kwh: float | None = None
    method: Literal["flow_trace", "one_hop_fallback"] | None = None
    breakdown_percent: dict[str, float] | None = None
    price_eur_per_mwh: float | None = None


def init_db(path: Path) -> None:
    """Initialize database schema and apply migrations."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(_SCHEMA)
        for column, sql_type in _MIGRATION_COLUMNS:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute(f"ALTER TABLE intensity_history ADD COLUMN {column} {sql_type}")


def upsert_rows(path: Path, rows: Sequence[HistoryRow]) -> None:
    """Insert or update historical observations in database."""
    if not rows:
        return
    init_db(path)
    with sqlite3.connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO intensity_history
                (zone, timestamp, hour_sin, hour_cos, dow_sin, dow_cos,
                 month_sin, month_cos, residual_load_share, target_g_per_kwh,
                 lifecycle_g_per_kwh, method, breakdown_percent_json, price_eur_per_mwh)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(zone, timestamp) DO UPDATE SET
                hour_sin=excluded.hour_sin,
                hour_cos=excluded.hour_cos,
                dow_sin=excluded.dow_sin,
                dow_cos=excluded.dow_cos,
                month_sin=excluded.month_sin,
                month_cos=excluded.month_cos,
                residual_load_share=excluded.residual_load_share,
                target_g_per_kwh=excluded.target_g_per_kwh,
                lifecycle_g_per_kwh=excluded.lifecycle_g_per_kwh,
                method=excluded.method,
                breakdown_percent_json=excluded.breakdown_percent_json,
                price_eur_per_mwh=excluded.price_eur_per_mwh
            """,
            [
                (
                    row.zone,
                    row.features.timestamp.isoformat(),
                    row.features.hour_sin,
                    row.features.hour_cos,
                    row.features.dow_sin,
                    row.features.dow_cos,
                    row.features.month_sin,
                    row.features.month_cos,
                    row.features.residual_load_share,
                    row.target_g_per_kwh,
                    row.lifecycle_g_per_kwh,
                    row.method,
                    (
                        json.dumps(row.breakdown_percent, sort_keys=True)
                        if row.breakdown_percent is not None
                        else None
                    ),
                    row.price_eur_per_mwh,
                )
                for row in rows
            ],
        )
    logger.info("history.upserted", rows=len(rows))


def _feature_row_from_columns(
    timestamp_iso: str,
    hour_sin: float,
    hour_cos: float,
    dow_sin: float,
    dow_cos: float,
    month_sin: float,
    month_cos: float,
    residual_load_share: float,
) -> FeatureRow:
    return FeatureRow(
        timestamp=dt.datetime.fromisoformat(str(timestamp_iso)),
        hour_sin=hour_sin,
        hour_cos=hour_cos,
        dow_sin=dow_sin,
        dow_cos=dow_cos,
        month_sin=month_sin,
        month_cos=month_cos,
        residual_load_share=residual_load_share,
        horizon_hours=0,
    )


def load_training_rows(
    path: Path, zone: str, *, target: Literal["direct", "lifecycle"] = "direct"
) -> tuple[list[FeatureRow], list[float]]:
    """Load historical features and intensity targets for training."""
    if not path.exists():
        return [], []
    init_db(path)  # tolerate a DB file whose schema predates a later migration column
    target_column = "target_g_per_kwh" if target == "direct" else "lifecycle_g_per_kwh"
    with sqlite3.connect(path) as conn:
        cursor = conn.execute(
            f"""
            SELECT timestamp, hour_sin, hour_cos, dow_sin, dow_cos,
                   month_sin, month_cos, residual_load_share, {target_column}
            FROM intensity_history
            WHERE zone = ? AND {target_column} IS NOT NULL
            ORDER BY timestamp ASC
            """,
            (zone,),
        )
        result = cursor.fetchall()

    rows = []
    targets = []
    for (
        timestamp_iso,
        hour_sin,
        hour_cos,
        dow_sin,
        dow_cos,
        month_sin,
        month_cos,
        residual_load_share,
        target_value,
    ) in result:
        rows.append(
            _feature_row_from_columns(
                timestamp_iso,
                hour_sin,
                hour_cos,
                dow_sin,
                dow_cos,
                month_sin,
                month_cos,
                residual_load_share,
            )
        )
        targets.append(target_value)
    return rows, targets


def load_price_training_rows(path: Path, zone: str) -> tuple[list[FeatureRow], list[float]]:
    """Load historical features and day-ahead prices for training.

    Reads every persisted row for ``zone`` (not just ones with a price)
    so ``price_lag_168h`` can be filled from a full observed-price
    series, then keeps only rows that themselves have a target price --
    same output shape as before this feature was added.
    """
    if not path.exists():
        return [], []
    init_db(path)  # tolerate a DB file whose schema predates a later migration column
    with sqlite3.connect(path) as conn:
        cursor = conn.execute(
            """
            SELECT timestamp, hour_sin, hour_cos, dow_sin, dow_cos,
                   month_sin, month_cos, residual_load_share, price_eur_per_mwh
            FROM intensity_history
            WHERE zone = ?
            ORDER BY timestamp ASC
            """,
            (zone,),
        )
        result = cursor.fetchall()

    price_by_hour: dict[dt.datetime, float] = {}
    all_rows: list[tuple[FeatureRow, float | None]] = []
    for (
        timestamp_iso,
        hour_sin,
        hour_cos,
        dow_sin,
        dow_cos,
        month_sin,
        month_cos,
        residual_load_share,
        price_value,
    ) in result:
        feature_row = _feature_row_from_columns(
            timestamp_iso,
            hour_sin,
            hour_cos,
            dow_sin,
            dow_cos,
            month_sin,
            month_cos,
            residual_load_share,
        )
        if price_value is not None:
            price_by_hour[feature_row.timestamp] = price_value
        all_rows.append((feature_row, price_value))

    rows = [row for row, price_value in all_rows if price_value is not None]
    targets = [price_value for _, price_value in all_rows if price_value is not None]
    return with_price_lag(rows, price_by_hour), targets


def load_breakdown_training_rows(
    path: Path, zone: str
) -> tuple[list[FeatureRow], list[dict[str, float]]]:
    """Load historical features and power mix breakdowns for training."""
    if not path.exists():
        return [], []
    init_db(path)  # tolerate a DB file whose schema predates a later migration column
    with sqlite3.connect(path) as conn:
        cursor = conn.execute(
            """
            SELECT timestamp, hour_sin, hour_cos, dow_sin, dow_cos,
                   month_sin, month_cos, residual_load_share, breakdown_percent_json
            FROM intensity_history
            WHERE zone = ? AND breakdown_percent_json IS NOT NULL
            ORDER BY timestamp ASC
            """,
            (zone,),
        )
        result = cursor.fetchall()

    rows = []
    breakdowns = []
    for (
        timestamp_iso,
        hour_sin,
        hour_cos,
        dow_sin,
        dow_cos,
        month_sin,
        month_cos,
        residual_load_share,
        breakdown_json,
    ) in result:
        rows.append(
            _feature_row_from_columns(
                timestamp_iso,
                hour_sin,
                hour_cos,
                dow_sin,
                dow_cos,
                month_sin,
                month_cos,
                residual_load_share,
            )
        )
        breakdowns.append(json.loads(breakdown_json))
    return rows, breakdowns


@dataclass(frozen=True, slots=True)
class HistoryPoint:
    """One historical observation with intensity and breakdown."""

    timestamp: dt.datetime
    value_g_per_kwh: float
    value_lifecycle_g_per_kwh: float | None
    method: str | None
    breakdown_percent: dict[str, float] | None
    price_eur_per_mwh: float | None


MAX_HISTORY_QUERY_HOURS = 24 * 30

_query_conn: sqlite3.Connection | None = None
_query_conn_path: Path | None = None


def reset_query_connection() -> None:
    """Close and drop the cached query connection.

    Needed after the underlying file is replaced in place (e.g. an atomic
    `os.replace` from a dataset sync) — the cached connection holds the old
    inode open and would otherwise keep serving stale data forever.
    """
    global _query_conn, _query_conn_path
    if _query_conn is not None:
        _query_conn.close()
    _query_conn = None
    _query_conn_path = None


def _get_query_connection(path: Path) -> sqlite3.Connection:
    """Get or create a shared read-only connection for query_recent.

    Uses WAL mode for better concurrent read performance. The pipeline is
    the sole writer, so a single shared read connection avoids per-request
    connect overhead. check_same_thread=False allows the same connection
    object to be used across different threads (safe with WAL mode).
    """
    global _query_conn, _query_conn_path
    if _query_conn is None or _query_conn_path != path:
        if _query_conn is not None:
            _query_conn.close()
        init_db(path)
        _query_conn = sqlite3.connect(path, timeout=10.0, check_same_thread=False)
        _query_conn.execute("PRAGMA journal_mode=WAL")
        _query_conn_path = path
    return _query_conn


def query_recent(path: Path, zone: str, since: dt.datetime) -> list[HistoryPoint]:
    """Query historical observations since a given time."""
    if not path.exists():
        return []
    conn = _get_query_connection(path)
    cursor = conn.execute(
        """
        SELECT timestamp, target_g_per_kwh, lifecycle_g_per_kwh, method,
               breakdown_percent_json, price_eur_per_mwh
        FROM intensity_history
        WHERE zone = ? AND timestamp >= ?
        ORDER BY timestamp ASC
        """,
        (zone, since.isoformat()),
    )
    result = cursor.fetchall()
    return [
        HistoryPoint(
            timestamp=dt.datetime.fromisoformat(str(timestamp_iso)),
            value_g_per_kwh=value,
            value_lifecycle_g_per_kwh=lifecycle_value,
            method=method,
            breakdown_percent=json.loads(breakdown_json) if breakdown_json is not None else None,
            price_eur_per_mwh=price,
        )
        for timestamp_iso, value, lifecycle_value, method, breakdown_json, price in result
    ]


def load_recent_prices(path: Path, zone: str, since: dt.datetime) -> dict[dt.datetime, float]:
    """Load a zone's observed day-ahead prices since a given time, keyed by hour.

    Used at inference time to fill ``price_lag_168h`` (see
    ``oko.forecast.features.with_price_lag``) from prices already
    persisted in ``intensity_history`` — the lag always points at a past,
    already-observed hour, never one still ahead of ``now``.
    """
    if not path.exists():
        return {}
    conn = _get_query_connection(path)
    cursor = conn.execute(
        """
        SELECT timestamp, price_eur_per_mwh
        FROM intensity_history
        WHERE zone = ? AND timestamp >= ? AND price_eur_per_mwh IS NOT NULL
        ORDER BY timestamp ASC
        """,
        (zone, since.isoformat()),
    )
    return {dt.datetime.fromisoformat(str(timestamp_iso)): price for timestamp_iso, price in cursor}
