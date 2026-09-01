"""Local development tool: generate a synthetic ``forecast_de.json``.

Writes a file in the exact same binding schema a real pipeline run
produces (reuses ``oko.export.build_payload``/``write_json`` directly, so
nothing downstream — the API, the web UI — can tell it apart from a real
export other than the ``model_version`` prefix below). Exists purely to
unblock building/testing the API and web UI while ENTSO-E is unreachable;
it is **never** wired into the hourly pipeline and must not be run in
production — see README's "Local development" section.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import random
from pathlib import Path

import structlog

from oko.config import (
    EXCHANGE_BORDERS,
    FLOW_TRACING_ZONES,
    FORECAST_HORIZON_HOURS,
    TARGET_ZONE,
    get_settings,
)
from oko.emissions.calculator import emissions_weighted_breakdown_percentages
from oko.emissions.factors import factors_for_zone
from oko.export import CurrentBreakdown, build_exchanges_payload, build_payload, write_json
from oko.fetchers.entsoe import ExchangeRecord
from oko.forecast.model import Prediction, confidence_for_horizon

logger = structlog.get_logger(__name__)

#: Illustrative shape for a diurnal carbon-intensity curve (lower
#: overnight, higher during the morning/evening demand peaks) — not fitted
#: to real data, just plausible enough to exercise a chart/API locally.
#: The same curve is reused for every zone in ``--all-zones`` mode; this
#: is a dev-only fixture, not a claim about any zone's real profile.
BASE_G_PER_KWH = 320.0
DIURNAL_AMPLITUDE_G_PER_KWH = 120.0
NOISE_STDDEV_G_PER_KWH = 15.0
#: Local hour (UTC, treated loosely) the diurnal curve troughs at.
TROUGH_HOUR = 4
#: Illustrative lifecycle/direct ratio -- lifecycle factors are
#: consistently higher than direct ones for a fossil-heavy mix (see
#: ``oko.emissions.factors``), so the mock lifecycle value is direct
#: scaled up, not an independent curve.
LIFECYCLE_RATIO = 1.25

#: Illustrative production mix for the mock ``current`` block -- percentages
#: sum to 100, not fitted to any real zone.
MOCK_POWER_BREAKDOWN_PERCENT: dict[str, float] = {
    "coal": 18.0,
    "gas": 12.0,
    "nuclear": 5.0,
    "wind": 32.0,
    "solar": 15.0,
    "hydro": 8.0,
    "biomass": 7.0,
    "unknown": 3.0,
}
MOCK_RENEWABLE_PERCENT = 62.0
MOCK_FOSSIL_FREE_PERCENT = 67.0


def generate_predictions(
    *, reference_time: dt.datetime, hours: int, seed: int | None = None
) -> list[Prediction]:
    """Generate a synthetic, hourly forecast shaped like a real one.

    Args:
        reference_time: UTC hour the synthetic forecast starts after.
        hours: number of hourly points to generate.
        seed: RNG seed for reproducible output; ``None`` for non-deterministic runs.

    Returns:
        ``hours`` synthetic ``Prediction`` objects, one per hour ahead of
        ``reference_time``, with confidence assigned the same way a real
        forecast's would be (see ``confidence_for_horizon``), and a
        synthetic ``value_lifecycle_g_per_kwh`` alongside direct plus a
        synthetic ``power_breakdown_percent`` (see
        ``MOCK_POWER_BREAKDOWN_PERCENT``, lightly perturbed and
        renormalized to 100 per hour so it isn't a flat repeat).
    """
    rng = random.Random(seed)
    predictions = []
    for horizon_hours in range(1, hours + 1):
        timestamp = reference_time + dt.timedelta(hours=horizon_hours)
        diurnal = -math.cos((timestamp.hour - TROUGH_HOUR) / 24 * 2 * math.pi)
        value = BASE_G_PER_KWH + diurnal * DIURNAL_AMPLITUDE_G_PER_KWH
        value += rng.gauss(0, NOISE_STDDEV_G_PER_KWH)
        value = max(value, 0.0)
        breakdown = {
            category: max(pct + rng.gauss(0, pct * 0.15), 0.0)
            for category, pct in MOCK_POWER_BREAKDOWN_PERCENT.items()
        }
        breakdown_total = sum(breakdown.values()) or 1.0
        breakdown = {
            category: 100.0 * pct / breakdown_total for category, pct in breakdown.items()
        }
        predictions.append(
            Prediction(
                timestamp=timestamp,
                value_g_per_kwh=value,
                confidence=confidence_for_horizon(horizon_hours),
                value_lifecycle_g_per_kwh=value * LIFECYCLE_RATIO,
                power_breakdown_percent=breakdown,
            )
        )
    return predictions


def generate_payload(
    zone: str, *, reference_time: dt.datetime, hours: int, seed: int | None, model_version: str
) -> dict[str, object]:
    """Build one zone's full mock export payload (predictions + current block)."""
    predictions = generate_predictions(reference_time=reference_time, hours=hours, seed=seed)
    current = CurrentBreakdown(
        timestamp=reference_time,
        power_breakdown_percent=dict(MOCK_POWER_BREAKDOWN_PERCENT),
        renewable_percent=MOCK_RENEWABLE_PERCENT,
        fossil_free_percent=MOCK_FOSSIL_FREE_PERCENT,
        emissions_breakdown_percent=emissions_weighted_breakdown_percentages(
            MOCK_POWER_BREAKDOWN_PERCENT, factors_for_zone(zone)
        ),
    )
    return build_payload(
        predictions,
        zone=zone,
        generated_at=reference_time,
        model_version=model_version,
        source_repo_url=get_settings().source_repo_url,
        training_rows=0,
        current=current,
    )


#: Illustrative flow magnitude range for the mock exchanges snapshot -- not
#: fitted to any real border's typical throughput, just plausible enough to
#: exercise the map's flow-arrow rendering locally.
MOCK_FLOW_RANGE_MW = (-2000.0, 2000.0)


def generate_exchanges_payload(
    *, reference_time: dt.datetime, seed: int | None
) -> dict[str, object]:
    """Build a mock ``exchanges.json`` snapshot covering every configured border.

    Mirrors ``oko.export.build_exchanges_payload``'s real shape exactly, so
    the web UI's flow-line rendering can be exercised offline the same way
    ``generate_payload`` already does for forecasts -- see module
    docstring.
    """
    rng = random.Random(seed)
    records = [
        ExchangeRecord(
            zone_from=zone_from,
            zone_to=zone_to,
            timestamp=reference_time,
            net_flow_mw=rng.uniform(*MOCK_FLOW_RANGE_MW),
        )
        for zone_from, zone_to in EXCHANGE_BORDERS
    ]
    return build_exchanges_payload(
        records, generated_at=reference_time, source_repo_url=get_settings().source_repo_url
    )


def main() -> None:
    """CLI entry point.

    Single zone: ``python -m oko.mockdata --out output/forecast_de.json``.
    Every published zone: ``python -m oko.mockdata --all-zones --out-dir output``
    (writes ``forecast_de.json`` for DE-LU, ``forecast_{ZONE}.json`` for
    every other zone in ``oko.config.FLOW_TRACING_ZONES`` -- the same
    naming convention ``oko.pipeline`` uses).
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", help="Path to write a single zone's mock forecast JSON to.")
    parser.add_argument(
        "--out-dir", help="Directory to write every published zone's mock forecast into."
    )
    parser.add_argument(
        "--zone",
        default=TARGET_ZONE,
        help=f"Zone to generate for with --out (default {TARGET_ZONE}); ignored with --all-zones.",
    )
    parser.add_argument(
        "--all-zones",
        action="store_true",
        help="Generate every zone in FLOW_TRACING_ZONES (requires --out-dir).",
    )
    parser.add_argument(
        "--hours", type=int, default=FORECAST_HORIZON_HOURS, help="Hourly points to generate."
    )
    parser.add_argument("--seed", type=int, default=None, help="RNG seed for reproducible output.")
    args = parser.parse_args()

    if args.all_zones and not args.out_dir:
        parser.error("--all-zones requires --out-dir")
    if not args.all_zones and not args.out:
        parser.error("either --out or --all-zones/--out-dir is required")

    settings = get_settings()
    now = dt.datetime.now(dt.UTC).replace(minute=0, second=0, microsecond=0)
    model_version = f"mock-{settings.model_version}"

    zones = FLOW_TRACING_ZONES if args.all_zones else (args.zone,)
    for zone in zones:
        payload = generate_payload(
            zone, reference_time=now, hours=args.hours, seed=args.seed, model_version=model_version
        )
        if args.all_zones:
            filename = "forecast_de.json" if zone == TARGET_ZONE else f"forecast_{zone}.json"
            out_path = Path(args.out_dir) / filename
        else:
            out_path = Path(args.out)
        write_json(payload, out_path)
        forecast = payload["forecast"]
        assert isinstance(forecast, list)
        logger.info("mockdata.written", zone=zone, path=str(out_path), hours=len(forecast))

    if args.all_zones:
        exchanges_payload = generate_exchanges_payload(reference_time=now, seed=args.seed)
        exchanges_path = Path(args.out_dir) / "exchanges.json"
        write_json(exchanges_payload, exchanges_path)
        exchanges = exchanges_payload["exchanges"]
        assert isinstance(exchanges, list)
        logger.info(
            "mockdata.written", zone="exchanges", path=str(exchanges_path), borders=len(exchanges)
        )


if __name__ == "__main__":
    main()
