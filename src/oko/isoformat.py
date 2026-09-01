"""Shared ISO-8601 'Z' (Zulu) timestamp formatting -- the one wire format OKO uses everywhere.

Every timestamp OKO ever writes or reads across zone boundaries (the JSON
export, evcc's rate format, the history API) uses this exact
``YYYY-MM-DDTHH:MM:SSZ`` shape, hourly resolution, no fractional seconds.
Pulled out once several modules needed the identical pair of functions.
"""

from __future__ import annotations

import datetime as dt


def format_iso_z(timestamp: dt.datetime) -> str:
    """Format a datetime as ``YYYY-MM-DDTHH:MM:SSZ`` (Zulu, no offset), converting to UTC first."""
    return timestamp.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso_z(timestamp: str) -> dt.datetime:
    """Parse a ``YYYY-MM-DDTHH:MM:SSZ`` string into a UTC-aware datetime."""
    return dt.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
