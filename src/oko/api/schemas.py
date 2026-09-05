"""Pydantic response models for the FastAPI query layer.

Without these, every route returned a bare ``dict[str, Any]`` /
``list[dict[str, Any]]``, so FastAPI's auto-generated ``/openapi.json``
had nothing to describe each response with
(``{"additionalProperties": true}`` on every schema, verified directly
against the running app) — ``/docs`` had nothing useful to show. Attaching
these via ``response_model=`` on each route in ``oko.api.app`` fixes that;
FastAPI derives real ``/openapi.json`` and ``/docs`` content from them
automatically, no other wiring needed.

Field shapes mirror ``oko.export.build_payload`` / ``oko.api.evcc`` /
``oko.history`` exactly -- these are response-validation/documentation
models, not a second source of truth for the payload shape.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ForecastPoint(BaseModel):
    """One hourly forecast point."""

    timestamp: str = Field(description="UTC hour this forecast point is for (ISO 8601, 'Z').")
    value: int = Field(description="Predicted direct-emissions carbon intensity, gCO2eq/kWh.")
    value_lifecycle: int | None = Field(
        default=None,
        description="Predicted lifecycle-emissions carbon intensity, gCO2eq/kWh -- null until "
        "this zone's lifecycle model has bootstrapped.",
    )
    confidence: Literal["high", "medium", "low"] = Field(
        description="Coarse confidence label: high (day 1), medium (days 2-3), low (days 4-5)."
    )
    power_breakdown_percent: dict[str, float] | None = Field(
        default=None,
        description="Predicted generation category -> percent of total production for this "
        "hour -- null until this zone's breakdown model has bootstrapped.",
    )
    price_eur_per_mwh: float | None = Field(
        default=None,
        description="Predicted day-ahead auction price, EUR/MWh -- can be negative; null until "
        "this zone's price model has bootstrapped.",
    )


class CurrentBreakdown(BaseModel):
    """The zone's most recent real (non-forecast) observed hour."""

    timestamp: str = Field(description="UTC hour this observation applies to (ISO 8601, 'Z').")
    power_breakdown_percent: dict[str, float] = Field(
        description="Generation category -> percent of that hour's total production (MW share)."
    )
    renewable_percent: float = Field(description="Renewable share of that hour's production.")
    fossil_free_percent: float = Field(description="Renewable-plus-nuclear share.")
    emissions_breakdown_percent: dict[str, float] = Field(
        default_factory=dict,
        description="Generation category -> percent of that hour's total direct emissions "
        "(gCO2eq share, not MW share) -- zero-factor categories (wind, solar, hydro, nuclear, "
        "biomass, geothermal) drop out even if they dominate power_breakdown_percent.",
    )


class ForecastPayload(BaseModel):
    """The full forecast export for one zone -- see README's 'API schema'."""

    zone: str = Field(description="Public zone code (e.g. 'DE', 'FR', 'DK-DK1').")
    generated_at: str = Field(description="UTC time this forecast was produced (ISO 8601, 'Z').")
    model_version: str
    unit: Literal["gCO2eq/kWh"]
    training_rows: int = Field(
        description="Accumulated training rows behind this zone's direct-intensity model -- a "
        "coarse model-maturity signal."
    )
    current: CurrentBreakdown | None = Field(
        default=None, description="Null if this zone has never had a usable production fetch yet."
    )
    forecast: list[ForecastPoint]
    attribution: list[str]
    source: str = Field(description="Public repository URL.")


class EvccRate(BaseModel):
    """One evcc custom-tariff rate slot -- the ``api.Rate`` shape evcc's forecast plugin expects."""

    start: str = Field(description="UTC slot start (ISO 8601, 'Z').")
    end: str = Field(description="UTC slot end (ISO 8601, 'Z').")
    value: int = Field(description="gCO2eq/kWh.")


class HistoryPoint(BaseModel):
    """One raw historical observation."""

    timestamp: str = Field(description="UTC hour this observation applies to (ISO 8601, 'Z').")
    value: float = Field(description="Observed direct-emissions carbon intensity, gCO2eq/kWh.")
    value_lifecycle: float | None = Field(
        default=None, description="Observed lifecycle-emissions carbon intensity, gCO2eq/kWh."
    )
    method: Literal["flow_trace", "one_hop_fallback"] | None = Field(
        default=None,
        description="Which method produced 'value' -- null for rows persisted before this "
        "column existed.",
    )
    power_breakdown_percent: dict[str, float] | None = Field(
        default=None,
        description="Observed generation category -> percent of total production for this hour "
        "-- null for rows persisted before this column existed, or hours with no usable "
        "production data.",
    )
    price_eur_per_mwh: float | None = Field(
        default=None,
        description="Observed day-ahead auction price, EUR/MWh -- can be negative; null for rows "
        "persisted before this column existed, or zones/hours with no cleared auction.",
    )


class ZoneStatus(BaseModel):
    """One published zone's availability."""

    zone: str = Field(description="Public zone code, as used in /{zone}.json.")
    available: bool = Field(description="Whether this zone currently has a produced forecast.")


class ZonesResponse(BaseModel):
    """Every zone OKO publishes a forecast for."""

    zones: list[ZoneStatus]


class ExchangeEdge(BaseModel):
    """One border's latest known net physical flow."""

    zone_from: str = Field(description="OKO zone key, alphabetically first of the pair.")
    zone_to: str = Field(description="OKO zone key, alphabetically second of the pair.")
    timestamp: str = Field(description="UTC hour this flow applies to (ISO 8601, 'Z').")
    net_flow_mw: int = Field(
        description="Positive means net flow from zone_from to zone_to; negative the reverse."
    )


class BulkZoneData(BaseModel):
    """One zone's forecast + recent history, for the bulk startup endpoint."""

    forecast: ForecastPayload | None = Field(
        default=None, description="Null if this zone has no forecast yet."
    )
    history: list[HistoryPoint] = Field(default_factory=list)


class BulkResponse(BaseModel):
    """Every published zone's forecast + recent history in one response.

    Lets the frontend fetch all zones' startup data in a single round trip
    instead of firing 2 requests per zone -- see GET /api/bulk.
    """

    zones: dict[str, BulkZoneData]


class ExchangesPayload(BaseModel):
    """Latest cross-border flow snapshot across the whole flow-tracing network."""

    generated_at: str = Field(description="UTC time this snapshot was produced (ISO 8601, 'Z').")
    exchanges: list[ExchangeEdge] = Field(
        description="One entry per border with at least one record this run -- see "
        "GET /exchanges.json."
    )
    source: str = Field(description="Public repository URL.")
