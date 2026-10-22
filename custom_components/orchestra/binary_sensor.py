"""Platform for Orchestra binary sensor entities (Granular Asset Classes)."""

from __future__ import annotations

from typing import Any

from custom_components.orchestra.const import (
    BINARY_SENSOR_AC_PRECOOL,
    BINARY_SENSOR_AC_PREHEAT,
    BINARY_SENSOR_EV_CHARGE_NOW,
    BINARY_SENSOR_HEAT_PUMP_RUN_NOW,
    BINARY_SENSOR_HOME_BATTERY_STORM_WATCH,
    DOMAIN,
)
from custom_components.orchestra.coordinator import OrchestraDataUpdateCoordinator
from custom_components.orchestra.entity import Entity, OrchestraBaseEntity

try:
    from homeassistant.components.binary_sensor import (
        BinarySensorDeviceClass,
        BinarySensorEntity,
    )
except ImportError:
    class BinarySensorEntity(Entity):  # type: ignore
        """Binary sensor entity fallback."""
        pass

    class BinarySensorDeviceClass:  # type: ignore
        BATTERY_CHARGING = "battery_charging"
        COLD = "cold"
        HEAT = "heat"
        POWER = "power"
        RUNNING = "running"


async def async_setup_entry(
    hass: Any,
    entry: Any,
    async_add_entities: Any,
) -> None:
    """Set up Orchestra binary sensors from a config entry."""
    coordinator: OrchestraDataUpdateCoordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities(
        [
            OrchestraEVChargeNowBinarySensor(coordinator),
            OrchestraHeatPumpRunNowBinarySensor(coordinator),
            OrchestraACPrecoolBinarySensor(coordinator),
            OrchestraACPreheatBinarySensor(coordinator),
            OrchestraHomeBatteryStormWatchBinarySensor(coordinator),
        ]
    )


class OrchestraEVChargeNowBinarySensor(OrchestraBaseEntity, BinarySensorEntity):
    """Signals EV to initiate charging immediately (solar soaking / negative wholesale price)."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, BINARY_SENSOR_EV_CHARGE_NOW)
        self._attr_name = "EV Charge Now"
        self._attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
        self._attr_icon = "mdi:ev-station"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.dispatch.ev_charge_now)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            "target_amps": disp.ev_target_amps,
            "action": disp.ev_action,
            "reason": disp.ev_reason,
            "dispatch_id": disp.dispatch_id,
            "opted_out": disp.opted_out,
        }


class OrchestraHeatPumpRunNowBinarySensor(OrchestraBaseEntity, BinarySensorEntity):
    """Signals Heat Pump Hot Water heater to boost to 65°C immediately."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, BINARY_SENSOR_HEAT_PUMP_RUN_NOW)
        self._attr_name = "Heat Pump Run Now"
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:water-boiler"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.dispatch.heat_pump_run_now)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            "target_temp_c": disp.heat_pump_target_temp_c,
            "action": disp.heat_pump_action,
            "reason": disp.heat_pump_reason,
            "dispatch_id": disp.dispatch_id,
            "opted_out": disp.opted_out,
        }


class OrchestraACPrecoolBinarySensor(OrchestraBaseEntity, BinarySensorEntity):
    """Signals Ducted/Split A/C to pre-cool living spaces in advance of peak heat/price."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, BINARY_SENSOR_AC_PRECOOL)
        self._attr_name = "A/C Pre-Cool"
        self._attr_device_class = BinarySensorDeviceClass.COLD
        self._attr_icon = "mdi:snowflake"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.dispatch.ac_precool)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            "mode": disp.ac_mode,
            "setpoint_offset_c": disp.ac_setpoint_offset_c,
            "dispatch_mode": disp.ac_dispatch_mode,
            "reason": disp.ac_reason,
            "dispatch_id": disp.dispatch_id,
            "opted_out": disp.opted_out,
        }


class OrchestraACPreheatBinarySensor(OrchestraBaseEntity, BinarySensorEntity):
    """Signals Ducted/Split A/C to pre-heat living spaces during low-cost renewable intervals."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, BINARY_SENSOR_AC_PREHEAT)
        self._attr_name = "A/C Pre-Heat"
        self._attr_device_class = BinarySensorDeviceClass.HEAT
        self._attr_icon = "mdi:fire"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.dispatch.ac_preheat)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            "mode": disp.ac_mode,
            "setpoint_offset_c": disp.ac_setpoint_offset_c,
            "dispatch_mode": disp.ac_dispatch_mode,
            "reason": disp.ac_reason,
            "dispatch_id": disp.dispatch_id,
            "opted_out": disp.opted_out,
        }


class OrchestraHomeBatteryStormWatchBinarySensor(OrchestraBaseEntity, BinarySensorEntity):
    """Signals Home Battery to reserve 100% capacity / charge from grid before extreme weather or grid events."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, BINARY_SENSOR_HOME_BATTERY_STORM_WATCH)
        self._attr_name = "Home Battery Charge (Storm Watch)"
        self._attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING
        self._attr_icon = "mdi:battery-charging-100"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.dispatch.home_battery_storm_watch)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            "min_reserve_soc_pct": disp.battery_min_reserve_soc_pct,
            "min_soc_reserve_pct": disp.battery_min_reserve_soc_pct,
            "charge_from_grid": disp.battery_charge_from_grid,
            "action": disp.battery_action,
            "reason": disp.battery_reason,
            "dispatch_id": disp.dispatch_id,
            "opted_out": disp.opted_out,
        }
