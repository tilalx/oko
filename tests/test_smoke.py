"""Smoke tests that just confirm the package imports and settings load.

Fetcher/emissions/forecast test suites land in their respective phases.
"""

import oko
from oko.config import ALL_ZONES, NEIGHBOR_ZONES, TARGET_ZONE, Settings


def test_package_has_version() -> None:
    assert oko.__version__


def test_settings_load_without_token(monkeypatch: object) -> None:
    settings = Settings(entsoe_token="", _env_file=None)  # type: ignore[call-arg]
    assert settings.entsoe_token == ""
    assert settings.model_version


def test_zone_config_is_consistent() -> None:
    assert TARGET_ZONE == "DE-LU"
    assert TARGET_ZONE not in NEIGHBOR_ZONES
    assert set(ALL_ZONES) == {TARGET_ZONE, *NEIGHBOR_ZONES}
    assert len(ALL_ZONES) == len(set(ALL_ZONES))
