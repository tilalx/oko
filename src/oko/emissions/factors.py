"""Emission factors (g CO2eq/kWh) per category: direct and lifecycle, global + zone overrides."""

from __future__ import annotations

from typing import Literal

GLOBAL_DIRECT_FACTORS_G_PER_KWH: dict[str, float] = {
    "biomass": 0.0,
    "coal": 760.0,
    "gas": 370.0,
    "geothermal": 0.0,
    "hydro": 0.0,
    "nuclear": 0.0,
    "oil": 406.0,
    "solar": 0.0,
    "wind": 0.0,
    "unknown": 575.0,
}

CATEGORIES: tuple[str, ...] = tuple(GLOBAL_DIRECT_FACTORS_G_PER_KWH.keys())

DE_LU_DIRECT_FACTOR_OVERRIDES_G_PER_KWH: dict[str, float] = {
    "coal": 1052.69,
    "gas": 382.97,
    "oil": 826.11,
    "biomass": 0.0,
}

_OTHER_ZONE_DIRECT_FACTOR_OVERRIDES_G_PER_KWH: dict[str, dict[str, float]] = {
    "FR": {"coal": 968.5, "gas": 378.34, "oil": 721.9, "biomass": 0.0},
    "CH": {"biomass": 0.0},
    "AT": {"coal": 1075.5, "gas": 371.3, "oil": 826.11, "biomass": 0.0},
    "CZ": {"coal": 998.3, "gas": 368.47, "oil": 925.95, "biomass": 0.0},
    "PL": {"coal": 1038.61, "gas": 366.09, "oil": 826.11, "biomass": 0.0},
    "DK-DK1": {"coal": 1035.2, "gas": 389.72, "oil": 826.11, "biomass": 0.0},
    "DK-DK2": {"coal": 1035.2, "gas": 389.72, "oil": 826.11, "biomass": 0.0},
    "NL": {"coal": 715.99, "gas": 361.85, "oil": 925.95, "biomass": 0.0},
    "BE": {"coal": 1035.2, "gas": 380.59, "oil": 826.11, "biomass": 0.0},
    "ES": {"coal": 1052.03, "gas": 417.27, "oil": 826.11, "biomass": 0.0},
    "IT-NO": {"coal": 1046.14, "gas": 390.35, "oil": 950.79, "biomass": 0.0},
    "HU": {"coal": 1283.28, "gas": 401.95, "oil": 826.11, "biomass": 0.0},
    "SI": {"coal": 1022.92, "gas": 389.72, "oil": 826.11, "biomass": 0.0},
    "SK": {"coal": 1035.2, "gas": 359.05, "oil": 826.11, "biomass": 0.0},
}

ZONE_FACTOR_OVERRIDES: dict[str, dict[str, float]] = {
    "DE-LU": DE_LU_DIRECT_FACTOR_OVERRIDES_G_PER_KWH,
    **_OTHER_ZONE_DIRECT_FACTOR_OVERRIDES_G_PER_KWH,
}

GLOBAL_LIFECYCLE_FACTORS_G_PER_KWH: dict[str, float] = {
    "biomass": 230.0,
    "coal": 820.0,
    "gas": 490.0,
    "geothermal": 38.0,
    "hydro": 24.0,
    "nuclear": 12.0,
    "oil": 650.0,
    "solar": 45.0,
    "wind": 11.0,
    "unknown": 700.0,
}

DE_LU_LIFECYCLE_FACTOR_OVERRIDES_G_PER_KWH: dict[str, float] = {
    "biomass": 230.0,
    "coal": 1112.69,
    "gas": 502.97,
    "hydro": 10.7,
    "nuclear": 5.13,
    "oil": 1070.11,
    "solar": 35.12,
    "wind": 12.62,
}

_OTHER_ZONE_LIFECYCLE_FACTOR_OVERRIDES_G_PER_KWH: dict[str, dict[str, float]] = {
    "FR": {
        "coal": 1028.5, "gas": 498.34, "oil": 965.9, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 30.075, "wind": 12.62,
    },
    "CH": {"coal": 820.0, "gas": 490.0, "oil": 650.0, "biomass": 230.0, "solar": 29.5},
    "AT": {
        "coal": 1187.127343, "gas": 491.3, "oil": 1070.11, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 31.0, "wind": 12.62,
    },
    "CZ": {
        "coal": 1058.3, "gas": 488.47, "oil": 1169.95, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 34.33333333, "wind": 12.62,
    },
    "PL": {
        "coal": 1098.61, "gas": 486.09, "oil": 1070.11, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 34.84, "wind": 12.62,
    },
    "DK-DK1": {
        "coal": 1095.2, "gas": 509.72, "oil": 1070.11, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 37.66666667, "wind": 12.62,
    },
    "DK-DK2": {
        "coal": 1095.2, "gas": 509.72, "oil": 1070.11, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 36.0, "wind": 12.62,
    },
    "NL": {
        "coal": 775.99, "gas": 481.85, "oil": 1169.95, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 36.5, "wind": 12.62,
    },
    "BE": {
        "coal": 1095.2, "gas": 500.59, "oil": 1070.11, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 36.0, "wind": 12.62,
    },
    "ES": {
        "coal": 1112.03, "gas": 537.27, "oil": 1070.11, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 26.46666667, "wind": 12.62,
    },
    "IT-NO": {
        "coal": 1106.14, "gas": 510.35, "oil": 1194.79, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 27.3, "wind": 12.62,
    },
    "HU": {
        "coal": 1343.28, "gas": 521.95, "oil": 1070.11, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 30.66666667, "wind": 12.62,
    },
    "SI": {
        "coal": 1082.92, "gas": 509.72, "oil": 1070.11, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 30.83333333, "wind": 12.62,
    },
    "SK": {
        "coal": 1095.2, "gas": 479.05, "oil": 1070.11, "biomass": 230.0,
        "hydro": 10.7, "nuclear": 5.13, "solar": 33.0, "wind": 12.62,
    },
}

ZONE_LIFECYCLE_FACTOR_OVERRIDES: dict[str, dict[str, float]] = {
    "DE-LU": DE_LU_LIFECYCLE_FACTOR_OVERRIDES_G_PER_KWH,
    **_OTHER_ZONE_LIFECYCLE_FACTOR_OVERRIDES_G_PER_KWH,
}

FactorKind = Literal["direct", "lifecycle"]


def factors_for_zone(zone: str, *, kind: FactorKind = "direct") -> dict[str, float]:
    if kind == "direct":
        return {**GLOBAL_DIRECT_FACTORS_G_PER_KWH, **ZONE_FACTOR_OVERRIDES.get(zone, {})}
    return {
        **GLOBAL_LIFECYCLE_FACTORS_G_PER_KWH,
        **ZONE_LIFECYCLE_FACTOR_OVERRIDES.get(zone, {}),
    }
