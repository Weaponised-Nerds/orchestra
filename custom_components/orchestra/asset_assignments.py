"""
Which of the home's equipment follows Conductor: the Orchestra blueprint automations.

The setup agent prefills these on first boot (`setup_planner.plan_asset_automations`).
This module lets the setup assistant read them back and assign equipment the agent
missed or got wrong, for example a heat pump or battery set up after the first day.
Assignments live in automations.yaml as ordinary blueprint automations, so the person can
still see and edit them under Settings → Automations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .blueprint_installer import (
    BLUEPRINT_EV_CHARGE_RATE,
    BLUEPRINT_HEAT_PUMP_TARGET,
    BLUEPRINT_HOME_BATTERY_RESERVE,
    BLUEPRINT_HVAC_SETPOINT_OFFSET,
    blueprint_path,
)


@dataclass(frozen=True)
class AssetKind:
    """One Orchestra blueprint, and the inputs the assistant may fill."""

    blueprint: str
    title: str
    main_input: str
    main_domains: tuple[str, ...]
    entity_inputs: dict[str, tuple[str, ...]] = field(default_factory=dict)
    value_inputs: tuple[str, ...] = ()
    multiple: bool = False
    help: str = ""


ASSET_KINDS: dict[str, AssetKind] = {
    "air_conditioning": AssetKind(
        BLUEPRINT_HVAC_SETPOINT_OFFSET,
        "Air conditioning",
        "climate_entity",
        ("climate",),
        value_inputs=("skip_when_off", "turn_on_for_pre", "invert_shed_when_heating"),
        multiple=True,
        help=(
            "One automation for the whole home: list every climate that should drift. On "
            "zoned systems (AirTouch and similar) list the zone climates, not the main unit."
        ),
    ),
    "heat_pump": AssetKind(
        BLUEPRINT_HEAT_PUMP_TARGET,
        "Hot water / heat pump",
        "water_heater",
        ("water_heater", "climate", "switch"),
        entity_inputs={"power_switch": ("switch", "input_boolean", "climate", "water_heater")},
        value_inputs=(
            "boost_temperature",
            "boost_operation_mode",
            "coast_temperature",
            "coast_operation_mode",
            "normal_temperature",
            "normal_operation_mode",
        ),
        help="One per heat pump or hot water system. power_switch is its separate on/off switch, if it has one.",
    ),
    "ev_charger": AssetKind(
        BLUEPRINT_EV_CHARGE_RATE,
        "EV charger",
        "charger_current",
        ("number",),
        entity_inputs={"charger_switch": ("switch",)},
        value_inputs=("normal_amps",),
        help="One per charger: its charging current (amps) number, and its charge on/off switch if it has one.",
    ),
    "home_battery": AssetKind(
        BLUEPRINT_HOME_BATTERY_RESERVE,
        "Home battery",
        "reserve_number",
        ("number",),
        entity_inputs={"grid_charge_switch": ("switch",)},
        value_inputs=("normal_reserve",),
        help="One per battery: its backup reserve (%) number, and its grid charging switch if it has one.",
    ),
}
_KIND_BY_BLUEPRINT = {blueprint_path(k.blueprint): name for name, k in ASSET_KINDS.items()}


class AssignmentError(Exception):
    """Why an assignment was refused, in words the assistant can pass on."""


def automations_path(hass: Any) -> Path:
    return Path(hass.config.path("automations.yaml"))


def describe_assignments(automations: list[Any]) -> list[dict[str, Any]]:
    """The Orchestra blueprint automations in automations.yaml."""
    out = []
    for automation in automations:
        if not isinstance(automation, dict):
            continue
        use = automation.get("use_blueprint") or {}
        kind = _KIND_BY_BLUEPRINT.get(str(use.get("path", "")))
        if kind:
            out.append(
                {
                    "kind": kind,
                    "automation_id": automation.get("id"),
                    "name": automation.get("alias"),
                    "inputs": dict(use.get("input") or {}),
                }
            )
    return out


def apply_assignment(
    automations: list[Any],
    kind_name: str,
    entities: list[str],
    settings: dict[str, Any],
    domain_of: Any,
) -> tuple[list[Any], dict[str, Any]]:
    """
    Add or update the automation for one piece of equipment.

    `domain_of(entity_id)` returns the entity's domain, or None when it doesn't exist.
    Returns the new automation list and the assignment as described to the assistant.
    """
    kind = ASSET_KINDS.get(kind_name)
    if kind is None:
        raise AssignmentError(f"Unknown kind '{kind_name}'. Use one of: {', '.join(ASSET_KINDS)}.")
    if not entities or (len(entities) > 1 and not kind.multiple):
        raise AssignmentError(
            f"{kind.title} takes exactly one {kind.main_input} entity." if not kind.multiple
            else "List at least one climate entity."
        )
    for entity_id in entities:
        _check_entity(entity_id, kind.main_domains, domain_of)

    inputs: dict[str, Any] = {}
    for key, value in settings.items():
        if key in kind.entity_inputs:
            if value:
                _check_entity(str(value), kind.entity_inputs[key], domain_of)
            inputs[key] = str(value or "")
        elif key in kind.value_inputs:
            inputs[key] = value
        else:
            allowed = list(kind.entity_inputs) + list(kind.value_inputs)
            raise AssignmentError(
                f"{kind.title} has no setting '{key}'." + (f" It takes: {', '.join(allowed)}." if allowed else "")
            )

    if kind.multiple:
        automation_id = "orchestra_hvac_offset"
        inputs[kind.main_input] = sorted(set(entities))
    else:
        automation_id = f"orchestra_{_ID_PREFIX[kind_name]}_{entities[0].split('.', 1)[1]}"
        inputs[kind.main_input] = entities[0]

    path = blueprint_path(kind.blueprint)
    updated = list(automations)
    for index, automation in enumerate(updated):
        if not isinstance(automation, dict):
            continue
        use = automation.get("use_blueprint") or {}
        if automation.get("id") == automation_id or (
            use.get("path") == path and (use.get("input") or {}).get(kind.main_input) == inputs[kind.main_input]
        ):
            merged = {**(use.get("input") or {}), **inputs}
            updated[index] = {**automation, "use_blueprint": {"path": path, "input": merged}}
            return updated, {"kind": kind_name, "automation_id": automation.get("id"), "inputs": merged, "updated": True}

    names = ", ".join(entities)
    updated.append(
        {
            "id": automation_id,
            "alias": f"Orchestra: {kind.title.lower()} follows Conductor ({names})"
            if not kind.multiple
            else "Orchestra: air conditioning follows Conductor",
            "description": "Assigned by the Orchestra setup assistant.",
            "use_blueprint": {"path": path, "input": inputs},
        }
    )
    return updated, {"kind": kind_name, "automation_id": automation_id, "inputs": inputs, "updated": False}


def remove_assignment(automations: list[Any], automation_id: str) -> list[Any]:
    kept = [
        a
        for a in automations
        if not (isinstance(a, dict) and a.get("id") == automation_id and describe_assignments([a]))
    ]
    if len(kept) == len(automations):
        raise AssignmentError("There is no Orchestra blueprint automation with that id.")
    return kept


_ID_PREFIX = {"heat_pump": "heat_pump", "ev_charger": "ev_rate", "home_battery": "battery_reserve"}


def _check_entity(entity_id: str, domains: tuple[str, ...], domain_of: Any) -> None:
    domain = domain_of(entity_id)
    if domain is None:
        raise AssignmentError(f"There is no entity {entity_id}. Use orchestra_list_entities to find it.")
    if domain not in domains:
        raise AssignmentError(f"{entity_id} is a {domain}; this needs a {' or '.join(domains)} entity.")
