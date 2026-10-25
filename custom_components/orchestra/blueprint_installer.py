"""
Installs the bundled Orchestra automation blueprints into a Home Assistant config dir.

HACS only ships `custom_components/orchestra`, so the blueprints travel inside it and
are copied to `<config>/blueprints/automation/orchestra/` on setup. The commissioning
agent uses the same function before Home Assistant first boots.
"""

from __future__ import annotations

from pathlib import Path

BLUEPRINT_NAMESPACE = "orchestra"
BUNDLED_BLUEPRINT_DIR = Path(__file__).parent / "blueprints" / "automation"

BLUEPRINT_HVAC_SETPOINT_OFFSET = "orchestra_hvac_setpoint_offset.yaml"
BLUEPRINT_EV_CHARGE_RATE = "orchestra_ev_charge_rate.yaml"
BLUEPRINT_HEAT_PUMP_TARGET = "orchestra_heat_pump_target.yaml"
BLUEPRINT_HOME_BATTERY_RESERVE = "orchestra_home_battery_reserve.yaml"


def blueprint_path(filename: str) -> str:
    """The `use_blueprint.path` an automation uses to reference a bundled blueprint."""
    return f"{BLUEPRINT_NAMESPACE}/{filename}"


def install_blueprints(config_dir: Path | str) -> list[Path]:
    """
    Copy bundled blueprints into the config dir, replacing outdated copies.
    Returns the files written. To customise a blueprint, copy it under another name.
    """
    target_dir = Path(config_dir) / "blueprints" / "automation" / BLUEPRINT_NAMESPACE
    target_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for source in sorted(BUNDLED_BLUEPRINT_DIR.glob("*.yaml")):
        target = target_dir / source.name
        content = source.read_text(encoding="utf-8")
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            target.write_text(content, encoding="utf-8")
            written.append(target)
    return written
