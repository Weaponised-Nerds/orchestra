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

from custom_components.orchestra.heat_pump_control import async_setup_heat_pump_control  # noqa: E402
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
    await async_setup_heat_pump_control(hass)
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
    hass.states.async_set(WATER_HEATER, "eco", {"temperature": 55, "operation_mode": "eco"})
    hass.states.async_set(
        HP_TARGET, "65.0", {"action": "RUN_NOW", "run_now": True, "control_signal": "Energy Soak"}
    )
    await hass.async_block_till_done()
    assert calls[-1].domain == "water_heater"
    assert calls[-1].data["temperature"] == 65.0
    assert calls[-1].data["operation_mode"] == "high_demand"

    hass.states.async_set(HP_TARGET, "60.0", {"action": "NORMAL", "run_now": False, "control_signal": "Normal/Auto"})
    await hass.async_block_till_done()
    # Back to what it was doing before, not a fixed normal temperature
    assert calls[-1].data["temperature"] == 55
    assert calls[-1].data["operation_mode"] == "eco"

    hass.states.async_set(RESERVE, "100.0", {"action": "STORM_WATCH_HOLD", "charge_from_grid": True})
    await hass.async_block_till_done()
    assert calls[-1].domain == "number" and calls[-1].data["value"] == 100.0


async def test_hvac_action_reports_why(hass, setup) -> None:
    """The response explains each decision, so a trace shows why a setpoint did or didn't move."""
    _set_climate(hass, "heat", 21.0)
    _set_offset(hass, 1.5, "shed")
    await hass.async_block_till_done()
    response = await hass.services.async_call(
        "orchestra", "apply_hvac_offset", {"entity_id": CLIMATE}, blocking=True, return_response=True
    )
    assert response[CLIMATE]["offset"] == 1.5
    assert response[CLIMATE]["new_setpoint"] is None  # the automation already applied it
    assert "already at 19.5" in response[CLIMATE]["reason"]
    assert _setpoint(hass) == 19.5

    hass.states.async_set(OFFSET, "0.0", {"dispatch_mode": "idle"})
    response = await hass.services.async_call(
        "orchestra", "apply_hvac_offset", {"entity_id": BEDROOM}, blocking=True, return_response=True
    )
    assert response[BEDROOM]["reason"].startswith("offset is 0")


async def test_ev_switch_only_toggled_when_needed(hass, setup) -> None:
    calls = setup
    hass.states.async_set(CHARGER_SWITCH, "on")
    hass.states.async_set(AMPS, "10", {"action": "CHARGE_NOW"})
    await hass.async_block_till_done()
    assert [c.service for c in calls[-1:]] == ["set_value"]


async def test_hvac_drifts_a_unit_reporting_string_setpoints(hass, tmp_path) -> None:
    """AirTouch reports temperature, min/max and step as strings like '21'."""
    hass.config.config_dir = str(tmp_path)
    await hass.async_add_executor_job(install_blueprints, tmp_path)
    await async_setup_hvac_drift(hass)

    async def climate_set_temperature(call: ServiceCall) -> None:
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, state.state, {**state.attributes, "temperature": str(call.data["temperature"])})

    hass.services.async_register("climate", "set_temperature", climate_set_temperature)
    hass.states.async_set(
        CLIMATE, "heat", {"temperature": "21", "min_temp": "16", "max_temp": "30", "target_temp_step": "1"}
    )
    _set_offset(hass, 0.0, "idle")
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": [{"id": "gf", "use_blueprint": {
            "path": blueprint_path(BLUEPRINT_HVAC_SETPOINT_OFFSET), "input": {"climate_entity": CLIMATE}}}]},
    )
    await hass.async_block_till_done()
    await _offset(hass, 2.0)  # shed while heating drifts down
    assert float(_setpoint(hass)) == 19.0
    await _offset(hass, 0.0)
    assert float(_setpoint(hass)) == 21.0


async def test_hvac_drifts_every_selected_zone(hass, tmp_path) -> None:
    """Zoned A/Cs (AirTouch zone temperature control) are drifted through their zones."""
    hass.config.config_dir = str(tmp_path)
    await hass.async_add_executor_job(install_blueprints, tmp_path)
    await async_setup_hvac_drift(hass)

    async def climate_set_temperature(call: ServiceCall) -> None:
        state = hass.states.get(call.data["entity_id"])
        hass.states.async_set(state.entity_id, state.state, {**state.attributes, "temperature": call.data["temperature"]})

    hass.services.async_register("climate", "set_temperature", climate_set_temperature)
    _set_climate(hass, "heat", 21.0)
    _set_climate(hass, "heat", 22.0, BEDROOM)
    _set_offset(hass, 0.0, "idle")
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": [{"id": "zones", "use_blueprint": {
            "path": blueprint_path(BLUEPRINT_HVAC_SETPOINT_OFFSET), "input": {"climate_entity": [CLIMATE, BEDROOM]}}}]},
    )
    await hass.async_block_till_done()
    await _offset(hass, 1.5)
    assert (_setpoint(hass), _setpoint(hass, BEDROOM)) == (19.5, 20.5)
    await _offset(hass, 0.0)
    assert (_setpoint(hass), _setpoint(hass, BEDROOM)) == (21.0, 22.0)


async def test_preheat_switches_an_off_unit_on_and_back(hass, tmp_path) -> None:
    hass.config.config_dir = str(tmp_path)
    await hass.async_add_executor_job(install_blueprints, tmp_path)
    await async_setup_hvac_drift(hass)

    async def set_temperature(call: ServiceCall) -> None:
        state = hass.states.get(CLIMATE)
        hass.states.async_set(CLIMATE, state.state, {**state.attributes, "temperature": call.data["temperature"]})

    async def set_hvac_mode(call: ServiceCall) -> None:
        hass.states.async_set(CLIMATE, call.data["hvac_mode"], hass.states.get(CLIMATE).attributes)

    hass.services.async_register("climate", "set_temperature", set_temperature)
    hass.services.async_register("climate", "set_hvac_mode", set_hvac_mode)
    hass.states.async_set(
        CLIMATE, "off",
        {"temperature": 21.0, "min_temp": 16, "max_temp": 30, "target_temp_step": 1,
         "hvac_modes": ["off", "heat", "cool"]},
    )
    _set_offset(hass, 0.0, "idle")
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": [{"id": "gf", "use_blueprint": {
            "path": blueprint_path(BLUEPRINT_HVAC_SETPOINT_OFFSET), "input": {"climate_entity": CLIMATE}}}]},
    )
    await hass.async_block_till_done()
    await _offset(hass, 2.0, "preheat")
    assert hass.states.get(CLIMATE).state == "heat"
    assert _setpoint(hass) == 23.0
    await _offset(hass, 0.0)
    assert hass.states.get(CLIMATE).state == "off"
    assert _setpoint(hass) == 21.0


HP_POWER = "switch.heat_pump_power"


async def _heat_pump_setup(hass: HomeAssistant, tmp_path, inputs: dict) -> None:
    hass.config.config_dir = str(tmp_path)
    await hass.async_add_executor_job(install_blueprints, tmp_path)
    await async_setup_heat_pump_control(hass)

    async def wh_set_temperature(call: ServiceCall) -> None:
        state = hass.states.get(WATER_HEATER)
        attrs = {**state.attributes, "temperature": call.data["temperature"]}
        mode = call.data.get("operation_mode", state.state)
        attrs["operation_mode"] = mode
        hass.states.async_set(WATER_HEATER, mode, attrs)

    async def wh_turn(call: ServiceCall) -> None:
        state = hass.states.get(WATER_HEATER)
        on = call.service == "turn_on"
        mode = "eco" if on else "off"
        hass.states.async_set(WATER_HEATER, mode, {**state.attributes, "operation_mode": mode})

    async def switch_turn(call: ServiceCall) -> None:
        for entity_id in call.data["entity_id"] if isinstance(call.data["entity_id"], list) else [call.data["entity_id"]]:
            hass.states.async_set(entity_id, "on" if call.service == "turn_on" else "off")

    hass.services.async_register("water_heater", "set_temperature", wh_set_temperature)
    hass.services.async_register("water_heater", "turn_on", wh_turn)
    hass.services.async_register("water_heater", "turn_off", wh_turn)
    hass.services.async_register("homeassistant", "turn_on", switch_turn)
    hass.services.async_register("homeassistant", "turn_off", switch_turn)
    hass.states.async_set(HP_TARGET, "60.0", {"action": "NORMAL", "run_now": False, "control_signal": "Normal/Auto"})
    assert await async_setup_component(
        hass,
        "automation",
        {"automation": [{"id": "hp", "use_blueprint": {
            "path": blueprint_path(BLUEPRINT_HEAT_PUMP_TARGET), "input": inputs}}]},
    )
    await hass.async_block_till_done()


async def _hp_signal(hass: HomeAssistant, signal: str, target: float, run_now: bool = False) -> None:
    action = {"Energy Soak": "RUN_NOW", "Load Shed": "THERMAL_COAST"}.get(signal, "NORMAL")
    hass.states.async_set(HP_TARGET, str(target), {"action": action, "run_now": run_now, "control_signal": signal})
    await hass.async_block_till_done()


async def test_heat_pump_power_switch_soaks_sheds_and_restores(hass, tmp_path) -> None:
    hass.states.async_set(WATER_HEATER, "eco", {"temperature": 55, "operation_mode": "eco"})
    hass.states.async_set(HP_POWER, "off")
    await _heat_pump_setup(hass, tmp_path, {"water_heater": WATER_HEATER, "power_switch": HP_POWER})

    await _hp_signal(hass, "Energy Soak", 65.0, run_now=True)
    assert hass.states.get(HP_POWER).state == "on"
    assert hass.states.get(WATER_HEATER).attributes["temperature"] == 65.0
    # Any setpoint change during the soak keeps it on at the new target
    hass.states.async_set(HP_POWER, "off")
    await _hp_signal(hass, "Energy Soak", 62.0, run_now=True)
    assert hass.states.get(HP_POWER).state == "on"
    assert hass.states.get(WATER_HEATER).attributes["temperature"] == 62.0

    await _hp_signal(hass, "Load Shed", 50.0)
    assert hass.states.get(HP_POWER).state == "off"

    await _hp_signal(hass, "Normal/Auto", 60.0)
    assert hass.states.get(HP_POWER).state == "off"  # it was off before the soak
    assert hass.states.get(WATER_HEATER).attributes["temperature"] == 55
    assert hass.states.get(WATER_HEATER).state == "eco"


async def test_heat_pump_switch_on_before_shed_comes_back_on(hass, tmp_path) -> None:
    hass.states.async_set(HP_POWER, "on")
    await _heat_pump_setup(hass, tmp_path, {"power_switch": HP_POWER})
    await _hp_signal(hass, "Load Shed", 50.0)
    assert hass.states.get(HP_POWER).state == "off"
    await _hp_signal(hass, "Normal/Auto", 60.0)
    assert hass.states.get(HP_POWER).state == "on"


async def test_heat_pump_water_heater_alone_is_switched_and_restored(hass, tmp_path) -> None:
    hass.states.async_set(WATER_HEATER, "off", {"temperature": 52, "operation_mode": "off"})
    await _heat_pump_setup(hass, tmp_path, {"water_heater": WATER_HEATER})
    await _hp_signal(hass, "Energy Soak", 65.0, run_now=True)
    assert hass.states.get(WATER_HEATER).state != "off"
    assert hass.states.get(WATER_HEATER).attributes["temperature"] == 65.0
    await _hp_signal(hass, "Normal/Auto", 60.0)
    assert hass.states.get(WATER_HEATER).state == "off"
    assert hass.states.get(WATER_HEATER).attributes["temperature"] == 52


async def test_localtuya_pool_heat_pump_gets_its_temperatures(hass, tmp_path) -> None:
    """localtuya water heaters sit at 'unknown' with no operation_mode, yet take temperatures."""
    hass.states.async_set(
        WATER_HEATER, "unknown",
        {"temperature": 26, "min_temp": 20, "max_temp": 30, "operation_mode": None,
         "operation_list": ["boost", "silence", "Off"]},
    )
    hass.states.async_set(HP_POWER, "off")
    await _heat_pump_setup(hass, tmp_path, {
        "water_heater": WATER_HEATER, "power_switch": HP_POWER,
        "boost_operation_mode": "Boost", "normal_operation_mode": "Silence",
    })

    await _hp_signal(hass, "Energy Soak", 65.0, run_now=True)
    assert hass.states.get(HP_POWER).state == "on"
    # Clamped to the pool heat pump's own maximum, in its own spelling of the mode
    assert hass.states.get(WATER_HEATER).attributes["temperature"] == 30
    assert hass.states.get(WATER_HEATER).attributes["operation_mode"] == "boost"

    await _hp_signal(hass, "Load Shed", 50.0)
    assert hass.states.get(HP_POWER).state == "off"

    await _hp_signal(hass, "Normal/Auto", 60.0)
    assert hass.states.get(HP_POWER).state == "off"
    assert hass.states.get(WATER_HEATER).attributes["temperature"] == 26
    assert hass.states.get(WATER_HEATER).attributes["operation_mode"] == "silence"
