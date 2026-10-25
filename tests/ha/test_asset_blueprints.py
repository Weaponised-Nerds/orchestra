"""
Runs the bundled Orchestra asset blueprints inside a real Home Assistant core.

Needs `pytest-homeassistant-custom-component`, so it is skipped in the plain test run:
    pip install pytest-homeassistant-custom-component
    pytest tests/ha -p pytest_homeassistant_custom_component
"""

from __future__ import annotations

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.core import HomeAssistant, ServiceCall  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402

from custom_components.orchestra.hvac_drift import async_setup_hvac_drift  # noqa: E402
from custom_components.orchestra.blueprint_installer import (  # noqa: E402
    BLUEPRINT_EV_CHARGE_RATE,
    BLUEPRINT_HEAT_PUMP_TARGET,
    BLUEPRINT_HOME_BATTERY_RESERVE,
    BLUEPRINT_HVAC_SETPOINT_OFFSET,
    blueprint_path,
    install_blueprints,
)

CLIMATE = "climate.lounge"
BEDROOM = "climate.bedroom"  # an older automation that still names a baseline helper
OFFSET = "sensor.orchestra_hvac_setpoint_offset"
AMPS = "sensor.orchestra_ev_target_amps"
CHARGER = "number.wallbox_charging_current"
CHARGER_SWITCH = "switch.wallbox_charging_allowed"
HP_TARGET = "sensor.orchestra_heat_pump_target_temp"
WATER_HEATER = "water_heater.tank"
RESERVE = "sensor.orchestra_home_battery_min_reserve"
BATTERY_RESERVE = "number.powerwall_backup_reserve"


def _set_climate(
    hass: HomeAssistant, mode: str, temperature: float | None, entity_id: str = CLIMATE
) -> None:
    hass.states.async_set(
        entity_id,
        mode,
        {"temperature": temperature, "min_temp": 16, "max_temp": 30, "target_temp_step": 0.5},
    )


def _set_offset(hass: HomeAssistant, offset: float, dispatch_mode: str = "shed") -> None:
    hass.states.async_set(OFFSET, str(offset), {"dispatch_mode": dispatch_mode})


@pytest.fixture
async def setup(hass: HomeAssistant, tmp_path):
    """Real automation + input_number integrations, with mocked device services."""
    hass.config.config_dir = str(tmp_path)
    await hass.async_add_executor_job(install_blueprints, tmp_path)
    calls: list[ServiceCall] = []

    async def climate_set_temperature(call: ServiceCall) -> None:
        calls.append(call)
        entity_ids = call.data["entity_id"]
        for entity_id in [entity_ids] if isinstance(entity_ids, str) else entity_ids:
            state = hass.states.get(entity_id)
            hass.states.async_set(
                entity_id, state.state, {**state.attributes, "temperature": call.data["temperature"]}
            )

    async def record(call: ServiceCall) -> None:
        calls.append(call)

    hass.services.async_register("climate", "set_temperature", climate_set_temperature)
    hass.services.async_register("number", "set_value", record)
    hass.services.async_register("switch", "turn_on", record)
    hass.services.async_register("switch", "turn_off", record)
    hass.services.async_register("water_heater", "set_temperature", record)

    _set_climate(hass, "cool", 23.0)
    _set_climate(hass, "cool", 23.0, BEDROOM)
    _set_offset(hass, 0.0, "idle")
    hass.states.async_set(AMPS, "16", {"action": "NORMAL"})
    hass.states.async_set(CHARGER, "16", {"min": 6, "max": 32})
    hass.states.async_set(HP_TARGET, "60.0", {"action": "NORMAL", "run_now": False})
    hass.states.async_set(RESERVE, "20.0", {"action": "SELF_CONSUME", "charge_from_grid": False})
    hass.states.async_set(BATTERY_RESERVE, "20", {"min": 0, "max": 100})

    await async_setup_hvac_drift(hass)
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": [
                {
                    "id": "hvac",
                    "use_blueprint": {
                        "path": blueprint_path(BLUEPRINT_HVAC_SETPOINT_OFFSET),
                        "input": {"climate_entity": CLIMATE},
                    },
                },
                {
                    "id": "bedroom",
                    "use_blueprint": {
                        "path": blueprint_path(BLUEPRINT_HVAC_SETPOINT_OFFSET),
                        "input": {"climate_entity": BEDROOM, "baseline_helper": ""},
                    },
                },
                {
                    "id": "ev",
                    "use_blueprint": {
                        "path": blueprint_path(BLUEPRINT_EV_CHARGE_RATE),
                        "input": {
                            "charger_current": CHARGER,
                            "charger_switch": CHARGER_SWITCH,
                            "normal_amps": 32,
                        },
                    },
                },
                {
                    "id": "heat_pump",
                    "use_blueprint": {
                        "path": blueprint_path(BLUEPRINT_HEAT_PUMP_TARGET),
                        "input": {"water_heater": WATER_HEATER, "boost_operation_mode": "high_demand"},
                    },
                },
                {
                    "id": "battery",
                    "use_blueprint": {
                        "path": blueprint_path(BLUEPRINT_HOME_BATTERY_RESERVE),
                        "input": {"reserve_number": BATTERY_RESERVE},
                    },
                },
            ]
        },
    )
    await hass.async_block_till_done()
    assert len(hass.states.async_entity_ids("automation")) == 5
    return calls


def _setpoint(hass: HomeAssistant, entity_id: str = CLIMATE) -> float:
    return hass.states.get(entity_id).attributes["temperature"]


async def _offset(hass: HomeAssistant, offset: float, dispatch_mode: str = "shed") -> None:
    _set_offset(hass, offset, dispatch_mode if offset else "idle")
    await hass.async_block_till_done()


async def test_hvac_drifts_from_starting_setpoint_without_compounding(hass, setup) -> None:
    for offset, expected in ((1.5, 24.5), (2.0, 25.0), (1.5, 24.5), (0.0, 23.0)):
        await _offset(hass, offset)
        assert _setpoint(hass) == expected
        assert _setpoint(hass, BEDROOM) == expected


async def test_hvac_reconcile_is_idempotent(hass, setup) -> None:
    await _offset(hass, 1.5)
    for _ in range(3):
        hass.bus.async_fire("automation_reloaded")
        await hass.async_block_till_done()
    assert _setpoint(hass) == 24.5


async def test_hvac_picks_up_an_offset_already_active_when_saved(hass, tmp_path) -> None:
    """The reported bug: saving the automation mid-event must still drift the A/C."""
    hass.config.config_dir = str(tmp_path)
    await hass.async_add_executor_job(install_blueprints, tmp_path)
    await async_setup_hvac_drift(hass)

    async def climate_set_temperature(call: ServiceCall) -> None:
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, state.state, {**state.attributes, "temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_temperature", climate_set_temperature)
    _set_climate(hass, "cool", 23.0)
    _set_offset(hass, 1.5)  # Conductor has been sending 1.5 since before the automation existed
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": [{"id": "gf", "use_blueprint": {
            "path": blueprint_path(BLUEPRINT_HVAC_SETPOINT_OFFSET), "input": {"climate_entity": CLIMATE}}}]},
    )
    await hass.async_block_till_done()
    assert _setpoint(hass) == 23.0
    # What saving it in the editor fires once the new automation is loaded
    hass.bus.async_fire("automation_reloaded")
    await hass.async_block_till_done()
    assert _setpoint(hass) == 24.5


async def test_hvac_manual_change_becomes_new_start(hass, setup) -> None:
    await _offset(hass, 1.5)
    _set_climate(hass, "cool", 22.0)  # someone turns it down by hand
    await hass.async_block_till_done()
    await _offset(hass, 2.0)
    assert _setpoint(hass) == 22.5
    await _offset(hass, 0.0)
    assert _setpoint(hass) == 20.5


async def test_hvac_shed_drops_setpoint_when_heating(hass, setup) -> None:
    _set_climate(hass, "heat", 21.0)
    await hass.async_block_till_done()
    await _offset(hass, 1.5, "shed")
    assert _setpoint(hass) == 19.5
    await _offset(hass, 0.0)
    assert _setpoint(hass) == 21.0
    await _offset(hass, 1.5, "preheat")
    assert _setpoint(hass) == 22.5


async def test_hvac_leaves_unit_alone_when_off_and_clamps(hass, setup) -> None:
    _set_climate(hass, "off", 23.0)
    await hass.async_block_till_done()
    await _offset(hass, 1.5)
    assert _setpoint(hass) == 23.0

    # Switching it on mid-event starts the drift, clamped to the unit's max_temp
    _set_climate(hass, "cool", 29.5)
    await hass.async_block_till_done()
    assert _setpoint(hass) == 30.0
    await _offset(hass, 0.0)
    assert _setpoint(hass) == 29.5


async def test_hvac_ignores_unavailable_offset(hass, setup) -> None:
    await _offset(hass, 1.5)
    hass.states.async_set(OFFSET, "unavailable")
    await hass.async_block_till_done()
    await _offset(hass, 1.5)
    assert _setpoint(hass) == 24.5


async def test_ev_follows_target_amps_and_returns_to_normal(hass, setup) -> None:
    calls = setup
    hass.states.async_set(AMPS, "0", {"action": "Load Shed - Charging Paused"})
    await hass.async_block_till_done()
    assert calls[-1].service == "turn_off" and calls[-1].data["entity_id"] == [CHARGER_SWITCH]

    hass.states.async_set(AMPS, "10", {"action": "CHARGE_NOW"})
    await hass.async_block_till_done()
    assert [(c.service, c.data.get("value")) for c in calls[-2:]] == [("set_value", 10), ("turn_on", None)]

    # Same amps, different action: NORMAL goes back to the configured normal current
    hass.states.async_set(AMPS, "16", {"action": "CHARGE_NOW"})
    await hass.async_block_till_done()
    hass.states.async_set(AMPS, "16", {"action": "NORMAL"})
    await hass.async_block_till_done()
    assert calls[-2].service == "set_value" and calls[-2].data["value"] == 32


async def test_heat_pump_and_battery_follow_directives(hass, setup) -> None:
    calls = setup
    hass.states.async_set(HP_TARGET, "65.0", {"action": "RUN_NOW", "run_now": True})
    await hass.async_block_till_done()
    assert calls[-1].domain == "water_heater"
    assert calls[-1].data["temperature"] == 65.0
    assert calls[-1].data["operation_mode"] == "high_demand"

    hass.states.async_set(HP_TARGET, "60.0", {"action": "NORMAL", "run_now": False})
    await hass.async_block_till_done()
    assert calls[-1].data["temperature"] == 60
    assert "operation_mode" not in calls[-1].data

    hass.states.async_set(RESERVE, "100.0", {"action": "STORM_WATCH_HOLD", "charge_from_grid": True})
    await hass.async_block_till_done()
    assert calls[-1].domain == "number" and calls[-1].data["value"] == 100.0
