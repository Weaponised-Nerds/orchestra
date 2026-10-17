"""
Conductor Demand Response Entity Binder (`orchestra/agent/dr_binder.py`).

Automatically binds the Agent's semantically named entities and load-step-verified
circuit power monitors into the `conductor` Demand Response plugin inside Home Assistant.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
from typing import Any

from orchestra.agent.circuit_identifier import CircuitIdentificationMatch
from orchestra.agent.entity_organizer import OrganizedEntityRecord
from orchestra.hijacker.storage_injector import HAStorageInjector


@dataclass
class ControllableLoadBinding:
    """Pairs a controllable appliance entity with its verified CT circuit power sensor."""

    control_entity_id: str
    sensible_name: str
    room: str
    load_category: str  # "hvac" | "heat_pump" | "ev_charger" | "smart_contactor" | "battery_ems"
    verified_power_sensor_entity_id: str | None
    verified_power_sensor_name: str | None
    dispatch_priority: int
    safety_profile: str


@dataclass
class ConductorBindingManifest:
    """Complete mapping of named entities assigned to the Conductor Demand Response plugin."""

    bound_at: str
    solar_generation_sensors: list[str] = field(default_factory=list)
    battery_soc_sensors: list[str] = field(default_factory=list)
    grid_net_power_sensors: list[str] = field(default_factory=list)
    weather_irradiance_sensors: list[str] = field(default_factory=list)
    opt_out_switch_entity_id: str = "switch.conductor_dr_event_opt_out"
    market_signal_sensor_entity_id: str = "sensor.conductor_dr_ngangk_wem_market_signal"
    controllable_loads: list[ControllableLoadBinding] = field(default_factory=list)


class ConductorEntityBinder:
    """
    Assigns all semantically named energy, HVAC, heat pump, EV charger, and
    circuit monitor entities to the Conductor Demand Response integration.
    """

    def __init__(self, storage_injector: HAStorageInjector) -> None:
        self.storage_injector = storage_injector

    def bind_entities_to_conductor(
        self,
        organized_entities: list[OrganizedEntityRecord],
        circuit_matches: list[CircuitIdentificationMatch],
    ) -> ConductorBindingManifest:
        """
        Build the Conductor Demand Response entity binding manifest and persist it
        into `.storage/conductor.entity_bindings` and the `conductor` ConfigEntry.
        """
        # Map appliance_entity_id -> verified circuit match
        appliance_to_circuit: dict[str, CircuitIdentificationMatch] = {
            m.linked_appliance_entity_id: m
            for m in circuit_matches
            if m.linked_appliance_entity_id is not None
        }

        solar_sensors: list[str] = []
        battery_soc_sensors: list[str] = []
        grid_sensors: list[str] = []
        weather_sensors: list[str] = []
        controllable_loads: list[ControllableLoadBinding] = []

        for rec in organized_entities:
            if rec.hidden_from_grandpa:
                continue
            eid = rec.entity_id

            # 1. Site-level telemetry sensors
            if eid.startswith("sensor.") and any(
                k in eid for k in ("solar_power", "pv_power", "power_photovoltaics", "power_production")
            ):
                solar_sensors.append(eid)
            elif eid.startswith("sensor.") and any(
                k in eid for k in ("battery_soc", "state_of_charge")
            ):
                battery_soc_sensors.append(eid)
            elif eid.startswith("sensor.") and any(
                k in eid for k in ("grid_power", "net_power_consumption")
            ):
                grid_sensors.append(eid)
            elif eid.startswith(("sensor.bom_", "binary_sensor.bom_", "weather.bom_")):
                weather_sensors.append(eid)

            # 2. Controllable Demand Response Loads
            if eid.startswith("water_heater."):
                c_match = appliance_to_circuit.get(eid)
                controllable_loads.append(
                    ControllableLoadBinding(
                        control_entity_id=eid,
                        sensible_name=rec.sensible_name,
                        room=rec.assigned_room,
                        load_category="heat_pump",
                        verified_power_sensor_entity_id=c_match.sensor_entity_id if c_match else None,
                        verified_power_sensor_name=c_match.assigned_friendly_name if c_match else None,
                        dispatch_priority=1,  # First to soak solar at 65°C, first to coast at peak
                        safety_profile="hpwh_thermal_65c_boost_50c_coast_no_dry_cycle",
                    )
                )
            elif eid.startswith("climate.") and "zone_" not in eid and "_z0" not in eid:
                c_match = appliance_to_circuit.get(eid)
                controllable_loads.append(
                    ControllableLoadBinding(
                        control_entity_id=eid,
                        sensible_name=rec.sensible_name,
                        room=rec.assigned_room,
                        load_category="hvac",
                        verified_power_sensor_entity_id=c_match.sensor_entity_id if c_match else None,
                        verified_power_sensor_name=c_match.assigned_friendly_name if c_match else None,
                        dispatch_priority=2,
                        safety_profile="hvac_15m_min_dwell_setpoint_offset_max_2c",
                    )
                )
            elif eid.startswith("number.") and any(
                k in eid for k in ("charging_amps", "current_limit", "charging_current", "maximum_current")
            ):
                c_match = appliance_to_circuit.get(eid)
                controllable_loads.append(
                    ControllableLoadBinding(
                        control_entity_id=eid,
                        sensible_name=rec.sensible_name,
                        room=rec.assigned_room,
                        load_category="ev_charger",
                        verified_power_sensor_entity_id=c_match.sensor_entity_id if c_match else None,
                        verified_power_sensor_name=c_match.assigned_friendly_name if c_match else None,
                        dispatch_priority=1,
                        safety_profile="evse_ocpp_pilot_modulation_6a_to_32a",
                    )
                )
            elif eid == "switch.tuya_local_contactor_relay":
                c_match = appliance_to_circuit.get(eid)
                controllable_loads.append(
                    ControllableLoadBinding(
                        control_entity_id=eid,
                        sensible_name=rec.sensible_name,
                        room=rec.assigned_room,
                        load_category="smart_contactor",
                        verified_power_sensor_entity_id=c_match.sensor_entity_id if c_match else None,
                        verified_power_sensor_name=c_match.assigned_friendly_name if c_match else None,
                        dispatch_priority=1,
                        safety_profile="din_contactor_non_essential_load_shed",
                    )
                )

        manifest = ConductorBindingManifest(
            bound_at=datetime.now(timezone.utc).isoformat(),
            solar_generation_sensors=solar_sensors,
            battery_soc_sensors=battery_soc_sensors,
            grid_net_power_sensors=grid_sensors,
            weather_irradiance_sensors=weather_sensors,
            controllable_loads=controllable_loads,
        )

        # Persist to `.storage/conductor.entity_bindings`
        binding_payload: dict[str, Any] = {
            "version": 1,
            "minor_version": 1,
            "key": "conductor.entity_bindings",
            "data": asdict(manifest),
        }
        (self.storage_injector.storage_dir / "conductor.entity_bindings").write_text(
            json.dumps(binding_payload, indent=2),
            encoding="utf-8",
        )

        # Also update the `conductor` ConfigEntry options in `.storage/core.config_entries`
        entries_doc = self.storage_injector._read_storage("core.config_entries")
        for entry in entries_doc["data"]["entries"]:
            if entry.get("domain") == "conductor":
                entry["options"] = {
                    "bound_controllable_loads_count": len(controllable_loads),
                    "bound_solar_sensors": solar_sensors,
                    "bound_battery_soc_sensors": battery_soc_sensors,
                    "bound_grid_sensors": grid_sensors,
                    "bound_loads": [asdict(load) for load in controllable_loads],
                }
        self.storage_injector._write_storage("core.config_entries", entries_doc)

        return manifest
