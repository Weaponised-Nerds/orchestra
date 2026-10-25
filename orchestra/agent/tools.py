"""
Zenith AI Commissioning Agent Tool-Calling Runtime.

Implements the `ZenithAgentTools` specification from `ZENITH_MICRO_NODE_ARCHITECTURE.md`
Section 4.1 so the conversational AI Concierge can onboard, rename, test, and
commit demand-response automations without exposing raw Home Assistant entity IDs
to the homeowner.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
import uuid

from orchestra.models import (
    DeviceCategory,
    DiagnosticResult,
    DiscoveredHardware,
    LoadShedContract,
    UserEnergyPreferences,
)

if TYPE_CHECKING:
    from orchestra.orchestrator import OrchestraEngine


CATEGORY_ALIAS_MAP: dict[str, DeviceCategory] = {
    "hvac": DeviceCategory.AIRCONDITIONING,
    "evse": DeviceCategory.EV_CHARGER,
    "hpwh": DeviceCategory.HEAT_PUMP,
    "bess": DeviceCategory.SOLAR_BATTERY,
    "solar_battery": DeviceCategory.SOLAR_BATTERY,
    "airconditioning": DeviceCategory.AIRCONDITIONING,
    "heat_pump": DeviceCategory.HEAT_PUMP,
    "ev_charger": DeviceCategory.EV_CHARGER,
}


class ZenithAgentCommissioningTools:
    """Validated tool functions exposed to the Zenith AI Commissioning Concierge."""

    def __init__(self, engine: OrchestraEngine) -> None:
        self.engine = engine

    async def get_discovered_appliances(self) -> list[DiscoveredHardware]:
        """Return all discovered and cloud-liberated appliances on the local subnet."""
        if not self.engine.discovered_devices:
            await self.engine.discover_and_hijack_all(simulate_all_first_pass_brands=True)
        return list(self.engine.discovered_devices.values())

    async def map_device_to_category(self, device_id: str, category: str) -> bool:
        """Map or reclassify a discovered device to `'hvac' | 'evse' | 'hpwh' | 'bess'`."""
        hw = self.engine.discovered_devices.get(device_id)
        if hw is None or category.lower() not in CATEGORY_ALIAS_MAP:
            return False
        hw.category = CATEGORY_ALIAS_MAP[category.lower()]
        return True

    async def assign_device_metadata(
        self,
        device_id: str,
        friendly_name: str,
        room: str,
    ) -> bool:
        """Rename a discovered appliance and assign it to a room in Home Assistant's device registry."""
        hw = self.engine.discovered_devices.get(device_id)
        if hw is not None:
            hw.SuggestedArea = room
        return self.engine.storage_injector.update_device_metadata(
            device_id=device_id,
            friendly_name=friendly_name,
            room_name=room,
        )

    async def test_appliance_controllability(
        self,
        device_id: str,
        action: str = "throttle_50_pct",
    ) -> DiagnosticResult:
        """Run a silent local controllability pulse check and verify safety interlocks."""
        return await self.engine.verify_device_control(device_id=device_id, action=action)

    async def generate_household_load_contract(
        self,
        preferences: UserEnergyPreferences,
        asset_automations: list[dict[str, Any]] | None = None,
    ) -> LoadShedContract:
        """
        Compile a personalized behind-the-meter Load Shed & Solar Soak Contract
        conditioning on homeowner comfort preferences and hardware safety guardrails.

        `asset_automations` (from `ConductorAutomationBuilder`) replace the fixed
        signal-based automations: they follow each Conductor asset directive on the
        household's real entities instead of hard-coded ones.
        """
        enrolled_ids = list(self.engine.discovered_devices.keys())
        safety_cfg = self.engine.config.safety
        drift_target_c = min(
            preferences.summer_ac_max_comfort_c,
            23.0 + safety_cfg.hvac_max_setpoint_offset_c,
        )

        automations: list[dict[str, Any]] = [
            {
                "id": "orchestra_ngangk_solar_soak_ev_and_hpwh",
                "alias": "Conductor Solar Soak: EV Charger & 65°C Heat Pump Thermal Boost",
                "description": (
                    "Triggered when Ngangk WEM RTP price is negative or in midday solar soak window. "
                    "Ramps OCPP EV chargers and boosts Heat Pump Hot Water to 65°C thermal storage."
                ),
                "trigger": [
                    {
                        "platform": "state",
                        "entity_id": "sensor.conductor_dr_ngangk_wem_market_signal",
                        "to": "SOLAR_SOAK",
                    }
                ],
                "action": [
                    {
                        "service": "water_heater.set_temperature",
                        "target": {"entity_id": "all"},
                        "data": {
                            "temperature": safety_cfg.hpwh_solar_boost_temp_c,
                            "operation_mode": "high_demand",
                        },
                    },
                    {
                        "service": "number.set_value",
                        "target": {"entity_id": "number.fronius_wattpilot_charging_current"},
                        "data": {"value": 16},
                    },
                ],
            },
            {
                "id": "orchestra_ngangk_precool_thermal_battery",
                "alias": "Conductor Thermal Pre-Cool: 1:00 PM Solar Pre-Conditioning",
                "description": "Pre-cools living areas using midday rooftop solar prior to evening microgrid peak.",
                "trigger": [{"platform": "time", "at": "13:00:00"}],
                "action": [
                    {
                        "service": "climate.set_temperature",
                        "target": {"entity_id": "climate.airtouch5_ducted_ac"},
                        "data": {"temperature": preferences.precool_target_c, "hvac_mode": "cool"},
                    }
                ],
            },
            {
                "id": "orchestra_ngangk_peak_shed_guardrailed",
                "alias": "Conductor Peak Event: Guardrailed HVAC Drift, HPWH Coast & EV Pause",
                "description": (
                    f"Nudges HVAC setpoint to {drift_target_c}°C (enforcing {safety_cfg.hvac_min_dwell_seconds}s "
                    "min compressor dwell), coasts Heat Pump thermal tank, and pauses EV charging."
                ),
                "trigger": [
                    {
                        "platform": "state",
                        "entity_id": "sensor.conductor_dr_ngangk_wem_market_signal",
                        "to": "PEAK_DISCHARGE",
                    }
                ],
                "condition": [
                    {
                        "condition": "state",
                        "entity_id": "switch.conductor_dr_event_opt_out",
                        "state": "off",
                    }
                ],
                "action": [
                    {
                        "service": "climate.set_temperature",
                        "target": {"entity_id": "climate.airtouch5_ducted_ac"},
                        "data": {"temperature": drift_target_c},
                    },
                    {
                        "service": "water_heater.set_temperature",
                        "target": {"entity_id": "all"},
                        "data": {
                            "temperature": safety_cfg.hpwh_peak_coast_min_temp_c,
                            "operation_mode": "eco",
                        },
                    },
                    {
                        "service": "number.set_value",
                        "target": {"entity_id": "number.tesla_wall_connector_charging_amps_limit"},
                        "data": {"value": safety_cfg.evse_min_pilot_amps},
                    },
                ],
            },
        ]

        if asset_automations is not None:
            automations = list(asset_automations)

        return LoadShedContract(
            contract_id=f"zenith-contract-{uuid.uuid4().hex[:8]}",
            created_at=datetime.now(timezone.utc).isoformat(),
            preferences=preferences,
            enrolled_device_ids=enrolled_ids,
            estimated_monthly_savings_aud=42.50,
            sheddable_capacity_kw=4.2,
            ha_automations=automations,
            safety_interlocks={
                "hvac_min_dwell_seconds": safety_cfg.hvac_min_dwell_seconds,
                "hvac_max_setpoint_offset_c": safety_cfg.hvac_max_setpoint_offset_c,
                "forbid_raw_mains_cut_on_hvac": safety_cfg.forbid_raw_mains_cut_on_hvac,
                "hpwh_solar_boost_temp_c": safety_cfg.hpwh_solar_boost_temp_c,
                "hpwh_peak_coast_min_temp_c": safety_cfg.hpwh_peak_coast_min_temp_c,
                "evse_min_pilot_amps": safety_cfg.evse_min_pilot_amps,
                "bess_min_backup_soc_pct": safety_cfg.bess_min_backup_soc_pct,
            },
        )

    async def commit_automations_to_node(self, contract: LoadShedContract) -> bool:
        """Write the approved LoadShedContract automations directly into Home Assistant."""
        path = self.engine.storage_injector.write_automations(contract.ha_automations)
        return path.exists()
