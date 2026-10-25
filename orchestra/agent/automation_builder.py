"""
Conductor Asset Automation Builder (`orchestra/agent/automation_builder.py`).

Fills in the bundled Orchestra blueprints (`custom_components/orchestra/blueprints`)
with the asset entities the DR binder found, so every bound HVAC unit, EV charger,
heat pump and home battery follows its Conductor `granular_assets` directive:

- HVAC: setpoint drifts by `setpoint_offset_c` from a remembered baseline, then restores.
- EV: charger current follows `target_amps` (0 pauses), then returns to normal.
- Heat pump: tank target follows `target_temp_c`, then returns to normal.
- Home battery: backup reserve follows `min_soc_reserve_pct`, then returns to normal.

The directive sensors these read are already tranche (cohort) gated by the integration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from custom_components.orchestra.blueprint_installer import (
    BLUEPRINT_EV_CHARGE_RATE,
    BLUEPRINT_HEAT_PUMP_TARGET,
    BLUEPRINT_HOME_BATTERY_RESERVE,
    BLUEPRINT_HVAC_SETPOINT_OFFSET,
    blueprint_path,
    install_blueprints,
)
from orchestra.agent.dr_binder import ConductorBindingManifest
from orchestra.agent.entity_organizer import OrganizedEntityRecord
from orchestra.hijacker.storage_injector import HAStorageInjector

EV_CURRENT_SUFFIXES: tuple[str, ...] = (
    "_charging_amps_limit",
    "_charging_current",
    "_current_limit",
    "_maximum_current",
)
EV_SWITCH_SUFFIXES: tuple[str, ...] = ("_charging_allowed", "_charge_enable", "_charge_control")
BATTERY_RESERVE_PATTERN = re.compile(r"^number\..*(backup_reserve|reserve_soc|min_soc)")


@dataclass
class AssetAutomationPlan:
    """Automations and helpers generated for the bound Conductor assets."""

    automations: list[dict[str, Any]] = field(default_factory=list)
    helpers: list[dict[str, Any]] = field(default_factory=list)
    blueprints_installed: list[str] = field(default_factory=list)


def _object_id(entity_id: str) -> str:
    return entity_id.split(".", 1)[1]


class ConductorAutomationBuilder:
    """Generates blueprint-backed automations for every Conductor-controllable asset."""

    def __init__(self, storage_injector: HAStorageInjector) -> None:
        self.storage_injector = storage_injector

    def build_asset_automations(
        self,
        dr_manifest: ConductorBindingManifest,
        organized_entities: list[OrganizedEntityRecord],
    ) -> AssetAutomationPlan:
        """
        Install the blueprints, create baseline helpers, and return automations that
        use the blueprints. Committing them to `automations.yaml` is left to Stage 6.
        """
        config_dir = self.storage_injector.config.ha_config_dir
        plan = AssetAutomationPlan(
            blueprints_installed=[p.name for p in install_blueprints(config_dir)]
        )
        known_ids = {rec.entity_id for rec in organized_entities}
        names = {rec.entity_id: rec.sensible_name for rec in organized_entities}
        ev_chargers_seen: set[str] = set()

        for load in dr_manifest.controllable_loads:
            eid = load.control_entity_id
            if load.load_category == "hvac":
                helper_id = f"orchestra_baseline_{_object_id(eid)}"
                plan.helpers.append(
                    {
                        "id": helper_id,
                        "name": f"{load.sensible_name} baseline setpoint",
                        "min": 0.0,
                        "max": 40.0,
                        "step": 0.1,
                        "mode": "box",
                        "unit_of_measurement": "°C",
                        "icon": "mdi:thermometer-check",
                    }
                )
                plan.automations.append(
                    self._automation(
                        f"orchestra_hvac_offset_{_object_id(eid)}",
                        f"Orchestra: {load.sensible_name} follows Conductor setpoint offset",
                        BLUEPRINT_HVAC_SETPOINT_OFFSET,
                        {"climate_entity": eid, "baseline_helper": f"input_number.{helper_id}"},
                    )
                )
            elif load.load_category == "ev_charger":
                # One automation per charger, even if it exposes several current limits
                charger = self._ev_slug(eid)
                if charger in ev_chargers_seen:
                    continue
                ev_chargers_seen.add(charger)
                inputs: dict[str, Any] = {"charger_current": eid}
                switch_id = self._ev_switch_for(eid, known_ids)
                if switch_id:
                    inputs["charger_switch"] = switch_id
                plan.automations.append(
                    self._automation(
                        f"orchestra_ev_rate_{_object_id(eid)}",
                        f"Orchestra: {load.sensible_name} follows Conductor charge rate",
                        BLUEPRINT_EV_CHARGE_RATE,
                        inputs,
                    )
                )
            elif load.load_category == "heat_pump":
                plan.automations.append(
                    self._automation(
                        f"orchestra_heat_pump_{_object_id(eid)}",
                        f"Orchestra: {load.sensible_name} follows Conductor hot water target",
                        BLUEPRINT_HEAT_PUMP_TARGET,
                        {"water_heater": eid},
                    )
                )

        # Batteries aren't controllable loads in the DR manifest; bind their reserve setting
        for rec in organized_entities:
            if not rec.hidden_from_grandpa and BATTERY_RESERVE_PATTERN.match(rec.entity_id):
                plan.automations.append(
                    self._automation(
                        f"orchestra_battery_reserve_{_object_id(rec.entity_id)}",
                        f"Orchestra: {names[rec.entity_id]} follows Conductor battery reserve",
                        BLUEPRINT_HOME_BATTERY_RESERVE,
                        {"reserve_number": rec.entity_id},
                    )
                )

        if plan.helpers:
            self._upsert_input_number_helpers(plan.helpers)
        return plan

    @staticmethod
    def _automation(
        automation_id: str, alias: str, blueprint: str, inputs: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "id": automation_id,
            "alias": alias,
            "description": "Generated by the Orchestra setup agent from the bundled blueprint.",
            "use_blueprint": {"path": blueprint_path(blueprint), "input": inputs},
        }

    @staticmethod
    def _ev_slug(current_entity_id: str) -> str:
        """The charger's shared object id prefix, e.g. `tesla_wall_connector`."""
        object_id = _object_id(current_entity_id)
        for suffix in EV_CURRENT_SUFFIXES:
            if object_id.endswith(suffix):
                return object_id[: -len(suffix)]
        return object_id

    def _ev_switch_for(self, current_entity_id: str, known_ids: set[str]) -> str | None:
        """Find the charge start/stop switch on the same charger as its current limit."""
        slug = self._ev_slug(current_entity_id)
        for switch_suffix in EV_SWITCH_SUFFIXES:
            if f"switch.{slug}{switch_suffix}" in known_ids:
                return f"switch.{slug}{switch_suffix}"
        return None

    def _upsert_input_number_helpers(self, helpers: list[dict[str, Any]]) -> None:
        """Create UI-managed `input_number` helpers, keeping any existing items and values."""
        path = self.storage_injector.storage_dir / "input_number"
        if path.exists():
            doc = json.loads(path.read_text(encoding="utf-8"))
        else:
            doc = {"version": 1, "minor_version": 1, "key": "input_number", "data": {"items": []}}
        items = doc["data"].setdefault("items", [])
        existing = {item.get("id") for item in items}
        items.extend(h for h in helpers if h["id"] not in existing)
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
        self._ensure_input_number_loaded()

    def _ensure_input_number_loaded(self) -> None:
        """Helpers in `.storage/input_number` only load when the integration is set up."""
        yaml_path = self.storage_injector.config.ha_config_dir / "configuration.yaml"
        if not yaml_path.exists():
            return
        text = yaml_path.read_text(encoding="utf-8")
        if re.search(r"^(default_config|input_number):", text, re.MULTILINE):
            return
        yaml_path.write_text(text.rstrip("\n") + "\n\ninput_number:\n", encoding="utf-8")
