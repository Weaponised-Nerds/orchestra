"""Platform for Orchestra sensor entities."""

from __future__ import annotations

from typing import Any

from custom_components.orchestra.const import (
    DOMAIN,
    SENSOR_CONDUCTOR_CONTROL_SIGNAL,
    SENSOR_WEM_OPERATIONAL_DEMAND,
    SENSOR_WEM_RENEWABLE_AVAILABILITY,
    SENSOR_WEM_WHOLESALE_PRICE,
    SIGNAL_ENERGY_SOAK,
    SIGNAL_LOAD_SHED,
)
from custom_components.orchestra.coordinator import OrchestraDataUpdateCoordinator
from custom_components.orchestra.entity import Entity, OrchestraBaseEntity

try:
    from homeassistant.components.sensor import SensorEntity
except ImportError:
    class SensorEntity(Entity):  # type: ignore
        """Sensor entity fallback."""
        pass


async def async_setup_entry(
    hass: Any,
    entry: Any,
    async_add_entities: Any,
) -> None:
    """Set up Orchestra sensors from a config entry."""
    coordinator: OrchestraDataUpdateCoordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities(
        [
            OrchestraWEMOperationalDemandSensor(coordinator),
            OrchestraWEMWholesalePriceSensor(coordinator),
            OrchestraWEMRenewableAvailabilitySensor(coordinator),
            OrchestraConductorControlSignalSensor(coordinator),
            OrchestraACDispatchModeSensor(coordinator),
            OrchestraCounterfactualShedSensor(coordinator),
            OrchestraAccumulatedDividendSensor(coordinator),
        ]
    )


class OrchestraWEMOperationalDemandSensor(OrchestraBaseEntity, SensorEntity):
    """Exposes real-time SWIS WEM Operational Demand (MW)."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, SENSOR_WEM_OPERATIONAL_DEMAND)
        self._attr_name = "WEM Operational Demand"
        self._attr_native_unit_of_measurement = "MW"
        self._attr_device_class = "power"
        self._attr_icon = "mdi:transmission-tower"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.market.operational_demand_mw

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        mkt = self.coordinator.data.market
        return {
            "p10_mw": mkt.demand_p10_mw,
            "p50_mw": mkt.operational_demand_mw,
            "p90_mw": mkt.demand_p90_mw,
            "trading_interval_idx": mkt.trading_interval_idx,
            "interval_time": mkt.interval_time_formatted,
            "forecast_timestamp": mkt.forecast_timestamp,
            "source": mkt.source,
        }


class OrchestraWEMWholesalePriceSensor(OrchestraBaseEntity, SensorEntity):
    """Exposes real-time WEM Wholesale Price (AUD/MWh & c/kWh)."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, SENSOR_WEM_WHOLESALE_PRICE)
        self._attr_name = "WEM Wholesale Price"
        self._attr_native_unit_of_measurement = "AUD/MWh"
        self._attr_device_class = "monetary"
        self._attr_icon = "mdi:currency-usd"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.market.wholesale_price_aud_mwh

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        mkt = self.coordinator.data.market
        return {
            "cents_per_kwh": mkt.wholesale_price_cents_kwh,
            "is_negative": mkt.is_negative_price,
            "p10_aud_mwh": mkt.price_p10_aud_mwh,
            "p50_aud_mwh": mkt.wholesale_price_aud_mwh,
            "p90_aud_mwh": mkt.price_p90_aud_mwh,
            "trading_interval_idx": mkt.trading_interval_idx,
            "source": mkt.source,
        }


class OrchestraWEMRenewableAvailabilitySensor(OrchestraBaseEntity, SensorEntity):
    """Exposes WEM Grid Renewable Generation and Duck Curve Severity (%)."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, SENSOR_WEM_RENEWABLE_AVAILABILITY)
        self._attr_name = "WEM Renewable Availability"
        self._attr_native_unit_of_measurement = "%"
        self._attr_icon = "mdi:solar-power"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.market.renewable_availability_pct

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        mkt = self.coordinator.data.market
        return {
            "total_vre_mw": mkt.total_vre_mw,
            "estimated_dpv_mw": mkt.estimated_dpv_mw,
            "utility_solar_mw": mkt.utility_solar_mw,
            "utility_wind_mw": mkt.utility_wind_mw,
            "duck_curve_severity_pct": mkt.duck_curve_severity_pct,
            "trading_interval_idx": mkt.trading_interval_idx,
        }


class OrchestraConductorControlSignalSensor(OrchestraBaseEntity, SensorEntity):
    """
    Exposes active high-level dispatch control signal from Conductor
    (Normal/Auto, Energy Soak, Load Shed, Opt-Out).
    """

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, SENSOR_CONDUCTOR_CONTROL_SIGNAL)
        self._attr_name = "Conductor Control Signal"
        self._attr_icon = "mdi:broadcast"

    @property
    def native_value(self) -> str:
        return self.coordinator.data.dispatch.control_signal

    @property
    def icon(self) -> str:
        sig = self.coordinator.data.dispatch.control_signal
        if sig == SIGNAL_ENERGY_SOAK:
            return "mdi:solar-power-variant"
        if sig == SIGNAL_LOAD_SHED:
            return "mdi:transmission-tower-export"
        return "mdi:gauge"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            "signal_source": disp.signal_source,
            "dispatch_id": disp.dispatch_id,
            "reason": disp.reason,
            "valid_until": disp.valid_until,
            "opted_out": disp.opted_out,
            "ev_charge_now": disp.ev_charge_now,
            "heat_pump_run_now": disp.heat_pump_run_now,
            "ac_precool": disp.ac_precool,
            "ac_preheat": disp.ac_preheat,
            "home_battery_storm_watch": disp.home_battery_storm_watch,
        }


class OrchestraACDispatchModeSensor(OrchestraBaseEntity, SensorEntity):
    """Exposes current HVAC dispatch state ('idle', 'precool', 'preheat', 'shed')."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "ac_dispatch_mode")
        self._attr_name = "A/C Dispatch Mode"
        self._attr_icon = "mdi:air-conditioner"

    @property
    def native_value(self) -> str:
        return self.coordinator.data.dispatch.ac_dispatch_mode

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            "setpoint_offset_c": disp.ac_setpoint_offset_c,
            "reason": disp.ac_reason,
        }


class OrchestraCounterfactualShedSensor(OrchestraBaseEntity, SensorEntity):
    """Exposes verified Demand Response shed kW against 10-day rolling baseline."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "counterfactual_shed_power")
        self._attr_name = "Verified Demand Response Shed"
        self._attr_native_unit_of_measurement = "kW"
        self._attr_device_class = "power"
        self._attr_icon = "mdi:chart-timeline-variant-shimmer"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.dispatch.counterfactual_shed_kw


class OrchestraAccumulatedDividendSensor(OrchestraBaseEntity, SensorEntity):
    """Exposes cumulative load-shed dividend earned by the homeowner (AUD)."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, "accumulated_dividend_aud")
        self._attr_name = "Homeowner Load-Shed Dividend"
        self._attr_native_unit_of_measurement = "AUD"
        self._attr_device_class = "monetary"
        self._attr_icon = "mdi:cash-multiple"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.dispatch.accumulated_dividend_aud
