"""Platform for Orchestra sensor entities."""

from __future__ import annotations

from typing import Any

try:
    from .const import (
        ASSET_EV,
        ASSET_HEAT_PUMP,
        ASSET_HOME_BATTERY,
        ASSET_HVAC,
        DOMAIN,
        SENSOR_CONDUCTOR_CONTROL_SIGNAL,
        SENSOR_EV_TARGET_AMPS,
        SENSOR_HEAT_PUMP_TARGET_TEMP,
        SENSOR_HOME_BATTERY_MIN_RESERVE,
        SENSOR_HVAC_SETPOINT_OFFSET,
        SENSOR_WEM_OPERATIONAL_DEMAND,
        SENSOR_WEM_RENEWABLE_AVAILABILITY,
        SENSOR_WEM_WHOLESALE_PRICE,
        SIGNAL_ENERGY_SOAK,
        SIGNAL_LOAD_SHED,
    )
    from .coordinator import OrchestraDataUpdateCoordinator
    from .entity import Entity, OrchestraBaseEntity
except ImportError:
    from custom_components.orchestra.const import (
        ASSET_EV,
        ASSET_HEAT_PUMP,
        ASSET_HOME_BATTERY,
        ASSET_HVAC,
        DOMAIN,
        SENSOR_CONDUCTOR_CONTROL_SIGNAL,
        SENSOR_EV_TARGET_AMPS,
        SENSOR_HEAT_PUMP_TARGET_TEMP,
        SENSOR_HOME_BATTERY_MIN_RESERVE,
        SENSOR_HVAC_SETPOINT_OFFSET,
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

try:
    from homeassistant.helpers.restore_state import RestoreEntity
except ImportError:
    class RestoreEntity:  # type: ignore
        """Restore entity fallback: nothing to restore outside HA."""

        async def async_get_last_state(self) -> Any:
            return None


async def async_setup_entry(
    hass: Any,
    entry: Any,
    async_add_entities: Any,
) -> None:
    """Set up Orchestra sensors from a config entry."""
    coordinator: OrchestraDataUpdateCoordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities(
        [
            # 1. Canonical Orchestra Sensors (sensor.orchestra_*)
            OrchestraWEMOperationalDemandSensor(coordinator),
            OrchestraWEMWholesalePriceSensor(coordinator),
            OrchestraWEMRenewableAvailabilitySensor(coordinator),
            OrchestraConductorControlSignalSensor(coordinator),
            OrchestraACDispatchModeSensor(coordinator),
            OrchestraCounterfactualShedSensor(coordinator),
            OrchestraAccumulatedDividendSensor(coordinator),

            # Per-asset directive sensors driving the Orchestra automation blueprints
            OrchestraHVACSetpointOffsetSensor(coordinator),
            OrchestraEVTargetAmpsSensor(coordinator),
            OrchestraHeatPumpTargetTempSensor(coordinator),
            OrchestraHomeBatteryMinReserveSensor(coordinator),

            # 2. Backward-Compatible Conductor Aliases (sensor.conductor_dr_*)
            # Guarantees existing dashboards, Lovelace cards, and automations bind cleanly
            OrchestraConductorControlSignalSensor(
                coordinator,
                key="ngangk_wem_market_signal",
                prefix="conductor_dr",
                entity_id="sensor.conductor_dr_ngangk_wem_market_signal",
            ),
            OrchestraCounterfactualShedSensor(
                coordinator,
                key="counterfactual_shed_power",
                prefix="conductor_dr",
                entity_id="sensor.conductor_dr_counterfactual_shed_power",
            ),
            OrchestraAccumulatedDividendSensor(
                coordinator,
                key="accumulated_dividend_aud",
                prefix="conductor_dr",
                entity_id="sensor.conductor_dr_accumulated_dividend_aud",
            ),
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
            "interval_time": mkt.interval_time_formatted,
            "forecast_timestamp": mkt.forecast_timestamp,
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

    def __init__(
        self,
        coordinator: OrchestraDataUpdateCoordinator,
        key: str = SENSOR_CONDUCTOR_CONTROL_SIGNAL,
        prefix: str = DOMAIN,
        entity_id: str | None = None,
    ) -> None:
        super().__init__(coordinator, key, prefix, entity_id)
        self._attr_name = "Conductor Control Signal" if prefix == DOMAIN else "Conductor Ngangk WEM Market Signal"
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
            "cohort": self.coordinator.cohort,
            "excluded_assets": list(disp.excluded_assets),
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
            "mode": disp.ac_mode,
            "setpoint_offset_c": disp.ac_setpoint_offset_c,
            "reason": disp.ac_reason,
        }


class OrchestraCounterfactualShedSensor(OrchestraBaseEntity, SensorEntity):
    """Exposes verified Demand Response shed kW against 10-day rolling baseline."""

    def __init__(
        self,
        coordinator: OrchestraDataUpdateCoordinator,
        key: str = "counterfactual_shed_power",
        prefix: str = DOMAIN,
        entity_id: str | None = None,
    ) -> None:
        super().__init__(coordinator, key, prefix, entity_id)
        self._attr_name = "Verified Demand Response Shed" if prefix == DOMAIN else "Conductor Verified Demand Response Shed"
        self._attr_native_unit_of_measurement = "kW"
        self._attr_device_class = "power"
        self._attr_icon = "mdi:chart-timeline-variant-shimmer"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.dispatch.counterfactual_shed_kw


class OrchestraAccumulatedDividendSensor(OrchestraBaseEntity, SensorEntity):
    """Exposes cumulative load-shed dividend earned by the homeowner (AUD)."""

    def __init__(
        self,
        coordinator: OrchestraDataUpdateCoordinator,
        key: str = "accumulated_dividend_aud",
        prefix: str = DOMAIN,
        entity_id: str | None = None,
    ) -> None:
        super().__init__(coordinator, key, prefix, entity_id)
        self._attr_name = "Homeowner Load-Shed Dividend" if prefix == DOMAIN else "Conductor Homeowner Load-Shed Dividend"
        self._attr_native_unit_of_measurement = "AUD"
        self._attr_device_class = "monetary"
        self._attr_icon = "mdi:cash-multiple"

    @property
    def native_value(self) -> float:
        return self.coordinator.data.dispatch.accumulated_dividend_aud


class _OrchestraAssetDirectiveSensor(OrchestraBaseEntity, SensorEntity):
    """
    Base for per-asset directive sensors. Values are already cohort-gated: when this
    home is outside a directive's tranche the asset reports its normal schedule.
    """

    _asset: str = ""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator, key: str) -> None:
        super().__init__(coordinator, key, entity_id=f"sensor.{DOMAIN}_{key}")

    def _common_attributes(self, action: str, reason: str) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            "action": action,
            "reason": reason,
            "control_signal": disp.control_signal,
            "signal_source": disp.signal_source,
            "dispatch_id": disp.dispatch_id,
            "valid_until": disp.valid_until,
            "targeted": self._asset not in disp.excluded_assets,
            "opted_out": disp.opted_out,
        }


class OrchestraHVACSetpointOffsetSensor(_OrchestraAssetDirectiveSensor, RestoreEntity):
    """
    Setpoint drift (°C) to add to each A/C's own setpoint; 0 means no override.

    The HVAC blueprint moves setpoints by the change between consecutive states, so
    after a restart this reports the last offset until Orchestra's first update.
    Otherwise a still-active drift would read as 0 -> offset and be applied twice.
    """

    _asset = ASSET_HVAC

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, SENSOR_HVAC_SETPOINT_OFFSET)
        self._attr_name = "HVAC Setpoint Offset"
        self._attr_native_unit_of_measurement = "°C"
        self._attr_icon = "mdi:thermometer-chevron-up"
        self._restored_offset: float | None = None
        self._restored_dispatch_mode: str | None = None

    async def async_added_to_hass(self) -> None:
        last_state = await self.async_get_last_state()
        if last_state is not None:
            try:
                self._restored_offset = float(last_state.state)
                self._restored_dispatch_mode = last_state.attributes.get("dispatch_mode")
            except (TypeError, ValueError):
                self._restored_offset = None
        await super().async_added_to_hass()

    def _handle_coordinator_update(self) -> None:
        self._restored_offset = None
        super()._handle_coordinator_update()

    @property
    def native_value(self) -> float:
        if self._restored_offset is not None:
            return self._restored_offset
        return float(self.coordinator.data.dispatch.ac_setpoint_offset_c)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            **self._common_attributes(disp.ac_dispatch_mode, disp.ac_reason),
            # The blueprint reads dispatch_mode with the value, so restore them together
            "dispatch_mode": (
                self._restored_dispatch_mode if self._restored_offset is not None else disp.ac_dispatch_mode
            ),
            "mode": disp.ac_mode,
            "restored": self._restored_offset is not None,
        }


class OrchestraEVTargetAmpsSensor(_OrchestraAssetDirectiveSensor):
    """EV charger pilot current (A); 0 means pause charging."""

    _asset = ASSET_EV

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, SENSOR_EV_TARGET_AMPS)
        self._attr_name = "EV Target Amps"
        self._attr_native_unit_of_measurement = "A"
        self._attr_device_class = "current"
        self._attr_icon = "mdi:ev-station"

    @property
    def native_value(self) -> int:
        return int(self.coordinator.data.dispatch.ev_target_amps)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            **self._common_attributes(disp.ev_action, disp.ev_reason),
            "charge_now": disp.ev_charge_now,
            "paused": disp.ev_target_amps == 0,
        }


class OrchestraHeatPumpTargetTempSensor(_OrchestraAssetDirectiveSensor):
    """Heat pump hot water target (°C), clamped to the thermal storage guardrail."""

    _asset = ASSET_HEAT_PUMP

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, SENSOR_HEAT_PUMP_TARGET_TEMP)
        self._attr_name = "Heat Pump Target Temperature"
        self._attr_native_unit_of_measurement = "°C"
        self._attr_device_class = "temperature"
        self._attr_icon = "mdi:water-boiler"

    @property
    def native_value(self) -> float:
        return float(self.coordinator.data.dispatch.heat_pump_target_temp_c)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            **self._common_attributes(disp.heat_pump_action, disp.heat_pump_reason),
            "run_now": disp.heat_pump_run_now,
        }


class OrchestraHomeBatteryMinReserveSensor(_OrchestraAssetDirectiveSensor):
    """Home battery minimum state-of-charge reserve (%) to hold."""

    _asset = ASSET_HOME_BATTERY

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, SENSOR_HOME_BATTERY_MIN_RESERVE)
        self._attr_name = "Home Battery Minimum Reserve"
        self._attr_native_unit_of_measurement = "%"
        self._attr_icon = "mdi:battery-lock"

    @property
    def native_value(self) -> float:
        return float(self.coordinator.data.dispatch.battery_min_reserve_soc_pct)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        disp = self.coordinator.data.dispatch
        return {
            **self._common_attributes(disp.battery_action, disp.battery_reason),
            "storm_watch": disp.home_battery_storm_watch,
            "charge_from_grid": disp.battery_charge_from_grid,
        }
