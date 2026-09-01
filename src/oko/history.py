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

from oko.forecast.features import FeatureRow

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
)


@dataclass(frozen=True, slots=True)
class HistoryRow:
    zone: str
    features: FeatureRow
    target_g_per_kwh: float
    lifecycle_g_per_kwh: float | None = None
    method: Literal["flow_trace", "one_hop_fallback"] | None = None
    breakdown_percent: dict[str, float] | None = None


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.execute(_SCHEMA)
        for column, sql_type in _MIGRATION_COLUMNS:
            with contextlib.suppress(sqlite3.OperationalError):
                conn.execute(f"ALTER TABLE intensity_history ADD COLUMN {column} {sql_type}")


def upsert_rows(path: Path, rows: Sequence[HistoryRow]) -> None:
    if not rows:
        return
    init_db(path)
    with sqlite3.connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO intensity_history
                (zone, timestamp, hour_sin, hour_cos, dow_sin, dow_cos,
                 month_sin, month_cos, residual_load_share, target_g_per_kwh,
                 lifecycle_g_per_kwh, method, breakdown_percent_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                breakdown_percent_json=excluded.breakdown_percent_json
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
        timestamp=dt.datetime.fromisoformat(timestamp_iso),
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


def load_breakdown_training_rows(
    path: Path, zone: str
) -> tuple[list[FeatureRow], list[dict[str, float]]]:
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
    timestamp: dt.datetime
    value_g_per_kwh: float
    value_lifecycle_g_per_kwh: float | None
    method: str | None
    breakdown_percent: dict[str, float] | None


MAX_HISTORY_QUERY_HOURS = 24 * 30


def query_recent(path: Path, zone: str, since: dt.datetime) -> list[HistoryPoint]:
    if not path.exists():
        return []
    init_db(path)  # tolerate a DB file whose schema predates a later migration column
    with sqlite3.connect(path) as conn:
        cursor = conn.execute(
            """
            SELECT timestamp, target_g_per_kwh, lifecycle_g_per_kwh, method, breakdown_percent_json
            FROM intensity_history
            WHERE zone = ? AND timestamp >= ?
            ORDER BY timestamp ASC
            """,
            (zone, since.isoformat()),
        )
        result = cursor.fetchall()
    return [
        HistoryPoint(
            timestamp=dt.datetime.fromisoformat(timestamp_iso),
            value_g_per_kwh=value,
            value_lifecycle_g_per_kwh=lifecycle_value,
            method=method,
            breakdown_percent=json.loads(breakdown_json) if breakdown_json is not None else None,
        )
        for timestamp_iso, value, lifecycle_value, method, breakdown_json in result
    ]
