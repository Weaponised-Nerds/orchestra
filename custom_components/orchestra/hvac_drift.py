"""
HVAC setpoint drift held by the Orchestra integration (`orchestra.apply_hvac_offset`).

The HVAC blueprint calls this on every offset change, A/C mode change, automation save,
HA start and every few minutes. Orchestra remembers each A/C's starting setpoint and
the target it last set, so each call is idempotent: it sets starting setpoint + current
offset, never compounding, and restores the starting setpoint when the offset is 0.
A setpoint changed by hand mid-drift becomes the new starting point.

Pre-heat and pre-cool also switch on a unit that is off, and switch it back off once
the pre-conditioning ends. A unit switched off by hand mid-event is left off.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from .const import DOMAIN
except ImportError:
    from custom_components.orchestra.const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_APPLY_HVAC_OFFSET = "apply_hvac_offset"
STORAGE_KEY = f"{DOMAIN}.hvac_drift"
STORAGE_VERSION = 1
DEFAULT_OFFSET_SENSOR = "sensor.orchestra_hvac_setpoint_offset"
UNAVAILABLE_STATES = ("unavailable", "unknown")


def _as_float(value: Any, default: float | None = None) -> float | None:
    """Some integrations (AirTouch) report setpoint attributes as strings like '21'."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _round_clamp(value: float, step: float, min_temp: float, max_temp: float) -> float:
    return round(min(max(round(value / step) * step, min_temp), max_temp), 2)


# A unit still showing its old setpoint after we sent a new one gets it re-sent this
# many times; any other value means someone changed it by hand
MAX_RESENDS = 2


def plan_drift(
    held: dict[str, float] | None,
    *,
    current: float | None,
    hvac_mode: str,
    offset: float,
    dispatch_mode: str | None,
    step: float | None,
    min_temp: float,
    max_temp: float,
    invert_shed_when_heating: bool = True,
    skip_when_off: bool = True,
) -> tuple[float | None, dict[str, float] | None, str]:
    """
    Work out the setpoint to send (None = leave it), what to remember next
    (None = nothing held), and a short reason, from what was held for this A/C
    and its live state.
    """
    if current is None:
        return None, held, "no single setpoint (temperature attribute missing)"
    if hvac_mode in UNAVAILABLE_STATES:
        return None, held, f"unit is {hvac_mode}"

    if invert_shed_when_heating and hvac_mode == "heat" and dispatch_mode == "shed":
        offset = -abs(offset)
    unit_step = step if step and step > 0 else 0.5
    # Without a declared step the unit may round our target, so allow half a degree
    tolerance = unit_step / 2 + 0.01 if step else 0.51

    if held is not None and abs(current - held["target"]) > tolerance:
        untaken = (
            abs(current - held.get("before", float("nan"))) <= tolerance
            and held.get("resends", 0) < MAX_RESENDS
        )
        if not untaken:
            # Changed by hand mid-drift: their setpoint becomes the new starting point
            held = {"baseline": current - held["offset"], "target": current, "offset": held["offset"]}

    if abs(offset) < 0.05:
        if held is None:
            return None, None, "offset is 0 and no drift is held"
        target = _round_clamp(held["baseline"], unit_step, min_temp, max_temp)
        if abs(target - current) <= 0.01:
            return None, None, "offset is 0; already at starting setpoint"
        return target, None, f"offset is 0; restoring starting setpoint {held['baseline']}"

    if held is None:
        if skip_when_off and hvac_mode == "off":
            return None, None, "unit is off"
        held = {"baseline": current}
    target = _round_clamp(held["baseline"] + offset, unit_step, min_temp, max_temp)
    reason = f"starting setpoint {held['baseline']} + offset {offset}"
    next_held = {"baseline": held["baseline"], "target": target, "offset": offset}
    same_target = "target" in held and abs(held["target"] - target) < 0.01

    # Leave it when it's there, allowing for the unit rounding a target we already sent
    if abs(current - target) <= (tolerance if same_target else 0.01):
        return None, next_held, f"{reason}; already at {current}"
    if same_target and "before" in held:
        # The unit still shows its old setpoint: send ours again
        next_held.update(before=held["before"], resends=held.get("resends", 0) + 1)
        return target, next_held, f"{reason}; unit still at {current}, re-sending"
    next_held.update(before=current, resends=0)
    return target, next_held, reason


PRE_MODES = {"preheat": "heat", "precool": "cool"}


def _pick_mode(wanted: str, hvac_modes: list[str] | None) -> str | None:
    """The mode to switch an off unit on in, from the modes it supports."""
    if not hvac_modes:
        return wanted
    for mode in (wanted, "heat_cool", "auto"):
        if mode in hvac_modes:
            return mode
    return next((mode for mode in hvac_modes if mode != "off"), None)


def plan_mode(
    held: dict[str, Any] | None,
    *,
    hvac_mode: str,
    hvac_modes: list[str] | None,
    offset: float,
    dispatch_mode: str | None,
    turn_on_for_pre: bool = True,
) -> tuple[str | None, dict[str, Any] | None, str | None]:
    """
    Work out the HVAC mode to set (None = leave it), what to remember next
    (None = nothing held), and a reason when the mode changes. Only a unit Orchestra
    switched on itself is switched back.
    """
    if hvac_mode in UNAVAILABLE_STATES:
        return None, held, None
    pre_mode = PRE_MODES.get(dispatch_mode or "") if abs(offset) >= 0.05 else None

    if pre_mode:
        if held is None:
            if not (turn_on_for_pre and hvac_mode == "off"):
                return None, None, None
            mode = _pick_mode(pre_mode, hvac_modes)
            if mode is None:
                return None, None, None
            return mode, {"restore": "off", "set": mode}, f"switching on in {mode} for {dispatch_mode}"
        if "set" in held and hvac_mode == "off":
            # Still off once could be a unit slow to take the command; twice is by hand
            if held.get("resends", 0) < 1:
                resent = {**held, "resends": held.get("resends", 0) + 1}
                return held["set"], resent, f"still off, switching on in {held['set']} again"
            return None, {"off_by_hand": True}, None
        return None, held, None

    if held and "set" in held and hvac_mode == held["set"]:
        return held["restore"], None, f"{held['restore']} again now {dispatch_mode or 'idle'}"
    return None, None, None


class HvacDriftController:
    """Persists per-A/C drift state and applies `orchestra.apply_hvac_offset` calls."""

    def __init__(self, hass: Any) -> None:
        from homeassistant.helpers.storage import Store

        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._held: dict[str, dict[str, float]] = {}
        self._modes: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        data = await self._store.async_load() or {}
        if "drift" in data:
            self._held, self._modes = data["drift"], data.get("modes", {})
        else:  # saved before modes were held
            self._held = data

    async def async_apply(self, call: Any) -> dict[str, Any]:
        offset_sensor = call.data.get("offset_sensor", DEFAULT_OFFSET_SENSOR)
        offset_state = self.hass.states.get(offset_sensor)
        if offset_state is None or offset_state.state in UNAVAILABLE_STATES:
            _LOGGER.info("Orchestra HVAC drift skipped: %s is %s", offset_sensor,
                         offset_state.state if offset_state else "missing")
            return {"skipped": f"{offset_sensor} is {offset_state.state if offset_state else 'missing'}"}
        try:
            offset = float(offset_state.state)
        except ValueError:
            return {"skipped": f"{offset_sensor} is not a number ({offset_state.state})"}

        results: dict[str, Any] = {}
        async with self._lock:
            for entity_id in call.data["entity_id"]:
                results[entity_id] = await self._async_apply_one(
                    entity_id, offset, offset_state.attributes, call
                )
            self._store.async_delay_save(lambda: {"drift": self._held, "modes": self._modes}, 1)
        return results

    async def _async_apply_one(
        self, entity_id: str, offset: float, offset_attrs: Any, call: Any
    ) -> dict[str, Any]:
        climate = self.hass.states.get(entity_id)
        if climate is None:
            return {"reason": "entity not found"}
        attrs = climate.attributes
        current = _as_float(attrs.get("temperature"))
        dispatch_mode = offset_attrs.get("dispatch_mode")
        set_mode, mode_held, mode_reason = plan_mode(
            self._modes.get(entity_id),
            hvac_mode=climate.state,
            hvac_modes=attrs.get("hvac_modes"),
            offset=offset,
            dispatch_mode=dispatch_mode,
            turn_on_for_pre=call.data.get("turn_on_for_pre", True),
        )
        if mode_held is None:
            self._modes.pop(entity_id, None)
        else:
            self._modes[entity_id] = mode_held
        switching_on = set_mode is not None and climate.state == "off"
        if switching_on:
            await self._async_set_mode(entity_id, set_mode, call)
        target, held, reason = plan_drift(
            self._held.get(entity_id),
            current=current,
            hvac_mode=set_mode if switching_on else climate.state,
            offset=offset,
            dispatch_mode=dispatch_mode,
            step=_as_float(attrs.get("target_temp_step")),
            min_temp=_as_float(attrs.get("min_temp"), 7.0),
            max_temp=_as_float(attrs.get("max_temp"), 35.0),
            invert_shed_when_heating=call.data.get("invert_shed_when_heating", True),
            skip_when_off=call.data.get("skip_when_off", True),
        )
        if held is None:
            self._held.pop(entity_id, None)
        else:
            self._held[entity_id] = held
        result = {
            "hvac_mode": climate.state,
            "current_setpoint": current,
            "offset": offset,
            "dispatch_mode": dispatch_mode,
            "new_setpoint": target,
            "reason": f"{mode_reason}; {reason}" if mode_reason else reason,
        }
        if set_mode is not None:
            result["new_hvac_mode"] = set_mode
        (_LOGGER.info if target is not None else _LOGGER.debug)("Orchestra HVAC drift %s: %s", entity_id, result)
        if target is not None:
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": entity_id, "temperature": target},
                blocking=True,
                context=call.context,
            )
        if set_mode is not None and not switching_on:
            await self._async_set_mode(entity_id, set_mode, call)
        return result

    async def _async_set_mode(self, entity_id: str, hvac_mode: str, call: Any) -> None:
        await self.hass.services.async_call(
            "climate",
            "set_hvac_mode",
            {"entity_id": entity_id, "hvac_mode": hvac_mode},
            blocking=True,
            context=call.context,
        )


async def async_setup_hvac_drift(hass: Any) -> None:
    """Register `orchestra.apply_hvac_offset` once per HA instance."""
    services = getattr(hass, "services", None)
    if services is None or not hasattr(services, "has_service"):
        return
    if services.has_service(DOMAIN, SERVICE_APPLY_HVAC_OFFSET):
        return
    try:
        from .schema import vol
        from homeassistant.core import SupportsResponse
        from homeassistant.helpers import config_validation as cv
    except ImportError:
        return

    controller = HvacDriftController(hass)
    await controller.async_load()
    services.async_register(
        DOMAIN,
        SERVICE_APPLY_HVAC_OFFSET,
        controller.async_apply,
        schema=vol.Schema(
            {
                vol.Required("entity_id"): cv.entity_ids,
                vol.Optional("offset_sensor", default=DEFAULT_OFFSET_SENSOR): cv.entity_id,
                vol.Optional("invert_shed_when_heating", default=True): cv.boolean,
                vol.Optional("skip_when_off", default=True): cv.boolean,
                vol.Optional("turn_on_for_pre", default=True): cv.boolean,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
