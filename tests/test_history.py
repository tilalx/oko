"""Deterministic tests for the SQLite history store — no network access."""

from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from oko.forecast.features import FeatureRow
from oko.history import (
    HistoryRow,
    init_db,
    load_breakdown_training_rows,
    load_training_rows,
    query_recent,
    upsert_rows,
)

HOUR = dt.datetime(2026, 8, 31, 12, tzinfo=dt.UTC)


def _row(
    timestamp: dt.datetime,
    share: float,
    target: float,
    *,
    lifecycle: float | None = None,
    method: str | None = None,
    breakdown: dict[str, float] | None = None,
) -> HistoryRow:
    return HistoryRow(
        zone="DE-LU",
        features=FeatureRow(
            timestamp=timestamp,
            hour_sin=0.1,
            hour_cos=0.2,
            dow_sin=0.3,
            dow_cos=0.4,
            month_sin=0.5,
            month_cos=0.6,
            residual_load_share=share,
            horizon_hours=0,
        ),
        target_g_per_kwh=target,
        lifecycle_g_per_kwh=lifecycle,
        method=method,  # type: ignore[arg-type]
        breakdown_percent=breakdown,
    )


def test_load_training_rows_on_nonexistent_db_returns_empty(tmp_path: Path) -> None:
    rows, targets = load_training_rows(tmp_path / "missing.sqlite3", "DE-LU")
    assert rows == []
    assert targets == []


def test_init_db_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    init_db(db_path)
    init_db(db_path)  # must not raise
    assert db_path.exists()


def test_upsert_and_load_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    rows = [_row(HOUR, 0.4, 200.0), _row(HOUR + dt.timedelta(hours=1), 0.6, 300.0)]

    upsert_rows(db_path, rows)
    loaded_features, loaded_targets = load_training_rows(db_path, "DE-LU")

    assert [f.timestamp for f in loaded_features] == [HOUR, HOUR + dt.timedelta(hours=1)]
    assert loaded_targets == [200.0, 300.0]
    assert loaded_features[0].residual_load_share == 0.4
    assert loaded_features[0].horizon_hours == 0


def test_upsert_overwrites_existing_hour(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    upsert_rows(db_path, [_row(HOUR, 0.4, 200.0)])
    upsert_rows(db_path, [_row(HOUR, 0.9, 999.0)])  # same (zone, timestamp) -> overwrite

    loaded_features, loaded_targets = load_training_rows(db_path, "DE-LU")
    assert len(loaded_features) == 1
    assert loaded_targets == [999.0]
    assert loaded_features[0].residual_load_share == 0.9


def test_upsert_empty_list_is_a_noop(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    upsert_rows(db_path, [])
    assert not db_path.exists()


def test_load_training_rows_filters_by_zone(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    de_row = _row(HOUR, 0.4, 200.0)
    fr_row = HistoryRow(zone="FR", features=de_row.features, target_g_per_kwh=50.0)

    upsert_rows(db_path, [de_row, fr_row])

    de_features, de_targets = load_training_rows(db_path, "DE-LU")
    fr_features, fr_targets = load_training_rows(db_path, "FR")

    assert len(de_features) == 1 and de_targets == [200.0]
    assert len(fr_features) == 1 and fr_targets == [50.0]


def test_load_training_rows_sorted_by_timestamp(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    later = _row(HOUR + dt.timedelta(hours=5), 0.5, 100.0)
    earlier = _row(HOUR, 0.5, 50.0)

    upsert_rows(db_path, [later, earlier])
    loaded_features, _ = load_training_rows(db_path, "DE-LU")

    assert [f.timestamp for f in loaded_features] == [HOUR, HOUR + dt.timedelta(hours=5)]


def test_load_training_rows_lifecycle_target_excludes_null_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    with_lifecycle = _row(HOUR, 0.5, 200.0, lifecycle=250.0)
    without_lifecycle = _row(HOUR + dt.timedelta(hours=1), 0.5, 210.0)  # lifecycle=None

    upsert_rows(db_path, [with_lifecycle, without_lifecycle])

    direct_features, _direct_targets = load_training_rows(db_path, "DE-LU", target="direct")
    lifecycle_features, lifecycle_targets = load_training_rows(db_path, "DE-LU", target="lifecycle")

    assert len(direct_features) == 2
    assert len(lifecycle_features) == 1
    assert lifecycle_features[0].timestamp == HOUR
    assert lifecycle_targets == [250.0]


def test_query_recent_respects_since_and_returns_all_fields(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    old = _row(HOUR - dt.timedelta(hours=100), 0.5, 999.0)
    recent = _row(
        HOUR,
        0.5,
        250.0,
        lifecycle=310.0,
        method="flow_trace",
        breakdown={"wind": 60.0, "coal": 40.0},
    )
    upsert_rows(db_path, [old, recent])

    points = query_recent(db_path, "DE-LU", since=HOUR - dt.timedelta(hours=1))

    assert len(points) == 1
    assert points[0].timestamp == HOUR
    assert points[0].value_g_per_kwh == 250.0
    assert points[0].value_lifecycle_g_per_kwh == 310.0
    assert points[0].method == "flow_trace"
    assert points[0].breakdown_percent == {"wind": 60.0, "coal": 40.0}


def test_query_recent_breakdown_is_none_when_not_persisted(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    upsert_rows(db_path, [_row(HOUR, 0.5, 250.0)])

    points = query_recent(db_path, "DE-LU", since=HOUR - dt.timedelta(hours=1))

    assert points[0].breakdown_percent is None


def test_upsert_and_load_breakdown_training_rows_round_trip(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    with_breakdown = _row(HOUR, 0.5, 200.0, breakdown={"wind": 70.0, "gas": 30.0})
    without_breakdown = _row(HOUR + dt.timedelta(hours=1), 0.5, 210.0)

    upsert_rows(db_path, [with_breakdown, without_breakdown])
    features, breakdowns = load_breakdown_training_rows(db_path, "DE-LU")

    assert len(features) == 1
    assert features[0].timestamp == HOUR
    assert breakdowns == [{"wind": 70.0, "gas": 30.0}]


def test_load_breakdown_training_rows_on_nonexistent_db_returns_empty(tmp_path: Path) -> None:
    features, breakdowns = load_breakdown_training_rows(tmp_path / "missing.sqlite3", "DE-LU")
    assert features == []
    assert breakdowns == []


def test_query_recent_empty_for_missing_db(tmp_path: Path) -> None:
    assert query_recent(tmp_path / "missing.sqlite3", "DE-LU", since=HOUR) == []


def _create_legacy_table(db_path: Path) -> None:
    """Simulate a DB created before lifecycle_g_per_kwh/method/breakdown_percent_json existed."""
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE intensity_history (
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
            )
            """
        )
        conn.execute(
            "INSERT INTO intensity_history VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("DE-LU", HOUR.isoformat(), 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.5, 123.0),
        )


def test_init_db_migrates_a_pre_existing_table_without_the_new_columns(tmp_path: Path) -> None:
    db_path = tmp_path / "history.sqlite3"
    _create_legacy_table(db_path)

    init_db(db_path)  # must not raise, and the pre-existing row must survive

    features, targets = load_training_rows(db_path, "DE-LU")
    assert targets == [123.0]
    assert features[0].timestamp == HOUR


def test_query_recent_migrates_a_legacy_db_instead_of_raising(tmp_path: Path) -> None:
    """Regression: a read-only endpoint (GET /history) must not crash with
    'no such column: breakdown_percent_json' just because it's the first
    thing to touch a DB whose migration hasn't run in this process yet --
    it should self-migrate, same as any writer already does."""
    db_path = tmp_path / "history.sqlite3"
    _create_legacy_table(db_path)

    points = query_recent(db_path, "DE-LU", since=HOUR - dt.timedelta(hours=1))

    assert len(points) == 1
    assert points[0].value_g_per_kwh == 123.0
    assert points[0].breakdown_percent is None


def test_load_breakdown_training_rows_migrates_a_legacy_db_instead_of_raising(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "history.sqlite3"
    _create_legacy_table(db_path)

    features, breakdowns = load_breakdown_training_rows(db_path, "DE-LU")

    assert features == []
    assert breakdowns == []
