"""Export forecast and exchange data as JSON."""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oko.fetchers.entsoe import ExchangeRecord
from oko.forecast.model import Prediction
from oko.isoformat import format_iso_z

ATTRIBUTION: tuple[str, ...] = (
    "ENTSO-E Transparency Platform (CC-BY 4.0)",
    "NOAA GFS",
    "Emission factors adapted from electricitymaps-contrib (AGPLv3)",
)


@dataclass(frozen=True, slots=True)
class CurrentBreakdown:
    """Current power mix breakdown and emissions."""

    timestamp: dt.datetime
    power_breakdown_percent: dict[str, float]
    renewable_percent: float
    fossil_free_percent: float
    emissions_breakdown_percent: dict[str, float] = field(default_factory=dict)


def _export_zone_name(zone: str) -> str:
    return "DE" if zone == "DE-LU" else zone


def build_payload(
    predictions: list[Prediction],
    *,
    zone: str,
    generated_at: dt.datetime,
    model_version: str,
    source_repo_url: str,
    training_rows: int,
    current: CurrentBreakdown | None = None,
) -> dict[str, Any]:
    """Build forecast JSON payload."""
    return {
        "zone": _export_zone_name(zone),
        "generated_at": format_iso_z(generated_at),
        "model_version": model_version,
        "unit": "gCO2eq/kWh",
        "training_rows": training_rows,
        "current": (
            {
                "timestamp": format_iso_z(current.timestamp),
                "power_breakdown_percent": current.power_breakdown_percent,
                "renewable_percent": current.renewable_percent,
                "fossil_free_percent": current.fossil_free_percent,
                "emissions_breakdown_percent": current.emissions_breakdown_percent,
            }
            if current is not None
            else None
        ),
        "forecast": [
            {
                "timestamp": format_iso_z(prediction.timestamp),
                "value": round(prediction.value_g_per_kwh),
                "value_lifecycle": (
                    round(prediction.value_lifecycle_g_per_kwh)
                    if prediction.value_lifecycle_g_per_kwh is not None
                    else None
                ),
                "confidence": prediction.confidence,
                "power_breakdown_percent": prediction.power_breakdown_percent,
                "price_eur_per_mwh": (
                    round(prediction.price_eur_per_mwh, 2)
                    if prediction.price_eur_per_mwh is not None
                    else None
                ),
            }
            for prediction in predictions
        ],
        "attribution": list(ATTRIBUTION),
        "source": source_repo_url,
    }


def build_exchanges_payload(
    records: Sequence[ExchangeRecord],
    *,
    generated_at: dt.datetime,
    source_repo_url: str,
) -> dict[str, Any]:
    """Build cross-border exchange JSON payload."""
    latest_by_border: dict[tuple[str, str], ExchangeRecord] = {}
    for record in records:
        border = (record.zone_from, record.zone_to)
        current = latest_by_border.get(border)
        if current is None or record.timestamp > current.timestamp:
            latest_by_border[border] = record
    return {
        "generated_at": format_iso_z(generated_at),
        "exchanges": [
            {
                "zone_from": record.zone_from,
                "zone_to": record.zone_to,
                "timestamp": format_iso_z(record.timestamp),
                "net_flow_mw": round(record.net_flow_mw),
            }
            for record in sorted(latest_by_border.values(), key=lambda r: (r.zone_from, r.zone_to))
        ],
        "source": source_repo_url,
    }


def write_json(payload: dict[str, Any], path: Path) -> None:
    """Write JSON payload atomically to file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        os.fchmod(fd, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, indent=2) + "\n")
        os.replace(tmp_name, path)
    except BaseException:
        os.unlink(tmp_name)
        raise
