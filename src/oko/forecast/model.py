"""Carbon-intensity forecast model: LightGBM gradient boosted regressor."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import lightgbm as lgb
import numpy as np
import structlog

from oko.emissions.factors import CATEGORIES
from oko.forecast.features import FEATURE_COLUMNS, FeatureRow

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


def confidence_for_horizon(horizon_hours: int) -> Confidence:
    """Confidence level based on forecast horizon."""
    if horizon_hours <= CONFIDENCE_HIGH_MAX_HOURS:
        return "high"
    if horizon_hours <= CONFIDENCE_MEDIUM_MAX_HOURS:
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


def _to_matrix(rows: list[FeatureRow]) -> np.ndarray:
    return np.array([[row.as_dict()[col] for col in FEATURE_COLUMNS] for row in rows], dtype=float)


class CarbonIntensityModel:
    """LightGBM model for carbon intensity forecasting."""

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
        num_boost_round: int = DEFAULT_NUM_BOOST_ROUND,
    ) -> CarbonIntensityModel:
        """Train a new model on historical feature rows and their observed intensity.

        Args:
            rows: training feature rows (``horizon_hours`` should be 0 for
                all of them — see ``build_training_features``).
            targets: observed carbon intensity, g CO2eq/kWh, one per row,
                same order as ``rows``.
            params: LightGBM parameters; defaults to ``DEFAULT_LGB_PARAMS``.
            num_boost_round: boosting rounds.

        Returns:
            A fitted ``CarbonIntensityModel``.

        Raises:
            ValueError: if ``rows`` and ``targets`` don't line up, or
                there's nothing to train on.
        """
        if len(rows) != len(targets):
            raise ValueError(f"rows ({len(rows)}) and targets ({len(targets)}) length mismatch")
        if not rows:
            raise ValueError("Cannot train on an empty dataset")

        dataset = lgb.Dataset(_to_matrix(rows), label=np.array(targets, dtype=float))
        booster = lgb.train(params or DEFAULT_LGB_PARAMS, dataset, num_boost_round=num_boost_round)
        logger.info("model.trained", rows=len(rows), num_boost_round=num_boost_round)
        return cls(booster)

    def predict(self, rows: list[FeatureRow]) -> list[Prediction]:
        """Predict carbon intensity for a set of feature rows.

        Args:
            rows: feature rows to predict for (typically from
                ``build_forecast_features``).

        Returns:
            One ``Prediction`` per input row, same order, negative
            predictions clamped to 0 (intensity can't be negative).
        """
        if not rows:
            return []
        raw = self._booster.predict(_to_matrix(rows))
        return [
            Prediction(
                timestamp=row.timestamp,
                value_g_per_kwh=max(float(value), 0.0),
                confidence=confidence_for_horizon(row.horizon_hours),
            )
            for row, value in zip(rows, raw, strict=True)
        ]

    def save(self, path: Path) -> None:
        """Save the trained booster to ``path`` (LightGBM's native text format)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._booster.save_model(str(path))
        logger.info("model.saved", path=str(path))

    @classmethod
    def load(cls, path: Path) -> CarbonIntensityModel:
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
        num_boost_round: int = DEFAULT_NUM_BOOST_ROUND,
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
            num_boost_round: boosting rounds.

        Returns:
            A fitted ``BreakdownModel``.

        Raises:
            ValueError: if ``rows`` and ``breakdowns`` don't line up, or
                there's nothing to train on.
        """
        if len(rows) != len(breakdowns):
            msg = f"rows ({len(rows)}) and breakdowns ({len(breakdowns)}) length mismatch"
            raise ValueError(msg)
        if not rows:
            raise ValueError("Cannot train on an empty dataset")

        matrix = _to_matrix(rows)
        boosters: dict[str, lgb.Booster] = {}
        for category in categories:
            targets = np.array([b.get(category, 0.0) for b in breakdowns], dtype=float)
            dataset = lgb.Dataset(matrix, label=targets)
            boosters[category] = lgb.train(
                params or DEFAULT_LGB_PARAMS, dataset, num_boost_round=num_boost_round
            )
        logger.info("breakdown_model.trained", rows=len(rows), categories=len(boosters))
        return cls(boosters)

    def predict(self, rows: list[FeatureRow]) -> list[BreakdownPrediction]:
        """Predict a generation mix for a set of feature rows.

        Args:
            rows: feature rows to predict for (typically from
                ``build_forecast_features``).

        Returns:
            One ``BreakdownPrediction`` per input row, same order.
            Negative per-category predictions are clipped to 0, then each
            row's category shares are renormalized to sum to 100 -- the
            individual boosters don't otherwise guarantee that.
        """
        if not rows:
            return []
        matrix = _to_matrix(rows)
        raw = {
            category: np.clip(booster.predict(matrix), 0.0, None)
            for category, booster in self._boosters.items()
        }
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
