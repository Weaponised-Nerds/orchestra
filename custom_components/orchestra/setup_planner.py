"""
Decisions the Orchestra setup agent makes about a freshly discovered home.

Kept free of Home Assistant imports so the rules can be tested on their own:
- which discovery flows can be confirmed without a person, and what to ask for when not,
- which room and asset label a device belongs to,
- which entities fill each Conductor asset blueprint.

`setup_agent.py` feeds these from the live registries and acts on the results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

try:
    import voluptuous as vol

    _UNDEFINED: Any = vol.UNDEFINED
except ImportError:  # unit tests outside Home Assistant
    vol = None  # type: ignore
    _UNDEFINED = object()

try:
    from .blueprint_installer import (
        BLUEPRINT_EV_CHARGE_RATE,
        BLUEPRINT_HEAT_PUMP_TARGET,
        BLUEPRINT_HOME_BATTERY_RESERVE,
        BLUEPRINT_HVAC_SETPOINT_OFFSET,
        blueprint_path,
    )
except ImportError:
    from custom_components.orchestra.blueprint_installer import (
        BLUEPRINT_EV_CHARGE_RATE,
        BLUEPRINT_HEAT_PUMP_TARGET,
        BLUEPRINT_HOME_BATTERY_RESERVE,
        BLUEPRINT_HVAC_SETPOINT_OFFSET,
        blueprint_path,
    )

# Config flow sources that come from Home Assistant's own discovery
DISCOVERY_SOURCES: frozenset[str] = frozenset(
    {
        "bluetooth",
        "dhcp",
        "discovery",
        "hassio",
        "homekit",
        "integration_discovery",
        "mqtt",
        "ssdp",
        "usb",
        "zeroconf",
    }
)

# Plain words for the form fields a person has to fill in
FIELD_PHRASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("pairing_code", "pin", "code", "setup_code"), "the pairing code or PIN"),
    (("serial",), "the serial number"),
    (("password",), "the password"),
    (("username", "email", "user"), "the account login"),
    (("api_key", "token", "key", "secret"), "an API key"),
    (("host", "ip_address", "address", "url"), "the device address"),
)

# Flow errors that mean "press something on the device, then try again"
PRESS_BUTTON_ERRORS: frozenset[str] = frozenset(
    {"link_button_not_pressed", "register_failed", "button_not_pressed", "not_pressed"}
)

# Room words in device and entity names, most specific first
AREA_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("kitchen", "dishwasher", "fridge", "oven", "kettle", "pantry"), "Kitchen"),
    (("laundry", "washer", "dryer", "washing"), "Laundry"),
    (("bathroom", "ensuite", "toilet"), "Bathroom"),
    (("master", "bedroom", "bed_", "nursery", "kids"), "Bedroom"),
    (("study", "office", "desk"), "Study"),
    (("lounge", "living", "family", "theatre", "tv"), "Living room"),
    (("dining",), "Dining room"),
    (("garage", "roller_door", "workshop"), "Garage"),
    (("pool", "spa"), "Pool"),
    (("driveway", "carport", "wall_connector", "wallbox", "evse", "ev_charger"), "Driveway"),
    (("porch", "patio", "alfresco", "garden", "yard", "outdoor", "front_door", "doorbell"), "Outdoor"),
    (("solar", "inverter", "powerwall", "battery", "envoy", "fronius", "sigen", "switchboard", "meter"), "Utility"),
    (("hot_water", "water_heater", "heat_pump"), "Utility"),
)

LABEL_ENERGY = "Solar & battery"
LABEL_HVAC = "Heating & cooling"
LABEL_HOT_WATER = "Hot water"
LABEL_EV = "EV charging"
LABEL_LIGHTING = "Lighting"
LABEL_MEDIA = "Entertainment"
LABEL_SECURITY = "Security"
ORCHESTRA_LABELS: tuple[str, ...] = (
    LABEL_ENERGY,
    LABEL_HVAC,
    LABEL_HOT_WATER,
    LABEL_EV,
    LABEL_LIGHTING,
    LABEL_MEDIA,
    LABEL_SECURITY,
)

EV_WORDS = ("charg", "evse", "wallbox", "wall_connector", "wattpilot", "evnex", "ocpp")
HEAT_PUMP_WORDS = ("heat_pump", "heatpump", "hot_water", "water_heater", "hpwh")
BATTERY_RESERVE_WORDS = ("reserve", "backup", "min_soc")


@dataclass
class EntityInfo:
    """What the agent knows about one entity, flattened from the registries and state."""

    entity_id: str
    name: str = ""
    device_id: str | None = None
    device_name: str = ""
    via_device_id: str | None = None
    config_entry_id: str | None = None
    platform: str = ""
    unit: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def text(self) -> str:
        """Everything that names this entity, lowercased with `_` for spaces."""
        raw = f"{self.entity_id} {self.name} {self.device_name}".lower()
        return re.sub(r"[\s\-]+", "_", raw)


@dataclass
class PunchItem:
    """One thing the homeowner (or a person helping them) must do."""

    key: str
    summary: str
    description: str


@dataclass
class FlowDecision:
    """What to do with one step of a discovery flow."""

    action: str  # "submit" or "ask"
    user_input: dict[str, Any] = field(default_factory=dict)
    punch: PunchItem | None = None


def _field_name(key: Any) -> str:
    return str(getattr(key, "schema", key))


def required_fields(data_schema: Any) -> list[str]:
    """Names of form fields that are required and have no default."""
    if data_schema is None:
        return []
    keys = getattr(data_schema, "schema", data_schema)
    if not isinstance(keys, dict):
        return []
    fields: list[str] = []
    for key in keys:
        if type(key).__name__ != "Required":
            continue
        default = getattr(key, "default", _UNDEFINED)
        if default is _UNDEFINED or default is None:
            fields.append(_field_name(key))
    return fields


def describe_fields(fields: list[str]) -> str:
    """'the pairing code or PIN and the serial number' for a list of field names."""
    phrases: list[str] = []
    for name in fields:
        low = name.lower()
        phrase = next(
            (text for words, text in FIELD_PHRASES if any(word in low for word in words)),
            f"“{name.replace('_', ' ')}”",
        )
        if phrase not in phrases:
            phrases.append(phrase)
    if len(phrases) <= 1:
        return phrases[0] if phrases else "some details"
    return ", ".join(phrases[:-1]) + " and " + phrases[-1]


def flow_title(flow: dict[str, Any]) -> str:
    """A readable name for a discovered device, e.g. 'Philips Hue (192.168.1.20)'."""
    placeholders = (flow.get("context") or {}).get("title_placeholders") or {}
    name = placeholders.get("name") or placeholders.get("model") or flow.get("handler", "device")
    host = placeholders.get("host")
    return f"{name} ({host})" if host and host not in str(name) else str(name)


def decide_flow_step(flow: dict[str, Any], result: dict[str, Any]) -> FlowDecision:
    """
    Decide whether the agent can finish this step on its own.

    A form with nothing required is a confirmation, so the agent submits it. A form that
    needs input, came back with an error, or any other kind of step goes on the punch list.
    """
    handler = flow.get("handler", "")
    title = flow_title(flow)
    key = f"flow:{flow.get('flow_id')}"
    result_type = str(result.get("type", ""))
    errors = result.get("errors") or {}

    if result_type == "form":
        fields = required_fields(result.get("data_schema"))
        if errors:
            error_codes = {str(code) for code in errors.values()}
            if error_codes & PRESS_BUTTON_ERRORS:
                return FlowDecision(
                    "ask",
                    punch=PunchItem(
                        key,
                        f"Press the pairing button on {title}",
                        f"Press the link or pairing button on {title}, then open "
                        f"Settings → Devices & services and select Configure on the "
                        f"discovered {handler} card.",
                    ),
                )
            if not fields:
                return FlowDecision(
                    "ask",
                    punch=PunchItem(
                        key,
                        f"Finish adding {title}",
                        f"Home Assistant could not add {title} on its own "
                        f"({', '.join(sorted(error_codes))}). Open Settings → Devices & "
                        f"services and select Configure on the discovered {handler} card.",
                    ),
                )
        if not fields:
            return FlowDecision("submit")
        return FlowDecision(
            "ask",
            punch=PunchItem(
                key,
                f"Add {title}: needs {describe_fields(fields)}",
                f"Home Assistant found {title} but needs {describe_fields(fields)} to add it. "
                f"Open Settings → Devices & services and select Configure on the discovered "
                f"{handler} card.",
            ),
        )

    if result_type in ("external", "show_progress", "progress", "menu"):
        return FlowDecision(
            "ask",
            punch=PunchItem(
                key,
                f"Finish adding {title}",
                f"{title} needs a choice or a sign-in that only you can do. Open Settings → "
                f"Devices & services and select Configure on the discovered {handler} card.",
            ),
        )

    return FlowDecision("ask")


def infer_area(text: str) -> str | None:
    """A room for a device from the words in its names, or None when unsure."""
    low = text.lower()
    for words, area in AREA_RULES:
        if any(word in low for word in words):
            return area
    return None


def asset_label(entities: list[EntityInfo]) -> str | None:
    """Which kind of equipment a device is, from its entities."""
    domains = {e.domain for e in entities}
    text = " ".join(e.text for e in entities)
    if "water_heater" in domains or any(w in text for w in HEAT_PUMP_WORDS):
        return LABEL_HOT_WATER
    if any(w in text for w in EV_WORDS) and ("number" in domains or "switch" in domains):
        return LABEL_EV
    if "climate" in domains:
        return LABEL_HVAC
    if any(w in text for w in ("solar", "inverter", "powerwall", "battery_level", "pv_power", "envoy", "grid_power")):
        return LABEL_ENERGY
    if domains & {"alarm_control_panel", "lock", "camera"}:
        return LABEL_SECURITY
    if "media_player" in domains:
        return LABEL_MEDIA
    if domains == {"light"} or ("light" in domains and len(domains) <= 2):
        return LABEL_LIGHTING
    return None


def _automation(automation_id: str, alias: str, blueprint: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": automation_id,
        "alias": alias,
        "description": "Set up by the Orchestra setup agent. Check the entities it picked.",
        "use_blueprint": {"path": blueprint_path(blueprint), "input": inputs},
    }


def _object_id(entity_id: str) -> str:
    return entity_id.split(".", 1)[1]


def _hvac_climates(climates: list[EntityInfo], device_ids_of_climates: set[str]) -> list[EntityInfo]:
    """
    The climates that should drift. On zoned systems such as AirTouch the unit's own
    setpoint follows its zones and reverts direct changes, so drift the zones instead.
    A zone is a climate whose device hangs off another climate's device, or one named
    or classed as a zone.
    """
    zones = [
        c
        for c in climates
        if (c.via_device_id and c.via_device_id in device_ids_of_climates)
        or c.attributes.get("device_class") == "zone"
        or "zone" in c.text
    ]
    if not zones:
        return climates
    zoned_entries = {c.config_entry_id for c in zones}
    zone_parents = {c.via_device_id for c in zones if c.via_device_id}
    return [
        c
        for c in climates
        if c in zones
        or (c.config_entry_id not in zoned_entries and c.device_id not in zone_parents)
    ]


def _same_device(entities: list[EntityInfo], device_id: str | None, domain: str) -> list[EntityInfo]:
    if not device_id:
        return []
    return [e for e in entities if e.device_id == device_id and e.domain == domain]


def _heat_pump_power_switch(entities: list[EntityInfo], heater: EntityInfo) -> str | None:
    switches = _same_device(entities, heater.device_id, "switch")
    if not switches:
        return None
    for words in (("heat_pump", "heatpump", "power"), ("pump", "switch")):
        for sw in switches:
            if any(w in sw.text for w in words):
                return sw.entity_id
    return switches[0].entity_id if len(switches) == 1 else None


def plan_asset_automations(
    entities: list[EntityInfo],
    existing_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """
    Fill the four Conductor asset blueprints from the home's entities.

    Returns the new automations and a plain-English line per automation for the handover.
    Automations whose id is already in `existing_ids` are left alone, so a person's edits
    survive the agent's later passes.
    """
    existing = existing_ids or set()
    automations: list[dict[str, Any]] = []
    notes: list[str] = []

    def add(automation: dict[str, Any], note: str) -> None:
        if automation["id"] in existing:
            return
        automations.append(automation)
        notes.append(note)

    # Hot water / heat pumps: water heaters first, then climates or switches named as heat pumps
    heaters = [e for e in entities if e.domain == "water_heater"]
    if not heaters:
        heaters = [
            e
            for e in entities
            if e.domain in ("climate", "switch") and any(w in e.text for w in HEAT_PUMP_WORDS)
        ]
        # One per device: prefer the climate over its power switch
        seen_devices: set[str | None] = set()
        picked: list[EntityInfo] = []
        for e in sorted(heaters, key=lambda x: x.domain != "climate"):
            if e.device_id and e.device_id in seen_devices:
                continue
            seen_devices.add(e.device_id)
            picked.append(e)
        heaters = picked
    heater_ids = {h.entity_id for h in heaters}
    for heater in heaters:
        inputs: dict[str, Any] = {"water_heater": heater.entity_id}
        power = _heat_pump_power_switch(entities, heater) if heater.domain != "switch" else None
        if power:
            inputs["power_switch"] = power
        add(
            _automation(
                f"orchestra_heat_pump_{_object_id(heater.entity_id)}",
                f"Orchestra: {heater.name or heater.entity_id} follows Conductor",
                BLUEPRINT_HEAT_PUMP_TARGET,
                inputs,
            ),
            f"Heat pump: {heater.name or heater.entity_id}"
            + (f" (power switch {power})" if power else ""),
        )

    # Air conditioning: one automation drifting every A/C (or every zone)
    climates = [e for e in entities if e.domain == "climate" and e.entity_id not in heater_ids]
    climate_devices = {c.device_id for c in climates if c.device_id}
    hvac = _hvac_climates(climates, climate_devices)
    if hvac:
        ids = sorted(c.entity_id for c in hvac)
        add(
            _automation(
                "orchestra_hvac_offset",
                "Orchestra: air conditioning follows Conductor",
                BLUEPRINT_HVAC_SETPOINT_OFFSET,
                {"climate_entity": ids},
            ),
            "Air conditioning: " + ", ".join(c.name or c.entity_id for c in hvac),
        )

    # EV chargers: the charging current number on each charger, plus its charge switch
    seen_chargers: set[str] = set()
    for e in entities:
        if e.domain != "number" or (e.unit or "").upper() != "A":
            continue
        if not (any(w in e.text for w in EV_WORDS) and ("current" in e.text or "amp" in e.text or "charg" in e.text)):
            continue
        charger = e.device_id or e.entity_id
        if charger in seen_chargers:
            continue
        seen_chargers.add(charger)
        inputs = {"charger_current": e.entity_id}
        switch = next(
            (s.entity_id for s in _same_device(entities, e.device_id, "switch") if "charg" in s.text),
            None,
        )
        if switch:
            inputs["charger_switch"] = switch
        add(
            _automation(
                f"orchestra_ev_rate_{_object_id(e.entity_id)}",
                f"Orchestra: {e.device_name or e.name or e.entity_id} follows Conductor",
                BLUEPRINT_EV_CHARGE_RATE,
                inputs,
            ),
            f"EV charger: {e.device_name or e.name or e.entity_id}",
        )

    # Home batteries: the backup reserve percentage
    for e in entities:
        if e.domain != "number" or (e.unit or "") != "%":
            continue
        if not any(w in e.text for w in BATTERY_RESERVE_WORDS):
            continue
        add(
            _automation(
                f"orchestra_battery_reserve_{_object_id(e.entity_id)}",
                f"Orchestra: {e.device_name or e.name or e.entity_id} follows Conductor",
                BLUEPRINT_HOME_BATTERY_RESERVE,
                {"reserve_number": e.entity_id},
            ),
            f"Home battery: {e.device_name or e.name or e.entity_id}",
        )

    return automations, notes
