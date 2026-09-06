"""Carbon-intensity forecast model: LightGBM gradient boosted regressor."""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import lightgbm as lgb
import numpy as np
import structlog

from oko.emissions.factors import CATEGORIES
from oko.forecast.features import (
    CARBON_FEATURE_COLUMNS,
    FEATURE_COLUMNS,
    PRICE_FEATURE_COLUMNS,
    FeatureRow,
)

logger = structlog.get_logger(__name__)

Confidence = Literal["high", "medium", "low"]

CONFIDENCE_HIGH_MAX_HOURS = 24
CONFIDENCE_MEDIUM_MAX_HOURS = 72

DEFAULT_LGB_PARAMS: dict[str, object] = {
    "objective": "regression",
    "metric": "mae",
    "num_leaves": 15,
    "learning_rate": 0.05,
    "min_data_in_leaf": 20,
    "verbosity": -1,
}
DEFAULT_NUM_BOOST_ROUND = 200

#: Upper bound on boosting rounds when training with early stopping --
#: actual rounds used are chosen by validation MAE, not this constant
#: (see ``_train_with_early_stopping``); kept generous since early
#: stopping is what prevents overfitting, not this cap.
MAX_NUM_BOOST_ROUND = 2000

#: Fraction of training rows held out, chronologically (most recent rows
#: last), as an early-stopping validation set. Chronological rather than
#: random: a random split would let the model implicitly "see the future"
#: relative to nearby validation rows, understating real generalisation
#: error for a time series.
VALIDATION_FRACTION = 0.2

#: Rounds without validation-metric improvement before stopping early.
EARLY_STOPPING_ROUNDS = 20

#: Below this many rows, a chronological 80/20 split leaves too little in
#: either half to be a meaningful validation signal -- fall back to a
#: fixed ``DEFAULT_NUM_BOOST_ROUND`` training run instead.
MIN_ROWS_FOR_EARLY_STOPPING = 50

#: Row-count breakpoints (accumulated training hours) at which
#: ``_scaled_lgb_params`` steps up model capacity -- roughly "~90 days"
#: and "~1 year" of hourly history. A fixed config sized for the
#: bootstrap floor (336 rows) underfits the extra signal available once
#: a zone has accumulated months/years of history; early stopping
#: (already in place) is what prevents these larger configs from
#: overfitting, not a small ``num_leaves``. A simple discrete-band
#: heuristic, not a tuned/searched config.
_LGB_SCALE_BREAKPOINTS: tuple[tuple[int, int, int], ...] = (
    # (min_rows, num_leaves, min_data_in_leaf)
    (24 * 365, 63, 50),
    (24 * 90, 31, 30),
    (0, 15, 20),
)


def _scaled_lgb_params(n_rows: int) -> dict[str, object]:
    """``DEFAULT_LGB_PARAMS`` with capacity scaled to how much data there is.

    See ``_LGB_SCALE_BREAKPOINTS``. Used as the default whenever a
    caller doesn't pass explicit ``params`` -- explicit ``params`` are
    never overridden by this.
    """
    for min_rows, num_leaves, min_data_in_leaf in _LGB_SCALE_BREAKPOINTS:
        if n_rows >= min_rows:
            return {
                **DEFAULT_LGB_PARAMS,
                "num_leaves": num_leaves,
                "min_data_in_leaf": min_data_in_leaf,
            }
    return dict(DEFAULT_LGB_PARAMS)  # pragma: no cover -- breakpoints cover n_rows >= 0


#: Half-life (days) for recency-weighting training rows -- a row this
#: many days older than the newest one carries half the loss weight of
#: the newest row, softly discounting stale data (grid-composition drift:
#: capacity buildout, generator retirements) without truncating the
#: ever-growing training window outright. Early stopping (already in
#: place) still governs overfitting; this only reweights the loss.
RECENCY_HALF_LIFE_DAYS = 180.0


def _recency_weights(rows: Sequence[FeatureRow]) -> np.ndarray:
    """Exponential-decay sample weights, newest row's timestamp = weight 1.0.

    See ``RECENCY_HALF_LIFE_DAYS``. Rows need not be sorted; age is
    computed relative to ``max(row.timestamp for row in rows)``.
    """
    newest = max(row.timestamp for row in rows)
    age_days = np.array([(newest - row.timestamp).total_seconds() / 86400.0 for row in rows])
    return np.exp2(-age_days / RECENCY_HALF_LIFE_DAYS)


def _train_with_early_stopping(
    matrix: np.ndarray,
    targets: np.ndarray,
    *,
    params: dict[str, object] | None,
    weights: np.ndarray | None = None,
) -> lgb.Booster:
    """Train one booster, choosing boosting rounds via chronological validation.

    Args:
        matrix: feature matrix, rows in chronological order (callers pass
            already-sorted training rows).
        targets: one target value per row, same order as ``matrix``.
        params: LightGBM parameters; defaults to ``DEFAULT_LGB_PARAMS``.
        weights: optional per-row sample weight, same order as ``matrix``
            (see ``_recency_weights``); ``None`` trains unweighted.

    Returns:
        A booster trained with ``lgb.early_stopping`` on the last
        ``VALIDATION_FRACTION`` of rows, or -- for too little data to
        split meaningfully -- one trained for a fixed
        ``DEFAULT_NUM_BOOST_ROUND`` on everything.
    """
    n = len(targets)
    if n < MIN_ROWS_FOR_EARLY_STOPPING:
        dataset = lgb.Dataset(matrix, label=targets, weight=weights)
        return lgb.train(
            params or DEFAULT_LGB_PARAMS, dataset, num_boost_round=DEFAULT_NUM_BOOST_ROUND
        )

    split = max(1, int(n * (1 - VALIDATION_FRACTION)))
    train_weights = weights[:split] if weights is not None else None
    valid_weights = weights[split:] if weights is not None else None
    train_dataset = lgb.Dataset(matrix[:split], label=targets[:split], weight=train_weights)
    valid_dataset = lgb.Dataset(
        matrix[split:], label=targets[split:], weight=valid_weights, reference=train_dataset
    )
    return lgb.train(
        params or DEFAULT_LGB_PARAMS,
        train_dataset,
        num_boost_round=MAX_NUM_BOOST_ROUND,
        valid_sets=[valid_dataset],
        callbacks=[lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False)],
    )


def confidence_for_horizon(
    horizon_hours: int,
    *,
    high_max_hours: int = CONFIDENCE_HIGH_MAX_HOURS,
    medium_max_hours: int = CONFIDENCE_MEDIUM_MAX_HOURS,
) -> Confidence:
    """Confidence level based on forecast horizon.

    ``high_max_hours``/``medium_max_hours`` default to fixed constants,
    but callers that have measured per-horizon error (e.g. from
    ``oko.forecast.backtest.walk_forward_backtest``, binned by
    ``backtest.day_of_horizon``) can pass thresholds derived from actual
    error growth instead -- e.g. the hour at which model MAE crosses a
    "no longer meaningfully better than naive persistence" bar, rather
    than an arbitrary 24/72h guess.
    """
    if horizon_hours <= high_max_hours:
        return "high"
    if horizon_hours <= medium_max_hours:
        return "medium"
    return "low"


@dataclass(frozen=True, slots=True)
class Prediction:
    """One forecast hour's predicted carbon intensity."""

    timestamp: dt.datetime
    value_g_per_kwh: float
    confidence: Confidence
    value_lifecycle_g_per_kwh: float | None = None
    power_breakdown_percent: dict[str, float] | None = None
    price_eur_per_mwh: float | None = None


def _to_matrix(rows: list[FeatureRow], columns: Sequence[str] = FEATURE_COLUMNS) -> np.ndarray:
    return np.array([[row.as_dict()[col] for col in columns] for row in rows], dtype=float)


class CarbonIntensityModel:
    """LightGBM model for carbon intensity forecasting."""

    def __init__(self, booster: lgb.Booster, *, log_target: bool = False) -> None:
        """Wrap an already-trained LightGBM booster; prefer ``train``/``load``."""
        self._booster = booster
        self._log_target = log_target

    @classmethod
    def train(
        cls,
        rows: list[FeatureRow],
        targets: list[float],
        *,
        params: dict[str, object] | None = None,
        log_target: bool = True,
        use_recency_weighting: bool = True,
    ) -> CarbonIntensityModel:
        """Train a new model on historical feature rows and their observed intensity.

        Args:
            rows: training feature rows (``horizon_hours`` should be 0 for
                all of them — see ``build_training_features``).
            targets: observed carbon intensity, g CO2eq/kWh, one per row,
                same order as ``rows``.
            params: LightGBM parameters; defaults to ``_scaled_lgb_params(len(rows))``
                (see ``DEFAULT_LGB_PARAMS``).
            log_target: fit on ``log1p(targets)`` instead of raw g CO2eq/kWh
                (inverted back via ``expm1`` in ``predict``) -- carbon
                intensity is bounded at 0 and often right-skewed, which an
                MAE-objective booster can fit better in log space. Default
                ``True``; pass ``False`` to compare against the previous
                raw-target behavior (e.g. via the backtest).
            use_recency_weighting: down-weight older training rows (see
                ``_recency_weights``) so an ever-growing training window
                tracks grid-composition drift instead of treating a
                multi-year-old hour the same as yesterday's. Default
                ``True``.

        Returns:
            A fitted ``CarbonIntensityModel``. Boosting rounds are chosen
            via chronological validation + early stopping rather than a
            fixed count — see ``_train_with_early_stopping``.

        Raises:
            ValueError: if ``rows`` and ``targets`` don't line up, or
                there's nothing to train on.
        """
        if len(rows) != len(targets):
            raise ValueError(f"rows ({len(rows)}) and targets ({len(targets)}) length mismatch")
        if not rows:
            raise ValueError("Cannot train on an empty dataset")

        target_array = np.array(targets, dtype=float)
        if log_target:
            target_array = np.log1p(target_array)
        weights = _recency_weights(rows) if use_recency_weighting else None
        booster = _train_with_early_stopping(
            _to_matrix(rows, CARBON_FEATURE_COLUMNS),
            target_array,
            params=params or _scaled_lgb_params(len(rows)),
            weights=weights,
        )
        logger.info(
            "model.trained",
            rows=len(rows),
            best_iteration=booster.best_iteration,
            log_target=log_target,
        )
        return cls(booster, log_target=log_target)

    def predict(
        self,
        rows: list[FeatureRow],
        *,
        high_max_hours: int | None = None,
        medium_max_hours: int | None = None,
    ) -> list[Prediction]:
        """Predict carbon intensity for a set of feature rows.

        Args:
            rows: feature rows to predict for (typically from
                ``build_forecast_features``).
            high_max_hours: custom high-confidence horizon threshold
                (hours); defaults to ``CONFIDENCE_HIGH_MAX_HOURS``.
            medium_max_hours: custom medium-confidence horizon threshold
                (hours); defaults to ``CONFIDENCE_MEDIUM_MAX_HOURS``.

        Returns:
            One ``Prediction`` per input row, same order, negative
            predictions clamped to 0 (intensity can't be negative).
        """
        if not rows:
            return []
        raw = self._booster.predict(_to_matrix(rows, CARBON_FEATURE_COLUMNS))
        if self._log_target:
            raw = np.expm1(raw)
        return [
            Prediction(
                timestamp=row.timestamp,
                value_g_per_kwh=max(float(value), 0.0),
                confidence=confidence_for_horizon(
                    row.horizon_hours,
                    high_max_hours=high_max_hours or CONFIDENCE_HIGH_MAX_HOURS,
                    medium_max_hours=medium_max_hours or CONFIDENCE_MEDIUM_MAX_HOURS,
                ),
            )
            for row, value in zip(rows, raw, strict=True)
        ]

    def save(self, path: Path) -> None:
        """Save the trained booster to ``path`` (LightGBM's native text format).

        Also writes a small ``<path>.meta.json`` sidecar recording
        whether this model was trained with ``log_target`` -- needed so
        ``load`` can invert predictions the same way.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        self._booster.save_model(str(path))
        path.with_suffix(path.suffix + ".meta.json").write_text(
            json.dumps({"log_target": self._log_target})
        )
        logger.info("model.saved", path=str(path))

    @classmethod
    def load(cls, path: Path) -> CarbonIntensityModel:
        """Load a previously saved booster from ``path``.

        Reads the ``log_target`` flag from the ``.meta.json`` sidecar
        written by ``save``; defaults to ``False`` if it's missing (a
        model saved before this flag existed).
        """
        booster = lgb.Booster(model_file=str(path))
        meta_path = path.with_suffix(path.suffix + ".meta.json")
        log_target = False
        if meta_path.exists():
            log_target = bool(json.loads(meta_path.read_text()).get("log_target", False))
        return cls(booster, log_target=log_target)


@dataclass(frozen=True, slots=True)
class PricePrediction:
    """One forecast hour's predicted day-ahead price."""

    timestamp: dt.datetime
    price_eur_per_mwh: float
    confidence: Confidence


class PriceModel:
    """LightGBM model for day-ahead price forecasting.

    Same "perfect prognosis" shape as ``CarbonIntensityModel`` (see
    ``oko.forecast.features``), extended with one price-specific
    autocorrelation feature (``price_lag_168h`` -- see
    ``PRICE_FEATURE_COLUMNS``/``with_price_lag``). Predictions are *not*
    clamped to 0, since day-ahead prices can legitimately go negative.
    """

    def __init__(self, booster: lgb.Booster) -> None:
        """Wrap an already-trained LightGBM booster; prefer ``train``/``load``."""
        self._booster = booster

    @classmethod
    def train(
        cls,
        rows: list[FeatureRow],
        targets: list[float],
        *,
        params: dict[str, object] | None = None,
        use_recency_weighting: bool = True,
    ) -> PriceModel:
        """Train a new model on historical feature rows and their observed price.

        Args:
            rows: training feature rows (``horizon_hours`` should be 0 for
                all of them — see ``build_training_features``).
            targets: observed day-ahead price, EUR/MWh, one per row, same
                order as ``rows``.
            params: LightGBM parameters; defaults to ``DEFAULT_LGB_PARAMS``.
            use_recency_weighting: down-weight older training rows (see
                ``_recency_weights``); default ``True``.

        Returns:
            A fitted ``PriceModel``. Boosting rounds are chosen via
            chronological validation + early stopping (see
            ``_train_with_early_stopping``).

        Raises:
            ValueError: if ``rows`` and ``targets`` don't line up, or
                there's nothing to train on.
        """
        if len(rows) != len(targets):
            raise ValueError(f"rows ({len(rows)}) and targets ({len(targets)}) length mismatch")
        if not rows:
            raise ValueError("Cannot train on an empty dataset")

        weights = _recency_weights(rows) if use_recency_weighting else None
        booster = _train_with_early_stopping(
            _to_matrix(rows, PRICE_FEATURE_COLUMNS),
            np.array(targets, dtype=float),
            params=params or _scaled_lgb_params(len(rows)),
            weights=weights,
        )
        logger.info("price_model.trained", rows=len(rows), best_iteration=booster.best_iteration)
        return cls(booster)

    def predict(
        self,
        rows: list[FeatureRow],
        *,
        high_max_hours: int | None = None,
        medium_max_hours: int | None = None,
    ) -> list[PricePrediction]:
        """Predict day-ahead price for a set of feature rows.

        Args:
            rows: feature rows to predict for (typically from
                ``build_forecast_features``).
            high_max_hours: custom high-confidence horizon threshold
                (hours); defaults to ``CONFIDENCE_HIGH_MAX_HOURS``.
            medium_max_hours: custom medium-confidence horizon threshold
                (hours); defaults to ``CONFIDENCE_MEDIUM_MAX_HOURS``.

        Returns:
            One ``PricePrediction`` per input row, same order. Unlike
            ``CarbonIntensityModel.predict``, negative values are kept
            as-is (negative day-ahead prices are a real market outcome).
        """
        if not rows:
            return []
        raw = self._booster.predict(_to_matrix(rows, PRICE_FEATURE_COLUMNS))
        return [
            PricePrediction(
                timestamp=row.timestamp,
                price_eur_per_mwh=float(value),
                confidence=confidence_for_horizon(
                    row.horizon_hours,
                    high_max_hours=high_max_hours or CONFIDENCE_HIGH_MAX_HOURS,
                    medium_max_hours=medium_max_hours or CONFIDENCE_MEDIUM_MAX_HOURS,
                ),
            )
            for row, value in zip(rows, raw, strict=True)
        ]

    def save(self, path: Path) -> None:
        """Save the trained booster to ``path`` (LightGBM's native text format)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._booster.save_model(str(path))
        logger.info("price_model.saved", path=str(path))

    @classmethod
    def load(cls, path: Path) -> PriceModel:
        """Load a previously saved booster from ``path``."""
        booster = lgb.Booster(model_file=str(path))
        return cls(booster)


@dataclass(frozen=True, slots=True)
class BreakdownPrediction:
    """One forecast hour's predicted generation mix.

    Attributes:
        timestamp: UTC hour this prediction is for.
        power_breakdown_percent: category -> percent of total production,
            summing to 100 (see ``BreakdownModel.predict``).
    """

    timestamp: dt.datetime
    power_breakdown_percent: dict[str, float]


#: How far back the per-category breakdown autocorrelation feature looks
#: -- same 168h choice as ``INTENSITY_LAG_HOURS``/``PRICE_LAG_HOURS``.
BREAKDOWN_LAG_HOURS = 168


def _breakdown_lag_column(
    rows: Sequence[FeatureRow],
    breakdown_by_hour: Mapping[dt.datetime, Mapping[str, float]],
    category: str,
    lag_hours: int,
) -> np.ndarray:
    """That ``category``'s own share ``lag_hours`` before each row's timestamp, NaN if unknown."""
    lag = dt.timedelta(hours=lag_hours)
    return np.array(
        [breakdown_by_hour.get(row.timestamp - lag, {}).get(category, np.nan) for row in rows],
        dtype=float,
    )


class BreakdownModel:
    """One LightGBM regressor per generation category, predicting a future power mix.

    Same "perfect prognosis" idea as ``CarbonIntensityModel`` -- trained
    on realised, historical mix shares (see
    ``oko.history.load_breakdown_training_rows``) and queried with
    forecast-time feature rows built the same way -- but a mix has one
    target per category rather than a single scalar, so this wraps one
    independently-trained booster per category instead of one booster
    total. Each booster has no knowledge of the others, so nothing
    constrains their predictions to sum to any particular total --
    ``predict`` clips and renormalizes for that reason.

    Each category's booster also sees one extra feature beyond
    ``FEATURE_COLUMNS``: that category's own observed share
    ``BREAKDOWN_LAG_HOURS`` before the target hour (NaN, handled natively
    by LightGBM, when unavailable -- bootstrap window, or ``predict``
    called without ``breakdown_history``) -- the same autocorrelation
    idea as ``CarbonIntensityModel``'s intensity lags, just per-category
    since a mix has no single scalar to lag.
    """

    def __init__(self, boosters: dict[str, lgb.Booster]) -> None:
        """Wrap already-trained per-category boosters; prefer ``train``/``load``."""
        self._boosters = boosters

    @classmethod
    def train(
        cls,
        rows: list[FeatureRow],
        breakdowns: list[dict[str, float]],
        *,
        categories: Sequence[str] = CATEGORIES,
        params: dict[str, object] | None = None,
        use_recency_weighting: bool = True,
    ) -> BreakdownModel:
        """Train one booster per category on historical feature rows and their observed mix.

        Args:
            rows: training feature rows (``horizon_hours`` should be 0
                for all of them, same as ``CarbonIntensityModel.train``).
            breakdowns: observed generation mix for the same hour, one
                dict per row, same order as ``rows`` -- a category absent
                from a given hour's dict is treated as a 0% share for
                that hour (it simply didn't produce that hour, not a
                missing measurement).
            categories: which categories to train a booster for; defaults
                to every category OKO tracks (see
                ``oko.emissions.factors.CATEGORIES``).
            params: LightGBM parameters; defaults to ``DEFAULT_LGB_PARAMS``.
            use_recency_weighting: down-weight older training rows (see
                ``_recency_weights``); default ``True``.

        Returns:
            A fitted ``BreakdownModel``. Each category's boosting rounds
            are chosen independently via chronological validation + early
            stopping (see ``_train_with_early_stopping``).

        Raises:
            ValueError: if ``rows`` and ``breakdowns`` don't line up, or
                there's nothing to train on.
        """
        if len(rows) != len(breakdowns):
            msg = f"rows ({len(rows)}) and breakdowns ({len(breakdowns)}) length mismatch"
            raise ValueError(msg)
        if not rows:
            raise ValueError("Cannot train on an empty dataset")

        base_matrix = _to_matrix(rows)
        breakdown_by_hour = dict(zip((r.timestamp for r in rows), breakdowns, strict=True))
        resolved_params = params or _scaled_lgb_params(len(rows))
        weights = _recency_weights(rows) if use_recency_weighting else None
        boosters: dict[str, lgb.Booster] = {}
        for category in categories:
            targets = np.array([b.get(category, 0.0) for b in breakdowns], dtype=float)
            lag_column = _breakdown_lag_column(
                rows, breakdown_by_hour, category, BREAKDOWN_LAG_HOURS
            )
            matrix = np.column_stack([base_matrix, lag_column])
            boosters[category] = _train_with_early_stopping(
                matrix, targets, params=resolved_params, weights=weights
            )
        logger.info("breakdown_model.trained", rows=len(rows), categories=len(boosters))
        return cls(boosters)

    def predict(
        self,
        rows: list[FeatureRow],
        *,
        breakdown_history: Mapping[dt.datetime, Mapping[str, float]] | None = None,
    ) -> list[BreakdownPrediction]:
        """Predict a generation mix for a set of feature rows.

        Args:
            rows: feature rows to predict for (typically from
                ``build_forecast_features``).
            breakdown_history: observed generation mix, hour -> category
                -> percent, used to fill each category's
                ``BREAKDOWN_LAG_HOURS`` autocorrelation feature (see
                ``oko.history.load_recent_breakdowns``). Omitted/``None``
                (or an hour missing from it) leaves that row's lag
                feature as NaN, handled natively by LightGBM -- e.g. the
                bootstrap window, or a caller that doesn't have it yet.

        Returns:
            One ``BreakdownPrediction`` per input row, same order.
            Negative per-category predictions are clipped to 0, then each
            row's category shares are renormalized to sum to 100 -- the
            individual boosters don't otherwise guarantee that.
        """
        if not rows:
            return []
        base_matrix = _to_matrix(rows)
        history = breakdown_history or {}
        raw = {}
        for category, booster in self._boosters.items():
            lag_column = _breakdown_lag_column(rows, history, category, BREAKDOWN_LAG_HOURS)
            matrix = np.column_stack([base_matrix, lag_column])
            raw[category] = np.clip(booster.predict(matrix), 0.0, None)
        predictions = []
        for i, row in enumerate(rows):
            shares = {category: float(values[i]) for category, values in raw.items()}
            total = sum(shares.values())
            if total > 0:
                shares = {category: 100.0 * value / total for category, value in shares.items()}
            predictions.append(
                BreakdownPrediction(timestamp=row.timestamp, power_breakdown_percent=shares)
            )
        return predictions

    def save(self, dir_path: Path) -> None:
        """Save every category's booster under ``dir_path`` (one file per category)."""
        dir_path.mkdir(parents=True, exist_ok=True)
        for category, booster in self._boosters.items():
            booster.save_model(str(dir_path / f"breakdown_{category}.txt"))
        logger.info("breakdown_model.saved", path=str(dir_path), categories=len(self._boosters))

    @classmethod
    def load(cls, dir_path: Path, *, categories: Sequence[str] = CATEGORIES) -> BreakdownModel:
        """Load every category's previously saved booster from ``dir_path``."""
        boosters = {
            category: lgb.Booster(model_file=str(dir_path / f"breakdown_{category}.txt"))
            for category in categories
        }
        return cls(boosters)
