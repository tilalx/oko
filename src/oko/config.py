"""Typed application configuration from environment variables."""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

TARGET_ZONE = "DE-LU"

NEIGHBOR_ZONES: tuple[str, ...] = (
    "FR",
    "CH",
    "AT",
    "CZ",
    "PL",
    "DK-DK1",
    "DK-DK2",
    "NL",
    "BE",
)

SECOND_HOP_ZONES: tuple[str, ...] = (
    "ES",
    "IT-NO",
    "HU",
    "SI",
    "SK",
)

EXPANDED_ZONES: tuple[str, ...] = (
    # Nordics
    "FI",
    "NO-NO1",
    "NO-NO2",
    "NO-NO3",
    "NO-NO4",
    "NO-NO5",
    "SE-SE1",
    "SE-SE2",
    "SE-SE3",
    "SE-SE4",
    # Baltics
    "EE",
    "LV",
    "LT",
    # British Isles
    "GB",
    "GB-NIR",
    "IE",
    # Iberia
    "PT",
    # Balkans
    "BG",
    "RO",
    "GR",
    "HR",
    "RS",
    "BA",
    "ME",
    "MK",
    "AL",
    "XK",
    # Italy (real border-connected sub-zones beyond IT-NO)
    "IT-CNO",
    "IT-CSO",
    "IT-SO",
    "IT-SAR",
    "IT-SIC",
    # Malta
    "MT",
)

FLOW_TRACING_ZONES: tuple[str, ...] = (
    TARGET_ZONE,
    *NEIGHBOR_ZONES,
    *SECOND_HOP_ZONES,
    *EXPANDED_ZONES,
)

ALL_ZONES: tuple[str, ...] = (TARGET_ZONE, *NEIGHBOR_ZONES)

EXCHANGE_BORDERS: tuple[tuple[str, str], ...] = (
    ("AT", "CH"),
    ("AT", "CZ"),
    ("AT", "DE-LU"),
    ("AT", "HU"),
    ("AT", "IT-NO"),
    ("AT", "SI"),
    ("BE", "DE-LU"),
    ("BE", "FR"),
    ("BE", "NL"),
    ("CH", "DE-LU"),
    ("CH", "FR"),
    ("CH", "IT-NO"),
    ("CZ", "DE-LU"),
    ("CZ", "PL"),
    ("CZ", "SK"),
    ("DE-LU", "DK-DK1"),
    ("DE-LU", "DK-DK2"),
    ("DE-LU", "FR"),
    ("DE-LU", "NL"),
    ("DE-LU", "PL"),
    ("DK-DK1", "DK-DK2"),
    ("DK-DK1", "NL"),
    ("ES", "FR"),
    ("FR", "IT-NO"),
    ("HU", "SI"),
    ("HU", "SK"),
    ("IT-NO", "SI"),
    ("PL", "SK"),
    ("AL", "GR"),
    ("AL", "ME"),
    ("AL", "RS"),
    ("AL", "XK"),
    ("BA", "HR"),
    ("BA", "ME"),
    ("BA", "RS"),
    ("BE", "GB"),
    ("BG", "GR"),
    ("BG", "MK"),
    ("BG", "RO"),
    ("BG", "RS"),
    ("DE-LU", "NO-NO2"),
    ("DE-LU", "SE-SE4"),
    ("DK-DK1", "GB"),
    ("DK-DK1", "NO-NO2"),
    ("DK-DK1", "SE-SE3"),
    ("DK-DK2", "SE-SE4"),
    ("EE", "FI"),
    ("EE", "LV"),
    ("ES", "PT"),
    ("FI", "NO-NO4"),
    ("FI", "SE-SE1"),
    ("FI", "SE-SE3"),
    ("FR", "GB"),
    ("GB", "GB-NIR"),
    ("GB", "IE"),
    ("GB", "NL"),
    ("GB", "NO-NO2"),
    ("GB-NIR", "IE"),
    ("GR", "IT-SO"),
    ("GR", "MK"),
    ("HR", "HU"),
    ("HR", "RS"),
    ("HR", "SI"),
    ("HU", "RO"),
    ("HU", "RS"),
    ("IT-CNO", "IT-CSO"),
    ("IT-CNO", "IT-NO"),
    ("IT-CSO", "IT-SAR"),
    ("IT-CSO", "IT-SO"),
    ("IT-CSO", "ME"),
    ("IT-SIC", "IT-SO"),
    ("IT-SIC", "MT"),
    ("LT", "LV"),
    ("LT", "PL"),
    ("LT", "SE-SE4"),
    ("ME", "RS"),
    ("ME", "XK"),
    ("MK", "RS"),
    ("MK", "XK"),
    ("NL", "NO-NO2"),
    ("NO-NO1", "NO-NO2"),
    ("NO-NO1", "NO-NO3"),
    ("NO-NO1", "NO-NO5"),
    ("NO-NO1", "SE-SE3"),
    ("NO-NO2", "NO-NO5"),
    ("NO-NO3", "NO-NO4"),
    ("NO-NO3", "NO-NO5"),
    ("NO-NO3", "SE-SE2"),
    ("NO-NO4", "SE-SE1"),
    ("NO-NO4", "SE-SE2"),
    ("PL", "SE-SE4"),
    ("RO", "RS"),
    ("RS", "XK"),
    ("SE-SE1", "SE-SE2"),
    ("SE-SE2", "SE-SE3"),
    ("SE-SE3", "SE-SE4"),
)

ENTSOE_DOMAIN_MAPPINGS: dict[str, str] = {
    "DE-LU": "10Y1001A1001A82H",
    "FR": "10YFR-RTE------C",
    "CH": "10YCH-SWISSGRIDZ",
    "AT": "10YAT-APG------L",
    "CZ": "10YCZ-CEPS-----N",
    "PL": "10YPL-AREA-----S",
    "DK-DK1": "10YDK-1--------W",
    "DK-DK2": "10YDK-2--------M",
    "NL": "10YNL----------L",
    "BE": "10YBE----------2",
    "ES": "10YES-REE------0",
    "IT-NO": "10Y1001A1001A73I",
    "HU": "10YHU-MAVIR----U",
    "SI": "10YSI-ELES-----O",
    "SK": "10YSK-SEPS-----K",
    "FI": "10YFI-1--------U",
    "NO-NO1": "10YNO-1--------2",
    "NO-NO2": "10YNO-2--------T",
    "NO-NO3": "10YNO-3--------J",
    "NO-NO4": "10YNO-4--------9",
    "NO-NO5": "10Y1001A1001A48H",
    "SE-SE1": "10Y1001A1001A44P",
    "SE-SE2": "10Y1001A1001A45N",
    "SE-SE3": "10Y1001A1001A46L",
    "SE-SE4": "10Y1001A1001A47J",
    "EE": "10Y1001A1001A39I",
    "LV": "10YLV-1001A00074",
    "LT": "10YLT-1001A0008Q",
    "GB": "10YGB----------A",
    "GB-NIR": "10Y1001A1001A016",
    "IE": "10YIE-1001A00010",
    "PT": "10YPT-REN------W",
    "BG": "10YCA-BULGARIA-R",
    "RO": "10YRO-TEL------P",
    "GR": "10YGR-HTSO-----Y",
    "HR": "10YHR-HEP------M",
    "RS": "10YCS-SERBIATSOV",
    "BA": "10YBA-JPCC-----D",
    "ME": "10YCS-CG-TSO---S",
    "MK": "10YMK-MEPSO----8",
    "AL": "10YAL-KESH-----5",
    "XK": "10Y1001C--00100H",
    "IT-CNO": "10Y1001A1001A70O",
    "IT-CSO": "10Y1001A1001A71M",
    "IT-SO": "10Y1001A1001A788",
    "IT-SAR": "10Y1001A1001A74G",
    "IT-SIC": "10Y1001A1001A75E",
    "MT": "10Y1001A1001A93C",
}

FORECAST_HORIZON_HOURS = 120

ZONE_BBOXES: dict[str, dict[str, float]] = {
    "DE-LU": {"leftlon": 5.0, "rightlon": 16.0, "toplat": 56.0, "bottomlat": 47.0},
    "FR": {"leftlon": -5.0, "rightlon": 8.5, "toplat": 51.5, "bottomlat": 42.0},
    "CH": {"leftlon": 5.9, "rightlon": 10.6, "toplat": 47.9, "bottomlat": 45.7},
    "AT": {"leftlon": 9.5, "rightlon": 17.2, "toplat": 49.1, "bottomlat": 46.3},
    "CZ": {"leftlon": 12.0, "rightlon": 19.0, "toplat": 51.1, "bottomlat": 48.5},
    "PL": {"leftlon": 14.0, "rightlon": 24.2, "toplat": 55.0, "bottomlat": 49.0},
    "DK-DK1": {"leftlon": 8.0, "rightlon": 11.0, "toplat": 57.8, "bottomlat": 54.5},
    "DK-DK2": {"leftlon": 11.0, "rightlon": 12.8, "toplat": 56.2, "bottomlat": 54.5},
    "NL": {"leftlon": 3.3, "rightlon": 7.3, "toplat": 53.6, "bottomlat": 50.7},
    "BE": {"leftlon": 2.5, "rightlon": 6.4, "toplat": 51.6, "bottomlat": 49.5},
    "ES": {"leftlon": -9.5, "rightlon": 3.5, "toplat": 43.8, "bottomlat": 36.0},
    "IT-NO": {"leftlon": 6.6, "rightlon": 13.9, "toplat": 47.1, "bottomlat": 44.0},
    "HU": {"leftlon": 16.1, "rightlon": 22.9, "toplat": 48.6, "bottomlat": 45.7},
    "SI": {"leftlon": 13.4, "rightlon": 16.6, "toplat": 46.9, "bottomlat": 45.4},
    "SK": {"leftlon": 16.8, "rightlon": 22.6, "toplat": 49.6, "bottomlat": 47.7},
    "FI": {"leftlon": 20.5, "rightlon": 31.6, "toplat": 70.1, "bottomlat": 59.8},
    "NO-NO1": {"leftlon": 8.1, "rightlon": 12.9, "toplat": 62.6, "bottomlat": 58.9},
    "NO-NO2": {"leftlon": 5.2, "rightlon": 10.5, "toplat": 60.2, "bottomlat": 58.0},
    "NO-NO3": {"leftlon": 4.9, "rightlon": 12.9, "toplat": 65.0, "bottomlat": 61.0},
    "NO-NO4": {"leftlon": 11.6, "rightlon": 31.1, "toplat": 71.2, "bottomlat": 64.0},
    "NO-NO5": {"leftlon": 4.9, "rightlon": 9.6, "toplat": 61.8, "bottomlat": 60.1},
    "SE-SE1": {"leftlon": 15.4, "rightlon": 24.2, "toplat": 69.1, "bottomlat": 64.2},
    "SE-SE2": {"leftlon": 12.0, "rightlon": 21.0, "toplat": 66.3, "bottomlat": 60.8},
    "SE-SE3": {"leftlon": 11.1, "rightlon": 19.4, "toplat": 62.3, "bottomlat": 56.9},
    "SE-SE4": {"leftlon": 12.4, "rightlon": 17.2, "toplat": 57.5, "bottomlat": 55.3},
    "EE": {"leftlon": 21.9, "rightlon": 28.2, "toplat": 59.7, "bottomlat": 57.5},
    "LV": {"leftlon": 21.0, "rightlon": 28.2, "toplat": 58.1, "bottomlat": 55.7},
    "LT": {"leftlon": 21.1, "rightlon": 26.8, "toplat": 56.4, "bottomlat": 53.9},
    "GB": {"leftlon": -7.5, "rightlon": 1.8, "toplat": 59.1, "bottomlat": 50.0},
    "GB-NIR": {"leftlon": -8.2, "rightlon": -5.4, "toplat": 55.2, "bottomlat": 54.0},
    "IE": {"leftlon": -10.5, "rightlon": -6.0, "toplat": 55.4, "bottomlat": 51.4},
    "PT": {"leftlon": -9.5, "rightlon": -6.2, "toplat": 42.2, "bottomlat": 37.0},
    "BG": {"leftlon": 22.3, "rightlon": 28.6, "toplat": 44.2, "bottomlat": 41.2},
    "RO": {"leftlon": 20.2, "rightlon": 29.7, "toplat": 48.3, "bottomlat": 43.7},
    "GR": {"leftlon": 19.6, "rightlon": 28.2, "toplat": 41.7, "bottomlat": 34.9},
    "HR": {"leftlon": 13.5, "rightlon": 19.4, "toplat": 46.5, "bottomlat": 42.4},
    "RS": {"leftlon": 18.8, "rightlon": 23.0, "toplat": 46.2, "bottomlat": 42.2},
    "BA": {"leftlon": 15.7, "rightlon": 19.6, "toplat": 45.3, "bottomlat": 42.6},
    "ME": {"leftlon": 18.4, "rightlon": 20.3, "toplat": 43.5, "bottomlat": 41.9},
    "MK": {"leftlon": 20.4, "rightlon": 23.0, "toplat": 42.4, "bottomlat": 40.8},
    "AL": {"leftlon": 19.3, "rightlon": 21.0, "toplat": 42.7, "bottomlat": 39.6},
    "XK": {"leftlon": 20.0, "rightlon": 21.8, "toplat": 43.3, "bottomlat": 41.9},
    "IT-CNO": {"leftlon": 9.7, "rightlon": 13.9, "toplat": 44.5, "bottomlat": 42.4},
    "IT-CSO": {"leftlon": 11.4, "rightlon": 15.8, "toplat": 42.9, "bottomlat": 40.0},
    "IT-SO": {"leftlon": 13.9, "rightlon": 18.5, "toplat": 42.1, "bottomlat": 37.9},
    "IT-SAR": {"leftlon": 8.1, "rightlon": 9.8, "toplat": 41.3, "bottomlat": 38.9},
    "IT-SIC": {"leftlon": 12.4, "rightlon": 15.6, "toplat": 38.3, "bottomlat": 36.7},
    "MT": {"leftlon": 14.3, "rightlon": 14.5, "toplat": 36.0, "bottomlat": 35.8},
}


class Settings(BaseSettings):

    model_config = SettingsConfigDict(
        env_prefix="",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    entsoe_token: str = Field(
        default="",
        description="ENTSO-E Transparency Platform API token (server-side only, never "
        "exposed to consumers of the forecast).",
    )
    entsoe_base_url: str = Field(default="https://web-api.tp.entsoe.eu/api")
    noaa_gfs_base_url: str = Field(default="https://noaa-gfs-bdp-pds.s3.amazonaws.com")
    energy_charts_base_url: str = Field(default="https://api.energy-charts.info")

    data_dir: Path = Field(default=Path("data"))
    sqlite_path: Path = Field(default=Path("data/oko.sqlite3"))
    model_dir: Path = Field(default=Path("data/models"))
    export_path: Path = Field(default=Path("/output/forecast_de.json"))

    http_timeout_seconds: float = Field(default=30.0)
    http_max_retries: int = Field(default=3)

    log_level: str = Field(default="INFO")
    model_version: str = Field(default="0.1.0")

    @property
    def source_repo_url(self) -> str:
        return "https://github.com/tilalx/oko"


def get_settings() -> Settings:
    return Settings()
