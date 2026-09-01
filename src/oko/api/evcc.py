"""Reshape OKO's forecast export into evcc's custom-tariff rate format.

evcc's ``forecast`` tariff plugin expects a bare JSON array of
``{start, end, value}`` objects (the Go type ``api.Rate`` upstream),
sorted by ``start``, with ``value`` in the same unit OKO already exports
(gCO2eq/kWh) — see README's "evcc custom tariff integration" section for
the wiring.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from oko.isoformat import format_iso_z, parse_iso_z

#: OKO's export is hourly — each forecast point covers exactly one hour.
SLOT_DURATION = dt.timedelta(hours=1)


def to_evcc_rates(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Reshape a forecast export payload into evcc's ``[{start, end, value}]`` shape.

    Args:
        payload: a parsed export payload matching ``oko.export.build_payload``'s
            schema (a ``forecast`` list of ``{timestamp, value, confidence}``).

    Returns:
        One rate per forecast point, in the same order as the source (already
        sorted by timestamp), with ``end`` set to ``start`` plus one hour.
    """
    rates = []
    for point in payload["forecast"]:
        start = parse_iso_z(point["timestamp"])
        end = start + SLOT_DURATION
        rates.append(
            {
                "start": format_iso_z(start),
                "end": format_iso_z(end),
                "value": point["value"],
            }
        )
    return rates
