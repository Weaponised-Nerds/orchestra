"""
Semantic Device & Entity Namer + Standard Home Assistant Entity Organizer.

Ensures that both:
1. Orchestra's zero-cloud energy/HVAC/EVSE/Heat Pump hardware, AND
2. Any standard Home Assistant auto-discovered integrations (Smart TVs, Sonos/Cast
   speakers, Hue/LIFX lights, Garage doors, Shelly CT clamps, UPnP routers)
are automatically:
- Given clear, Grandpa-friendly names (e.g., `media_player.lg_webos_oled65_88a1` -> `"Lounge Room TV"`).
- Assigned to logical rooms in `.storage/core.area_registry` and `.storage/core.entity_registry`.
- Scrubbed of technical/diagnostic noise (`*_rssi`, `*_uptime`, `*_reboot`, `*_mac`, `*_firmware`),
  which are automatically marked `hidden_by="orchestra_agent"`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from orchestra.agent.circuit_identifier import CircuitIdentificationMatch
from orchestra.hijacker.storage_injector import HAStorageInjector
from orchestra.models import HAEntitySpec


@dataclass
class OrganizedEntityRecord:
    """Summary of how the Agent named, room-assigned, and curated an entity."""

    entity_id: str
    domain: str
    raw_name: str
    sensible_name: str
    assigned_room: str
    hidden_from_grandpa: bool = False
    source_type: str = "orchestra"  # "orchestra" | "standard_ha_discovery" | "circuit_prober"


# Keywords that indicate technical/diagnostic clutter Grandpa should never see
DIAGNOSTIC_NOISE_PATTERNS: tuple[str, ...] = (
    r"_rssi$",
    r"_lqi$",
    r"_uptime$",
    r"_reboot$",
    r"_restart$",
    r"_ip_address$",
    r"_mac_address$",
    r"_firmware",
    r"_wan_bytes",
    r"_cpu_temp",
    r"_memory_free",
    r"^update\.",
    r"^button\..*restart",
)

# Room inference rules based on tokens in entity_id or raw friendly name
ROOM_TOKEN_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("kitchen", "kettle", "dishwasher", "fridge", "oven"), "Kitchen & Dining"),
    (("lounge", "living", "tv", "oled", "sonos", "soundbar", "family"), "Living & Bedrooms"),
    (("master", "bedroom", "bedside", "nursery", "kids"), "Living & Bedrooms"),
    (("study", "office", "desk"), "Study / Office"),
    (("garage", "roller_door", "powerwall", "sigenstor", "istore", "hot_water", "shelly", "contactor"), "Garage / Utility"),
    (("driveway", "ev", "wattpilot", "wall_connector", "evnex", "charger"), "Driveway / EV Bay"),
    (("porch", "patio", "alfresco", "garden", "pool", "front_door", "ring"), "Outdoor & Porch"),
    (("roof", "solar", "fronius", "enphase", "pv"), "Roof / Solar Array"),
    (("bom", "weather", "ngangk", "conductor"), "Grid & Weather Intelligence"),
)

# Sensible Grandpa-friendly rewrites for common cryptic entity IDs
FRIENDLY_NAME_REWRITES: dict[str, str] = {
    "climate.airtouch5_ducted_ac": "Main House Air Conditioner",
    "climate.sensibo_split_ac": "Lounge Split-System Air Conditioner",
    "climate.advantage_air_myair_ac1": "MyAir Ducted Air Conditioner",
    "water_heater.istore_heat_pump_hot_water": "iStore Hot Water Heat Pump",
    "water_heater.stiebel_eltron_hpwh": "Stiebel Eltron Hot Water Heat Pump",
    "water_heater.rinnai_enviroflo": "Rinnai Hot Water Heat Pump",
    "water_heater.rheem_ambipower": "Rheem Hot Water Heat Pump",
    "water_heater.tuya_whitelabel_hpwh": "Smart Hot Water Heat Pump",
    "sensor.tesla_powerwall_battery_soc": "Home Battery Level (%)",
    "sensor.tesla_powerwall_solar_power": "Solar Roof Generation",
    "sensor.tesla_powerwall_site_grid_power": "Street Grid Power",
    "sensor.sigenstor_battery_soc": "Sigenergy Home Battery Level (%)",
    "sensor.sigenstor_pv_power": "Sigenergy Solar Generation",
    "switch.conductor_dr_event_opt_out": "Pause Grid Saving Event (Full Comfort Now)",
    "sensor.conductor_dr_accumulated_dividend_aud": "Total Cash Earned & Saved ($)",
    "sensor.conductor_dr_ngangk_wem_market_signal": "Current Electricity Price Mode",
}


class SemanticEntityOrganizer:
    """
    Organizes, renames, and assigns rooms to all Orchestra entities AND standard
    Home Assistant auto-discovered entities.
    """

    def __init__(self, storage_injector: HAStorageInjector) -> None:
        self.storage_injector = storage_injector

    def seed_standard_ha_discovered_entities(self) -> list[HAEntitySpec]:
        """
        Simulate/collect the standard consumer Home Assistant entities that HA's
        built-in discovery (`zeroconf`, `ssdp`, `dhcp`, `bluetooth`) pulls in on a
        typical home LAN (Smart TVs, lights, garage door, unidentified Shelly 3EM
        CT clamp channels, and router diagnostic noise).
        """
        standard_entities = [
            # Unidentified 3-phase / 3-channel CT Clamp Power Monitors in Switchboard
            HAEntitySpec(
                entity_id="sensor.shelly_pro_3em_channel_a_power",
                domain="sensor",
                friendly_name="shelly_pro_3em_08f9e0_channel_a_power",
                device_class="power",
                unit_of_measurement="W",
                initial_state=120.0,
            ),
            HAEntitySpec(
                entity_id="sensor.shelly_pro_3em_channel_b_power",
                domain="sensor",
                friendly_name="shelly_pro_3em_08f9e0_channel_b_power",
                device_class="power",
                unit_of_measurement="W",
                initial_state=85.0,
            ),
            HAEntitySpec(
                entity_id="sensor.shelly_pro_3em_channel_c_power",
                domain="sensor",
                friendly_name="shelly_pro_3em_08f9e0_channel_c_power",
                device_class="power",
                unit_of_measurement="W",
                initial_state=40.0,
            ),
            HAEntitySpec(
                entity_id="sensor.shelly_em_aux_circuit_4_power",
                domain="sensor",
                friendly_name="shelly_em_aux_circuit_4_power",
                device_class="power",
                unit_of_measurement="W",
                initial_state=110.0,
            ),
            # Standard Home Assistant consumer smart home devices
            HAEntitySpec(
                entity_id="media_player.lg_webos_tv_oled65_992a",
                domain="media_player",
                friendly_name="LG webOS TV OLED65C3PSA (992A)",
                initial_state="off",
            ),
            HAEntitySpec(
                entity_id="light.philips_hue_lounge_lamp_01",
                domain="light",
                friendly_name="hue_color_lamp_01_lounge",
                initial_state="on",
            ),
            HAEntitySpec(
                entity_id="light.lifx_front_porch_downlight",
                domain="light",
                friendly_name="LIFX_A19_22819A_porch",
                initial_state="off",
            ),
            HAEntitySpec(
                entity_id="cover.meross_msg100_garage_roller_door",
                domain="cover",
                friendly_name="meross_msg100_main_garage_door",
                device_class="garage",
                initial_state="closed",
            ),
            # Technical / diagnostic clutter that should be hidden from Grandpa
            HAEntitySpec(
                entity_id="sensor.shelly_pro_3em_wifi_rssi",
                domain="sensor",
                friendly_name="shelly_pro_3em_08f9e0_wifi_rssi",
                device_class="signal_strength",
                unit_of_measurement="dBm",
                initial_state=-64,
            ),
            HAEntitySpec(
                entity_id="button.shelly_pro_3em_reboot",
                domain="button",
                friendly_name="shelly_pro_3em_08f9e0_reboot",
            ),
        ]

        # Register them in HA's entity registry under standard discovery
        self.storage_injector._upsert_entities(
            config_entry_id="standard_ha_auto_discovery",
            device_reg_id="standard_ha_discovered_bundle",
            area_id="garage_utility",
            entities=standard_entities,
        )
        return standard_entities

    def organize_all_entities(
        self,
        circuit_matches: list[CircuitIdentificationMatch] | None = None,
    ) -> list[OrganizedEntityRecord]:
        """
        Sweep `.storage/core.entity_registry`, apply circuit identification names,
        humanize cryptic entity names, assign rooms, and hide diagnostic clutter.
        """
        circuit_map: dict[str, CircuitIdentificationMatch] = {
            m.sensor_entity_id: m for m in (circuit_matches or [])
        }

        ent_doc = self.storage_injector._read_storage("core.entity_registry")
        entities_list: list[dict[str, Any]] = ent_doc["data"]["entities"]
        organized_records: list[OrganizedEntityRecord] = []

        for ent in entities_list:
            entity_id = str(ent.get("entity_id", ""))
            domain = entity_id.split(".", 1)[0] if "." in entity_id else "sensor"
            raw_name = str(ent.get("original_name") or ent.get("name") or entity_id)

            # 1. Check if this is a power monitor channel identified by ActiveCircuitIdentifier
            if entity_id in circuit_map:
                match = circuit_map[entity_id]
                sensible_name = match.assigned_friendly_name
                room_name = match.assigned_area
                area_id = self.storage_injector._resolve_area_id(room_name)
                ent["name"] = sensible_name
                ent["area_id"] = area_id
                ent["labels"] = list(set(ent.get("labels", []) + ["circuit_verified"]))
                organized_records.append(
                    OrganizedEntityRecord(
                        entity_id=entity_id,
                        domain=domain,
                        raw_name=raw_name,
                        sensible_name=sensible_name,
                        assigned_room=room_name,
                        hidden_from_grandpa=False,
                        source_type="circuit_prober",
                    )
                )
                continue

            # 2. Check if this entity is technical/diagnostic noise
            is_noise = self._is_diagnostic_noise(entity_id, raw_name)
            if is_noise:
                ent["hidden_by"] = "orchestra_agent"
                ent["entity_category"] = "diagnostic"

            # 3. Compute a clean, Grandpa-sensible friendly name and room
            sensible_name = self._humanize_name(entity_id, raw_name)
            room_name = self._infer_room(entity_id, raw_name)
            area_id = self.storage_injector._resolve_area_id(room_name)

            ent["name"] = sensible_name
            ent["area_id"] = area_id

            source_type = (
                "standard_ha_discovery"
                if ent.get("config_entry_id") == "standard_ha_auto_discovery"
                else "orchestra"
            )
            organized_records.append(
                OrganizedEntityRecord(
                    entity_id=entity_id,
                    domain=domain,
                    raw_name=raw_name,
                    sensible_name=sensible_name,
                    assigned_room=room_name,
                    hidden_from_grandpa=is_noise,
                    source_type=source_type,
                )
            )

        self.storage_injector._write_storage("core.entity_registry", ent_doc)
        return organized_records

    @staticmethod
    def _is_diagnostic_noise(entity_id: str, raw_name: str) -> bool:
        combined = f"{entity_id.lower()} {raw_name.lower()}"
        return any(re.search(pattern, combined) for pattern in DIAGNOSTIC_NOISE_PATTERNS)

    @staticmethod
    def _infer_room(entity_id: str, raw_name: str) -> str:
        combined = f"{entity_id.lower()} {raw_name.lower()}"
        for tokens, room_name in ROOM_TOKEN_RULES:
            if any(tok in combined for tok in tokens):
                return room_name
        return "Living & Bedrooms"

    @staticmethod
    def _humanize_name(entity_id: str, raw_name: str) -> str:
        if entity_id in FRIENDLY_NAME_REWRITES:
            return FRIENDLY_NAME_REWRITES[entity_id]

        low_id = entity_id.lower()
        if "lg_webos_tv" in low_id or "oled" in low_id:
            return "Lounge Room Smart TV"
        if "philips_hue" in low_id and "lounge" in low_id:
            return "Lounge Room Lamp"
        if "lifx" in low_id and "porch" in low_id:
            return "Front Porch Light"
        if "garage_roller_door" in low_id or "meross_msg100" in low_id:
            return "Main Garage Roller Door"

        # Fallback: strip MAC/hex suffixes like `_992a` or `_08f9e0` and title-case
        cleaned = re.sub(r"_[0-9a-fA-F]{4,8}\b", "", raw_name)
        cleaned = cleaned.replace("_", " ").strip()
        return cleaned if cleaned else raw_name
