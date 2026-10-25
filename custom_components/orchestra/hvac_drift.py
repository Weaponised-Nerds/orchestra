"""
HVAC setpoint drift held by the Orchestra integration (`orchestra.apply_hvac_offset`).

The HVAC blueprint calls this on every offset change, A/C mode change, automation save,
HA start and every few minutes. Orchestra remembers each A/C's starting setpoint and
the target it last set, so each call is idempotent: it sets starting setpoint + current
offset, never compounding, and restores the starting setpoint when the offset is 0.
A setpoint changed by hand mid-drift becomes the new starting point.
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


def _round_clamp(value: float, step: float, min_temp: float, max_temp: float) -> float:
    return round(min(max(round(value / step) * step, min_temp), max_temp), 2)


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
) -> tuple[float | None, dict[str, float] | None]:
    """
    Work out the setpoint to send (None = leave it) and what to remember next
    (None = nothing held) from what was held for this A/C and its live state.
    """
    if current is None or hvac_mode in UNAVAILABLE_STATES:
        return None, held

    if invert_shed_when_heating and hvac_mode == "heat" and dispatch_mode == "shed":
        offset = -abs(offset)
    unit_step = step if step and step > 0 else 0.5
    # Without a declared step the unit may round our target, so allow half a degree
    tolerance = unit_step / 2 + 0.01 if step else 0.51

    if held is not None and abs(current - held["target"]) > tolerance:
        # Changed by hand mid-drift: their setpoint becomes the new starting point
        held = {**held, "baseline": current - held["offset"], "target": current}

    if abs(offset) < 0.05:
        if held is None:
            return None, None
        target = _round_clamp(held["baseline"], unit_step, min_temp, max_temp)
        return (target if abs(target - current) > 0.01 else None), None

    if held is None:
        if skip_when_off and hvac_mode == "off":
            return None, None
        held = {"baseline": current}
    target = _round_clamp(held["baseline"] + offset, unit_step, min_temp, max_temp)
    next_held = {"baseline": held["baseline"], "target": target, "offset": offset}
    # Re-send only for a new target, not when the unit merely rounded the last one
    unchanged = "target" in held and abs(held["target"] - target) < 0.01 and abs(current - target) <= tolerance
    return (None if unchanged or abs(target - current) < 0.01 else target), next_held


class HvacDriftController:
    """Persists per-A/C drift state and applies `orchestra.apply_hvac_offset` calls."""

    def __init__(self, hass: Any) -> None:
        from homeassistant.helpers.storage import Store

        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_KEY)
        self._held: dict[str, dict[str, float]] = {}
        self._lock = asyncio.Lock()

    async def async_load(self) -> None:
        self._held = await self._store.async_load() or {}

    async def async_apply(self, call: Any) -> None:
        offset_sensor = call.data.get("offset_sensor", DEFAULT_OFFSET_SENSOR)
        offset_state = self.hass.states.get(offset_sensor)
        if offset_state is None or offset_state.state in UNAVAILABLE_STATES:
            return
        try:
            offset = float(offset_state.state)
        except ValueError:
            return

        async with self._lock:
            for entity_id in call.data["entity_id"]:
                await self._async_apply_one(entity_id, offset, offset_state.attributes, call)
            self._store.async_delay_save(lambda: self._held, 1)

    async def _async_apply_one(
        self, entity_id: str, offset: float, offset_attrs: Any, call: Any
    ) -> None:
        climate = self.hass.states.get(entity_id)
        if climate is None:
            return
        attrs = climate.attributes
        current = attrs.get("temperature")
        target, held = plan_drift(
            self._held.get(entity_id),
            current=float(current) if isinstance(current, (int, float)) else None,
            hvac_mode=climate.state,
            offset=offset,
            dispatch_mode=offset_attrs.get("dispatch_mode"),
            step=attrs.get("target_temp_step"),
            min_temp=float(attrs.get("min_temp", 7)),
            max_temp=float(attrs.get("max_temp", 35)),
            invert_shed_when_heating=call.data.get("invert_shed_when_heating", True),
            skip_when_off=call.data.get("skip_when_off", True),
        )
        if held is None:
            self._held.pop(entity_id, None)
        else:
            self._held[entity_id] = held
        if target is not None:
            _LOGGER.debug("Orchestra HVAC drift: %s -> %s °C (offset %s)", entity_id, target, offset)
            await self.hass.services.async_call(
                "climate",
                "set_temperature",
                {"entity_id": entity_id, "temperature": target},
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
        import voluptuous as vol
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
            }
        ),
    )
