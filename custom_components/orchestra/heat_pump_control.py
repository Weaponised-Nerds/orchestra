"""
Heat pump control held by the Orchestra integration (`orchestra.apply_heat_pump`).

The heat pump blueprint calls this whenever the heat pump directive changes. Energy
Soak switches the heat pump on (and, for a water heater, sets the soak target), Load
Shed switches it off, and back on Normal it returns to exactly what it was doing
before: on or off, and its previous temperature and operation mode. Orchestra
remembers that starting state, so repeated calls during one event never overwrite it.

Works with water heaters, climate entities and plain switches (e.g. a pool heat pump).
When the heat pump has a separate power switch, give it as `power_switch`: the switch
is turned on and off, and the water heater or climate only has its setpoint set and
restored.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from .const import DOMAIN, SIGNAL_ENERGY_SOAK, SIGNAL_LOAD_SHED
    from .hvac_drift import _as_float
except ImportError:
    from custom_components.orchestra.const import DOMAIN, SIGNAL_ENERGY_SOAK, SIGNAL_LOAD_SHED
    from custom_components.orchestra.hvac_drift import _as_float

_LOGGER = logging.getLogger(__name__)

SERVICE_APPLY_HEAT_PUMP = "apply_heat_pump"
STORAGE_KEY = f"{DOMAIN}.heat_pump"
STORAGE_VERSION = 1
DEFAULT_TARGET_SENSOR = "sensor.orchestra_heat_pump_target_temp"
UNAVAILABLE_STATES = ("unavailable", "unknown")


def directive_phase(attrs: dict[str, Any]) -> str:
    """'soak', 'shed' or 'normal', from the heat pump target sensor's attributes."""
    if attrs.get("opted_out") or attrs.get("targeted") is False:
        return "normal"
    if attrs.get("run_now") or attrs.get("control_signal") == SIGNAL_ENERGY_SOAK:
        return "soak"
    if attrs.get("control_signal") == SIGNAL_LOAD_SHED:
        return "shed"
    return "normal"


def snapshot(domain: str, state: str, attrs: dict[str, Any]) -> dict[str, Any]:
    """What the heat pump is doing now, enough to put it back later."""
    held: dict[str, Any] = {"on": state not in ("off", "idle") if domain != "switch" else state == "on"}
    if domain == "water_heater":
        held["temperature"] = _as_float(attrs.get("temperature"))
        held["operation_mode"] = attrs.get("operation_mode") or (state if state != "off" else None)
    elif domain == "climate":
        held["hvac_mode"] = state
        held["temperature"] = _as_float(attrs.get("temperature"))
    return held


def plan_heat_pump(
    held: dict[str, Any] | None, *, phase: str, now: dict[str, Any], setpoint_only: bool = False
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, str]:
    """
    Work out the state to put the heat pump in (None = leave it), what to remember
    next (None = nothing held), and a short reason.
    """
    if phase == "normal":
        if held is None:
            return None, None, "normal; nothing to restore"
        return held, None, "normal; restoring what it was doing before"
    if held is None:
        held = now
    want_on = phase == "soak"
    if phase == "shed" and (setpoint_only or not now["on"]):
        return None, held, "load shed; left to the power switch" if setpoint_only else "load shed; already off"
    return {"on": want_on}, held, "energy soak; switching on" if want_on else "load shed; switching off"


class HeatPumpController:
    """Persists each heat pump's pre-event state and applies `orchestra.apply_heat_pump`."""

    def __init__(self, hass: Any) -> None:
        from homeassistant.helpers.storage import Store

        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._held: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        self._held = await self._store.async_load() or {}

    async def async_apply(self, call: Any) -> dict[str, Any]:
        sensor = call.data.get("target_sensor", DEFAULT_TARGET_SENSOR)
        target_state = self.hass.states.get(sensor)
        if target_state is None or target_state.state in UNAVAILABLE_STATES:
            return {"skipped": f"{sensor} is {target_state.state if target_state else 'missing'}"}
        phase = directive_phase(target_state.attributes)
        soak_temperature = _as_float(target_state.state)

        switches = call.data.get("power_switch", [])
        results: dict[str, Any] = {}
        async with self._lock:
            for entity_id in call.data.get("entity_id", []):
                results[entity_id] = await self._async_apply_one(
                    entity_id, phase, soak_temperature, call, setpoint_only=bool(switches)
                )
            for entity_id in switches:
                results[entity_id] = await self._async_apply_one(entity_id, phase, None, call, power_only=True)
            self._store.async_delay_save(lambda: self._held, 1)
        return results

    async def _async_apply_one(
        self,
        entity_id: str,
        phase: str,
        soak_temperature: float | None,
        call: Any,
        *,
        setpoint_only: bool = False,
        power_only: bool = False,
    ) -> dict[str, Any]:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in UNAVAILABLE_STATES:
            return {"reason": f"{entity_id} is {state.state if state else 'missing'}"}
        # A power switch is only ever switched, whatever its domain
        domain = "switch" if power_only else entity_id.split(".", 1)[0]
        now = snapshot(domain, state.state, state.attributes)
        apply, held, reason = plan_heat_pump(
            self._held.get(entity_id), phase=phase, now=now, setpoint_only=setpoint_only
        )
        if held is None:
            self._held.pop(entity_id, None)
        else:
            self._held[entity_id] = held
        result = {"phase": phase, "was_on": now["on"], "reason": reason}
        if apply is None:
            return result
        _LOGGER.info("Orchestra heat pump %s: %s", entity_id, result)
        try:
            if phase == "soak":
                await self._async_soak(entity_id, domain, soak_temperature, call, setpoint_only)
            elif phase == "shed":
                await self._async_call(domain if domain != "switch" else "homeassistant", "turn_off", entity_id, {}, call)
            else:
                await self._async_restore(entity_id, domain, apply, call, setpoint_only)
        except Exception as err:  # noqa: BLE001 - one unit failing mustn't stop the rest
            _LOGGER.warning("Orchestra heat pump %s: %s failed: %s", entity_id, reason, err)
            result["error"] = str(err)
        return result

    async def _async_soak(
        self, entity_id: str, domain: str, temperature: float | None, call: Any, setpoint_only: bool
    ) -> None:
        if domain == "water_heater":
            data: dict[str, Any] = {}
            if temperature is not None:
                data["temperature"] = temperature
            boost = call.data.get("boost_operation_mode")
            if boost:
                data["operation_mode"] = boost
            elif not setpoint_only and self.hass.states.get(entity_id).state == "off":
                await self._async_call("water_heater", "turn_on", entity_id, {}, call)
            if data:
                await self._async_call("water_heater", "set_temperature", entity_id, data, call)
        elif domain == "climate":
            if not setpoint_only and self.hass.states.get(entity_id).state == "off":
                await self._async_call("climate", "turn_on", entity_id, {}, call)
        else:
            await self._async_call("homeassistant", "turn_on", entity_id, {}, call)

    async def _async_restore(
        self, entity_id: str, domain: str, held: dict[str, Any], call: Any, setpoint_only: bool
    ) -> None:
        if domain == "water_heater":
            data = {k: held[k] for k in ("temperature", "operation_mode") if held.get(k) is not None}
            if setpoint_only:
                if data:
                    service = "set_temperature" if "temperature" in data else "set_operation_mode"
                    await self._async_call("water_heater", service, entity_id, data, call)
            elif held["on"]:
                if data.get("operation_mode") is None and self.hass.states.get(entity_id).state == "off":
                    await self._async_call("water_heater", "turn_on", entity_id, {}, call)
                if "temperature" in data:
                    await self._async_call("water_heater", "set_temperature", entity_id, data, call)
                elif "operation_mode" in data:
                    await self._async_call("water_heater", "set_operation_mode", entity_id, data, call)
            else:
                if "temperature" in data:
                    await self._async_call(
                        "water_heater", "set_temperature", entity_id, {"temperature": data["temperature"]}, call
                    )
                await self._async_call("water_heater", "turn_off", entity_id, {}, call)
        elif domain == "climate":
            if not setpoint_only:
                await self._async_call("climate", "set_hvac_mode", entity_id, {"hvac_mode": held["hvac_mode"]}, call)
            if (held["on"] or setpoint_only) and held.get("temperature") is not None:
                await self._async_call("climate", "set_temperature", entity_id, {"temperature": held["temperature"]}, call)
        else:
            await self._async_call("homeassistant", "turn_on" if held["on"] else "turn_off", entity_id, {}, call)

    async def _async_call(
        self, domain: str, service: str, entity_id: str, data: dict[str, Any], call: Any
    ) -> None:
        await self.hass.services.async_call(
            domain, service, {"entity_id": entity_id, **data}, blocking=True, context=call.context
        )


async def async_setup_heat_pump_control(hass: Any) -> None:
    """Register `orchestra.apply_heat_pump` once per HA instance."""
    services = getattr(hass, "services", None)
    if services is None or not hasattr(services, "has_service"):
        return
    if services.has_service(DOMAIN, SERVICE_APPLY_HEAT_PUMP):
        return
    try:
        import voluptuous as vol
        from homeassistant.core import SupportsResponse
        from homeassistant.helpers import config_validation as cv
    except ImportError:
        return

    controller = HeatPumpController(hass)
    await controller.async_load()
    services.async_register(
        DOMAIN,
        SERVICE_APPLY_HEAT_PUMP,
        controller.async_apply,
        schema=vol.Schema(
            {
                vol.Optional("entity_id", default=[]): cv.entity_ids,
                vol.Optional("power_switch", default=[]): cv.entity_ids,
                vol.Optional("target_sensor", default=DEFAULT_TARGET_SENSOR): cv.entity_id,
                vol.Optional("boost_operation_mode", default=""): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
