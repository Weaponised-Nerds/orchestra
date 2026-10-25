"""
Tests for the Conductor asset automations:
- Per-asset directive sensors the blueprints read (cohort-gated, reset on opt-out)
- Blueprint installation into the HA config dir
- The setup agent filling the blueprints in with the bound asset entities

The blueprints themselves run inside a real Home Assistant core in `tests/ha/`.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import tempfile
import unittest

from custom_components.orchestra.blueprint_installer import BUNDLED_BLUEPRINT_DIR, install_blueprints
from custom_components.orchestra.coordinator import OrchestraDataUpdateCoordinator
from custom_components.orchestra.hvac_drift import plan_drift
from custom_components.orchestra.safety_guard import InProcessSafetyGuard
from custom_components.orchestra.sensor import (
    OrchestraEVTargetAmpsSensor,
    OrchestraHeatPumpTargetTempSensor,
    OrchestraHomeBatteryMinReserveSensor,
    OrchestraHVACSetpointOffsetSensor,
)
from orchestra.agent import ConductorAutomationBuilder, GrandpaCommissioningAgent
from orchestra.agent.dr_binder import ConductorBindingManifest, ControllableLoadBinding
from orchestra.agent.entity_organizer import OrganizedEntityRecord
from orchestra.config import OrchestraConfig
from orchestra.orchestrator import OrchestraEngine

# The VPP manager frame from Conductor (version 1.2)
VPP_FRAME = {
    "version": "1.2",
    "source": "manual",
    "control_signal": "Normal/Auto",
    "reason": "VPP manager dispatch: Normal/Auto",
    "valid_until": "2099-10-25T03:41:40.614Z",
    "granular_assets": {
        "ev": {"enabled": False, "charge_now": False, "action": "Load Shed - Charging Paused",
               "target_amps": 0, "reason": "VPP manager dispatch: Normal/Auto"},
        "heat_pump": {"enabled": False, "run_now": False, "action": "THERMAL_COAST",
                      "target_temp_c": 50, "reason": "VPP manager dispatch: Normal/Auto"},
        "hvac": {"enabled": True, "action": "SHED_OFFSET", "mode": "auto",
                 "setpoint_offset_c": 1.5, "reason": "VPP manager dispatch: Normal/Auto"},
        "home_battery": {"storm_watch": False, "charge_now": False, "charge_from_grid": False,
                         "action": "PEAK_DISCHARGE", "min_soc_reserve_pct": 20,
                         "reason": "VPP manager dispatch: Normal/Auto"},
    },
}


class _MockHass:
    def __init__(self) -> None:
        self.data: dict = {}

    def async_create_task(self, coro):
        return asyncio.create_task(coro)


class TestAssetDirectiveSensors(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.coordinator = OrchestraDataUpdateCoordinator(
            hass=_MockHass(),
            safety_guard=InProcessSafetyGuard(hvac_max_setpoint_offset_c=2.0),
            poll_interval_seconds=0,
        )
        self.hvac = OrchestraHVACSetpointOffsetSensor(self.coordinator)
        self.ev = OrchestraEVTargetAmpsSensor(self.coordinator)
        self.heat_pump = OrchestraHeatPumpTargetTempSensor(self.coordinator)
        self.battery = OrchestraHomeBatteryMinReserveSensor(self.coordinator)

    async def asyncTearDown(self) -> None:
        await self.coordinator.async_stop()

    def test_entity_ids_match_blueprint_defaults(self) -> None:
        self.assertEqual(self.hvac.entity_id, "sensor.orchestra_hvac_setpoint_offset")
        self.assertEqual(self.ev.entity_id, "sensor.orchestra_ev_target_amps")
        self.assertEqual(self.heat_pump.entity_id, "sensor.orchestra_heat_pump_target_temp")
        self.assertEqual(self.battery.entity_id, "sensor.orchestra_home_battery_min_reserve")
        for path in BUNDLED_BLUEPRINT_DIR.glob("*.yaml"):
            text = path.read_text(encoding="utf-8")
            self.assertTrue(
                any(s.entity_id in text for s in (self.hvac, self.ev, self.heat_pump, self.battery)),
                path.name,
            )

    def test_vpp_frame_drives_each_asset_sensor(self) -> None:
        self.coordinator.apply_conductor_dispatch(VPP_FRAME)

        self.assertEqual(self.hvac.native_value, 1.5)
        self.assertEqual(self.hvac.extra_state_attributes["dispatch_mode"], "shed")
        self.assertTrue(self.hvac.extra_state_attributes["targeted"])

        self.assertEqual(self.ev.native_value, 0)
        self.assertTrue(self.ev.extra_state_attributes["paused"])
        self.assertEqual(self.ev.extra_state_attributes["action"], "Load Shed - Charging Paused")

        self.assertEqual(self.heat_pump.native_value, 50.0)
        self.assertEqual(self.heat_pump.extra_state_attributes["action"], "THERMAL_COAST")

        self.assertEqual(self.battery.native_value, 20.0)
        self.assertEqual(self.battery.extra_state_attributes["action"], "PEAK_DISCHARGE")

    def test_offset_is_clamped_to_safety_limit(self) -> None:
        frame = json.loads(json.dumps(VPP_FRAME))
        frame["granular_assets"]["hvac"]["setpoint_offset_c"] = 4.0
        self.coordinator.apply_conductor_dispatch(frame)
        self.assertEqual(self.hvac.native_value, 2.0)

    def test_outside_tranche_reports_normal_schedule(self) -> None:
        frame = json.loads(json.dumps(VPP_FRAME))
        for directive in frame["granular_assets"].values():
            directive["cohort"] = {"from": 0, "to": 25}
        self.coordinator.cohort = 60
        self.coordinator.apply_conductor_dispatch(frame)

        self.assertEqual(self.hvac.native_value, 0.0)
        self.assertFalse(self.hvac.extra_state_attributes["targeted"])
        self.assertEqual(self.ev.native_value, 16)
        self.assertEqual(self.ev.extra_state_attributes["action"], "NORMAL")
        self.assertEqual(self.heat_pump.extra_state_attributes["action"], "NORMAL")
        self.assertEqual(self.battery.extra_state_attributes["action"], "SELF_CONSUME")

    def test_opt_out_returns_every_asset_to_normal(self) -> None:
        self.coordinator.apply_conductor_dispatch(VPP_FRAME)
        self.coordinator.set_opt_out(True)

        self.assertEqual(self.hvac.native_value, 0.0)
        self.assertEqual(self.ev.native_value, 16)
        self.assertEqual(self.ev.extra_state_attributes["action"], "NORMAL")
        self.assertEqual(self.heat_pump.extra_state_attributes["action"], "NORMAL")
        self.assertEqual(self.battery.native_value, 20.0)
        self.assertTrue(self.ev.extra_state_attributes["opted_out"])

    async def test_hvac_offset_restores_last_value_until_first_update(self) -> None:
        """After a restart the blueprint must not see a still-active drift as 0 -> offset."""

        class _LastState:
            state = "1.5"
            attributes = {"dispatch_mode": "shed"}

        async def _last_state():
            return _LastState()

        self.hvac.async_get_last_state = _last_state
        self.hvac.async_write_ha_state = lambda: None
        await self.hvac.async_added_to_hass()
        self.assertEqual(self.hvac.native_value, 1.5)
        self.assertEqual(self.hvac.extra_state_attributes["dispatch_mode"], "shed")
        self.assertTrue(self.hvac.extra_state_attributes["restored"])

        self.coordinator.apply_conductor_dispatch(VPP_FRAME)
        self.assertEqual(self.hvac.native_value, 1.5)
        self.assertFalse(self.hvac.extra_state_attributes["restored"])

        self.coordinator.set_opt_out(True)
        self.assertEqual(self.hvac.native_value, 0.0)

    def test_manual_load_shed_pauses_ev(self) -> None:
        self.coordinator.set_manual_dispatch_mode("Load Shed")
        self.assertEqual(self.ev.native_value, 0)
        self.coordinator.set_manual_dispatch_mode("Normal/Auto")
        self.assertEqual(self.ev.native_value, 16)


class TestBlueprintInstallAndAgentAutomations(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.tmp_dir.name)
        self.engine = OrchestraEngine(OrchestraConfig(ha_config_dir=self.config_dir))

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_install_blueprints_writes_once_and_refreshes_outdated(self) -> None:
        written = install_blueprints(self.config_dir)
        self.assertEqual(len(written), 4)
        self.assertEqual(install_blueprints(self.config_dir), [])

        written[0].write_text("stale", encoding="utf-8")
        self.assertEqual(install_blueprints(self.config_dir), [written[0]])

    def test_builder_fills_blueprints_with_bound_assets(self) -> None:
        manifest = ConductorBindingManifest(
            bound_at="now",
            controllable_loads=[
                ControllableLoadBinding("climate.lounge_ac", "Lounge Air Conditioner", "Lounge",
                                        "hvac", None, None, 2, "hvac"),
                ControllableLoadBinding("number.wallbox_charging_current", "Garage EV Charger", "Garage",
                                        "ev_charger", None, None, 1, "evse"),
                ControllableLoadBinding("number.wallbox_maximum_current", "Garage EV Charger", "Garage",
                                        "ev_charger", None, None, 1, "evse"),
                ControllableLoadBinding("water_heater.tank", "Hot Water", "Laundry",
                                        "heat_pump", None, None, 1, "hpwh"),
            ],
        )
        organized = [
            OrganizedEntityRecord("switch.wallbox_charging_allowed", "switch", "x", "EV Charging", "Garage"),
            OrganizedEntityRecord("number.powerwall_backup_reserve", "number", "x", "Battery Reserve", "Garage"),
        ]
        plan = ConductorAutomationBuilder(self.engine.storage_injector).build_asset_automations(
            manifest, organized
        )

        by_id = {a["id"]: a["use_blueprint"] for a in plan.automations}
        self.assertEqual(
            by_id["orchestra_hvac_offset_lounge_ac"],
            {
                "path": "orchestra/orchestra_hvac_setpoint_offset.yaml",
                "input": {"climate_entity": "climate.lounge_ac"},
            },
        )
        # One automation per charger, with its charging switch found by name
        ev = [a for a in plan.automations if a["id"].startswith("orchestra_ev_rate_")]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["use_blueprint"]["input"]["charger_switch"], "switch.wallbox_charging_allowed")
        self.assertIn("orchestra_heat_pump_tank", by_id)
        self.assertEqual(
            by_id["orchestra_battery_reserve_powerwall_backup_reserve"]["input"],
            {"reserve_number": "number.powerwall_backup_reserve"},
        )

        # Drifting from the live setpoint needs no helper
        self.assertFalse((self.config_dir / ".storage" / "input_number").exists())
        for name in ("orchestra_hvac_setpoint_offset.yaml", "orchestra_ev_charge_rate.yaml"):
            self.assertTrue((self.config_dir / "blueprints" / "automation" / "orchestra" / name).exists())

    async def test_full_setup_commits_blueprint_automations(self) -> None:
        result = await GrandpaCommissioningAgent(self.engine).run_autonomous_grandpa_setup()

        automations = json.loads((self.config_dir / "automations.yaml").read_text())
        self.assertEqual(automations, result.asset_automation_plan.automations)
        paths = {a["use_blueprint"]["path"] for a in automations}
        self.assertEqual(
            paths,
            {
                "orchestra/orchestra_hvac_setpoint_offset.yaml",
                "orchestra/orchestra_ev_charge_rate.yaml",
                "orchestra/orchestra_heat_pump_target.yaml",
                "orchestra/orchestra_home_battery_reserve.yaml",
            },
        )
        hvac = [a for a in automations if a["id"].startswith("orchestra_hvac_offset_")]
        self.assertTrue(hvac)
        for a in hvac:
            self.assertEqual(set(a["use_blueprint"]["input"]), {"climate_entity"})


class TestHvacDriftPlan(unittest.TestCase):
    """`plan_drift` decides each `orchestra.apply_hvac_offset` call from remembered state."""

    def _plan(self, held, current, offset, mode="cool", dispatch="shed", step=0.5):
        return plan_drift(
            held, current=current, hvac_mode=mode, offset=offset, dispatch_mode=dispatch,
            step=step, min_temp=16, max_temp=30,
        )

    def test_sequence_never_compounds_and_restores(self) -> None:
        held = None
        for offset, current, expected in ((1.5, 23.0, 24.5), (2.0, 24.5, 25.0), (1.5, 25.0, 24.5)):
            target, held = self._plan(held, current, offset)
            self.assertEqual(target, expected)
            self.assertEqual(held["baseline"], 23.0)
        target, held = self._plan(held, 24.5, 0.0, dispatch="idle")
        self.assertEqual((target, held), (23.0, None))

    def test_steady_offset_is_applied_once(self) -> None:
        target, held = self._plan(None, 23.0, 1.5)
        self.assertEqual(target, 24.5)
        self.assertEqual(self._plan(held, 24.5, 1.5), (None, held))

    def test_manual_change_becomes_new_start(self) -> None:
        _, held = self._plan(None, 23.0, 1.5)
        target, held = self._plan(held, 22.0, 1.5)
        self.assertIsNone(target)
        self.assertEqual(held["baseline"], 20.5)

    def test_device_rounding_is_not_a_manual_change(self) -> None:
        _, held = self._plan(None, 23.0, 1.5, step=None)
        target, held = self._plan(held, 25.0, 1.5, step=None)  # unit rounded 24.5 up
        self.assertIsNone(target)
        self.assertEqual(held["baseline"], 23.0)

    def test_off_units_and_unavailable_states_are_left_alone(self) -> None:
        self.assertEqual(self._plan(None, 23.0, 1.5, mode="off"), (None, None))
        self.assertEqual(self._plan(None, 23.0, 1.5, mode="unavailable"), (None, None))
        self.assertEqual(self._plan(None, None, 1.5), (None, None))
