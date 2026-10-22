"""
Tests for Home Assistant Orchestra Custom Component (Ngangk API & Conductor Dispatch).

Validates:
1. Coordinator fetching and parsing Ngangk WEM 30-minute interval forecasts.
2. Exposure of primary sensors: WEM Operational Demand (MW), Wholesale Price (AUD/MWh),
   Renewable Availability (%), and Conductor Control Signal (Normal/Auto, Energy Soak, Load Shed).
3. Subscription to Conductor dispatch signals with granular asset-class responses:
   - EV charge now
   - Heat Pump run now
   - A/C pre-cool and pre-heat
   - Home battery charge (storm watch)
4. Enforcing hardware safety interlocks (dwell, setpoint clamps, thermal storage limits).
5. Homeowner 1-tap opt-out switch functionality.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock, patch

from custom_components.orchestra import async_setup, async_setup_entry
from custom_components.orchestra.binary_sensor import (
    OrchestraACPrecoolBinarySensor,
    OrchestraACPreheatBinarySensor,
    OrchestraEVChargeNowBinarySensor,
    OrchestraHeatPumpRunNowBinarySensor,
    OrchestraHomeBatteryStormWatchBinarySensor,
)
from custom_components.orchestra.conductor_subscriber import ConductorSubscriber
from custom_components.orchestra.const import (
    ACTION_EV_CHARGE_NOW,
    ACTION_HPWH_RUN_NOW,
    DOMAIN,
    SIGNAL_ENERGY_SOAK,
    SIGNAL_LOAD_SHED,
    SIGNAL_NORMAL_AUTO,
    SIGNAL_OPT_OUT,
)
from custom_components.orchestra.coordinator import (
    OrchestraCoordinatorData,
    OrchestraDataUpdateCoordinator,
    OrchestraMarketData,
)
from custom_components.orchestra.safety_guard import InProcessSafetyGuard
from custom_components.orchestra.select import OrchestraConductorDispatchModeSelect
from custom_components.orchestra.sensor import (
    OrchestraACDispatchModeSensor,
    OrchestraAccumulatedDividendSensor,
    OrchestraConductorControlSignalSensor,
    OrchestraCounterfactualShedSensor,
    OrchestraWEMOperationalDemandSensor,
    OrchestraWEMRenewableAvailabilitySensor,
    OrchestraWEMWholesalePriceSensor,
)
from custom_components.orchestra.switch import OrchestraConductorOptOutSwitch


class _MockHassBus:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []
        self.listeners: dict[str, list] = {}

    def async_listen(self, event_type: str, callback) -> None:
        self.listeners.setdefault(event_type, []).append(callback)

    async def async_fire(self, event_type: str, event_data: dict) -> None:
        self.events.append((event_type, event_data))
        for cb in self.listeners.get(event_type, []):
            if asyncio.iscoroutinefunction(cb):
                await cb(event_data)
            else:
                cb(event_data)


class _MockConfigEntries:
    def __init__(self) -> None:
        self.forwarded_setups: list[tuple[Any, list[str]]] = []

    async def async_forward_entry_setups(self, entry: Any, platforms: list[str]) -> bool:
        self.forwarded_setups.append((entry, platforms))
        return True


class _MockHass:
    def __init__(self) -> None:
        self.data: dict = {}
        self.bus = _MockHassBus()
        self.config_entries = _MockConfigEntries()

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class TestOrchestraHAPlugin(unittest.IsolatedAsyncioTestCase):
    """Test suite for the Orchestra custom component and Conductor dispatch engine."""

    def setUp(self) -> None:
        self.hass = _MockHass()
        self.safety_guard = InProcessSafetyGuard(
            hvac_min_dwell_seconds=900,
            hvac_max_setpoint_offset_c=2.0,
            hpwh_min_temp_c=50.0,
            hpwh_max_temp_c=65.0,
        )
        self.coordinator = OrchestraDataUpdateCoordinator(
            hass=self.hass,
            safety_guard=self.safety_guard,
            poll_interval_seconds=0,  # disable auto loop during unit tests
        )

    async def asyncTearDown(self) -> None:
        await self.coordinator.async_stop()

    async def test_coordinator_ngangk_parsing_and_market_sensors(self) -> None:
        """Verify parsing of Ngangk forecast payload into market entities."""
        mock_payload = {
            "status": "success",
            "project": "Conductor",
            "forecast": [
                {
                    "intervalIdx": 1,
                    "tradingIntervalIdx": 25,
                    "timestamp": "2026-10-21T12:00:00+08:00",
                    "timeFormatted": "12:00 AWST",
                    "demandMW": {"p10": 1950.0, "p50": 2100.0, "p90": 2250.0},
                    "renewablesMW": {
                        "totalVRE": 1150.0,
                        "estimatedDPV": 750.0,
                        "utilitySolar": 200.0,
                        "utilityWind": 200.0,
                        "duckCurveSeverityPct": 54.8,
                    },
                    "wholesalePriceRTP": {
                        "p10": -20.0,
                        "p50": -14.50,
                        "p90": 5.0,
                        "isNegative": True,
                    },
                }
            ],
        }

        with patch.object(self.coordinator, "_fetch_ngangk_forecast") as mock_fetch:
            mock_fetch.return_value = OrchestraMarketData(
                operational_demand_mw=2100.0,
                demand_p10_mw=1950.0,
                demand_p90_mw=2250.0,
                wholesale_price_aud_mwh=-14.50,
                price_p10_aud_mwh=-20.0,
                price_p90_aud_mwh=5.0,
                wholesale_price_cents_kwh=-1.45,
                is_negative_price=True,
                renewable_availability_pct=54.8,
                total_vre_mw=1150.0,
                estimated_dpv_mw=750.0,
                utility_solar_mw=200.0,
                utility_wind_mw=200.0,
                duck_curve_severity_pct=54.8,
                trading_interval_idx=25,
                interval_time_formatted="12:00 AWST",
                forecast_timestamp="2026-10-21T12:00:00+08:00",
            )
            await self.coordinator.async_refresh()

        # Check Market Sensors
        demand_sensor = OrchestraWEMOperationalDemandSensor(self.coordinator)
        self.assertEqual(demand_sensor.native_value, 2100.0)
        self.assertEqual(demand_sensor.native_unit_of_measurement, "MW")
        self.assertEqual(demand_sensor.device_class, "power")
        self.assertEqual(demand_sensor.extra_state_attributes["trading_interval_idx"], 25)

        price_sensor = OrchestraWEMWholesalePriceSensor(self.coordinator)
        self.assertEqual(price_sensor.native_value, -14.50)
        self.assertEqual(price_sensor.native_unit_of_measurement, "AUD/MWh")
        self.assertTrue(price_sensor.extra_state_attributes["is_negative"])
        self.assertEqual(price_sensor.extra_state_attributes["cents_per_kwh"], -1.45)

        renewables_sensor = OrchestraWEMRenewableAvailabilitySensor(self.coordinator)
        self.assertEqual(renewables_sensor.native_value, 54.8)
        self.assertEqual(renewables_sensor.native_unit_of_measurement, "%")
        self.assertEqual(renewables_sensor.extra_state_attributes["total_vre_mw"], 1150.0)

        # Negative price triggers autonomous Energy Soak
        signal_sensor = OrchestraConductorControlSignalSensor(self.coordinator)
        self.assertEqual(signal_sensor.native_value, SIGNAL_ENERGY_SOAK)

        # Granular responses automatically active during Energy Soak
        ev_sensor = OrchestraEVChargeNowBinarySensor(self.coordinator)
        self.assertTrue(ev_sensor.is_on)
        self.assertEqual(ev_sensor.extra_state_attributes["target_amps"], 16)

        hp_sensor = OrchestraHeatPumpRunNowBinarySensor(self.coordinator)
        self.assertTrue(hp_sensor.is_on)
        self.assertEqual(hp_sensor.extra_state_attributes["target_temp_c"], 65.0)

        precool_sensor = OrchestraACPrecoolBinarySensor(self.coordinator)
        self.assertTrue(precool_sensor.is_on)
        self.assertEqual(precool_sensor.extra_state_attributes["setpoint_offset_c"], -1.5)

    async def test_autonomous_load_shed_behavior(self) -> None:
        """Verify autonomous Load Shed behavior when WEM wholesale price spikes."""
        self.coordinator.data.market = OrchestraMarketData(
            operational_demand_mw=3400.0,
            wholesale_price_aud_mwh=165.20,
            is_negative_price=False,
            renewable_availability_pct=15.0,
            trading_interval_idx=36,
        )
        self.coordinator._evaluate_autonomous_dispatch()

        signal_sensor = OrchestraConductorControlSignalSensor(self.coordinator)
        self.assertEqual(signal_sensor.native_value, SIGNAL_LOAD_SHED)

        ev_sensor = OrchestraEVChargeNowBinarySensor(self.coordinator)
        self.assertFalse(ev_sensor.is_on)

        hp_sensor = OrchestraHeatPumpRunNowBinarySensor(self.coordinator)
        self.assertFalse(hp_sensor.is_on)

        precool_sensor = OrchestraACPrecoolBinarySensor(self.coordinator)
        self.assertFalse(precool_sensor.is_on)

        ac_mode = OrchestraACDispatchModeSensor(self.coordinator)
        self.assertEqual(ac_mode.native_value, "shed")
        self.assertEqual(ac_mode.extra_state_attributes["setpoint_offset_c"], 1.5)

    async def test_conductor_inbound_push_directive_and_granular_assets(self) -> None:
        """Verify inbound Conductor push dispatch with all granular asset classes."""
        subscriber = ConductorSubscriber(
            hass=self.hass,
            coordinator=self.coordinator,
            node_id="grandpa_wa_node_01",
        )

        inbound_payload = {
            "version": "1.0",
            "dispatch_id": "disp_wem_20261021_042",
            "control_signal": "Energy Soak",
            "reason": "Midday duck curve surplus soak event",
            "valid_until": "2026-10-21T14:30:00Z",
            "granular_assets": {
                "ev": {
                    "enabled": True,
                    "target_amps": 32,
                    "action": "CHARGE_NOW",
                    "reason": "100% solar surplus charging",
                },
                "heat_pump": {
                    "enabled": True,
                    "target_temp_c": 65.0,
                    "action": "RUN_NOW",
                    "reason": "Thermal boost to 65C",
                },
                "hvac": {
                    "action": "PRE_COOL",
                    "precool": True,
                    "setpoint_offset_c": -2.0,
                    "reason": "Pre-cool living area before 5PM peak",
                },
                "home_battery": {
                    "action": "STORM_WATCH_HOLD",
                    "storm_watch": True,
                    "min_soc_reserve_pct": 100.0,
                    "charge_from_grid": True,
                    "reason": "Storm watch grid backup active",
                },
            },
        }

        ack = subscriber.process_inbound_dispatch(inbound_payload)
        self.assertEqual(ack["status"], "applied")
        self.assertEqual(ack["dispatch_id"], "disp_wem_20261021_042")
        self.assertEqual(ack["control_signal"], "Energy Soak")
        self.assertFalse(ack["opted_out"])

        # Verify all binary sensors match the Conductor push directive
        ev_sensor = OrchestraEVChargeNowBinarySensor(self.coordinator)
        self.assertTrue(ev_sensor.is_on)
        self.assertEqual(ev_sensor.extra_state_attributes["target_amps"], 32)
        self.assertEqual(ev_sensor.extra_state_attributes["action"], "CHARGE_NOW")

        hp_sensor = OrchestraHeatPumpRunNowBinarySensor(self.coordinator)
        self.assertTrue(hp_sensor.is_on)
        self.assertEqual(hp_sensor.extra_state_attributes["target_temp_c"], 65.0)

        precool_sensor = OrchestraACPrecoolBinarySensor(self.coordinator)
        self.assertTrue(precool_sensor.is_on)
        self.assertEqual(precool_sensor.extra_state_attributes["setpoint_offset_c"], -2.0)

        preheat_sensor = OrchestraACPreheatBinarySensor(self.coordinator)
        self.assertFalse(preheat_sensor.is_on)

        battery_sensor = OrchestraHomeBatteryStormWatchBinarySensor(self.coordinator)
        self.assertTrue(battery_sensor.is_on)
        self.assertEqual(battery_sensor.extra_state_attributes["min_reserve_soc_pct"], 100.0)
        self.assertTrue(battery_sensor.extra_state_attributes["charge_from_grid"])

        # Yield to allow any async bus tasks to settle
        await asyncio.sleep(0)

        # Check that dispatch event was fired on the HA bus
        self.assertEqual(len(self.hass.bus.events), 1)
        event_name, event_data = self.hass.bus.events[0]
        self.assertEqual(event_name, "orchestra_conductor_dispatch_event")
        self.assertEqual(event_data["dispatch_id"], "disp_wem_20261021_042")

    async def test_hardware_safety_interlock_clamping(self) -> None:
        """Verify safety guardrail clamping on Conductor directives (HVAC, HPWH, EVSE)."""
        subscriber = ConductorSubscriber(
            hass=self.hass,
            coordinator=self.coordinator,
        )

        excessive_payload = {
            "dispatch_id": "disp_unsafe_test",
            "control_signal": "Energy Soak",
            "granular_assets": {
                "ev": {
                    "enabled": True,
                    "target_amps": 2,  # Too low, IEC 61851 requires >= 6A
                },
                "heat_pump": {
                    "enabled": True,
                    "target_temp_c": 85.0,  # Exceeds 65°C safe limit
                },
                "hvac": {
                    "action": "PRE_COOL",
                    "precool": True,
                    "setpoint_offset_c": -5.0,  # Exceeds -2.0°C limit
                },
            },
        }

        ack = subscriber.process_inbound_dispatch(excessive_payload)
        self.assertEqual(ack["status"], "applied")

        # Verify clamps in ACK adjustments
        adjustments = ack["safety_adjustments"]
        self.assertIn("ev_pilot_clamped_to_6a_minimum", adjustments)
        self.assertTrue(any("hpwh_target_clamped" in adj for adj in adjustments))
        self.assertTrue(any("hvac_offset_clamped" in adj for adj in adjustments))

        # Check entity values are strictly clamped
        ev_sensor = OrchestraEVChargeNowBinarySensor(self.coordinator)
        self.assertEqual(ev_sensor.extra_state_attributes["target_amps"], 6)

        hp_sensor = OrchestraHeatPumpRunNowBinarySensor(self.coordinator)
        self.assertEqual(hp_sensor.extra_state_attributes["target_temp_c"], 65.0)

        precool_sensor = OrchestraACPrecoolBinarySensor(self.coordinator)
        self.assertEqual(precool_sensor.extra_state_attributes["setpoint_offset_c"], -2.0)

    async def test_customer_one_tap_opt_out_switch(self) -> None:
        """Verify 1-tap customer opt-out switch zeroes all active DR actions immediately."""
        # First trigger an active energy soak dispatch
        self.coordinator.data.market = OrchestraMarketData(
            wholesale_price_aud_mwh=-15.0, is_negative_price=True
        )
        self.coordinator._evaluate_autonomous_dispatch()
        self.assertTrue(self.coordinator.data.dispatch.ev_charge_now)
        self.assertTrue(self.coordinator.data.dispatch.heat_pump_run_now)

        # Toggle Opt-Out switch ON
        opt_out_switch = OrchestraConductorOptOutSwitch(self.coordinator)
        self.assertFalse(opt_out_switch.is_on)

        await opt_out_switch.async_turn_on()
        self.assertTrue(opt_out_switch.is_on)
        self.assertEqual(self.coordinator.data.dispatch.control_signal, SIGNAL_OPT_OUT)

        # All asset classes must immediately turn OFF
        self.assertFalse(self.coordinator.data.dispatch.ev_charge_now)
        self.assertFalse(self.coordinator.data.dispatch.heat_pump_run_now)
        self.assertFalse(self.coordinator.data.dispatch.ac_precool)
        self.assertFalse(self.coordinator.data.dispatch.home_battery_storm_watch)

        # An incoming Conductor push must be rejected while opt-out is active
        subscriber = ConductorSubscriber(
            hass=self.hass, coordinator=self.coordinator
        )
        reject_ack = subscriber.process_inbound_dispatch(
            {"dispatch_id": "disp_while_opted_out", "control_signal": "Energy Soak"}
        )
        self.assertEqual(reject_ack["status"], "rejected")
        self.assertEqual(reject_ack["reason"], "customer_opt_out_active")

        # Toggle Opt-Out switch back OFF restores automated operation
        await opt_out_switch.async_turn_off()
        self.assertFalse(opt_out_switch.is_on)
        self.assertEqual(self.coordinator.data.dispatch.control_signal, SIGNAL_ENERGY_SOAK)

    async def test_select_entity_manual_mode_override(self) -> None:
        """Verify manual select entity allows operator mode override."""
        select = OrchestraConductorDispatchModeSelect(self.coordinator)
        self.assertIn(SIGNAL_ENERGY_SOAK, select._attr_options)
        self.assertIn(SIGNAL_LOAD_SHED, select._attr_options)

        await select.async_select_option(SIGNAL_LOAD_SHED)
        self.assertEqual(select.current_option, SIGNAL_LOAD_SHED)
        self.assertEqual(self.coordinator.data.dispatch.ac_dispatch_mode, "shed")
        self.assertEqual(self.coordinator.data.dispatch.ac_setpoint_offset_c, 1.5)

    async def test_async_setup_and_setup_entry_wiring(self) -> None:
        """Verify integration registers coordinator, subscriber, and platforms."""
        ok = await async_setup(
            self.hass,
            {
                "orchestra": {
                    "hvac_min_dwell_seconds": 900,
                    "hvac_max_setpoint_offset_c": 2.0,
                    "poll_interval_seconds": 0,
                }
            },
        )
        self.assertTrue(ok)
        self.assertIn("coordinator", self.hass.data[DOMAIN])
        self.assertIn("subscriber", self.hass.data[DOMAIN])

        # Test async_setup_entry forwards platforms
        entry = MagicMock()
        entry.data = {}
        entry_ok = await async_setup_entry(self.hass, entry)
        self.assertTrue(entry_ok)
        self.assertEqual(len(self.hass.config_entries.forwarded_setups), 1)
        _, platforms = self.hass.config_entries.forwarded_setups[0]
        self.assertIn("sensor", platforms)
        self.assertIn("binary_sensor", platforms)
        self.assertIn("select", platforms)
        self.assertIn("switch", platforms)
