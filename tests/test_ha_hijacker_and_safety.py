"""Tests for Home Assistant Process Hijacker, Storage Injector, AI Agent Tools, and Safety Guardrails."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from custom_components.orchestra import async_setup
from custom_components.orchestra.safety_guard import (
    HardwareSafetyInterlockError,
    InProcessSafetyGuard,
)
from orchestra.adapters.integrations.conductor import ConductorDemandResponseAdapter
from orchestra.config import OrchestraConfig
from orchestra.models import UserEnergyPreferences
from orchestra.orchestrator import OrchestraEngine


class _MockHassBus:
    def __init__(self) -> None:
        self.listeners: dict[str, list] = {}

    def async_listen(self, event_type: str, callback) -> None:
        self.listeners.setdefault(event_type, []).append(callback)

    async def async_fire(self, event_type: str, event_data: dict) -> None:
        for cb in self.listeners.get(event_type, []):
            await cb(event_data)


class _MockHass:
    def __init__(self) -> None:
        self.data: dict = {}
        self.bus = _MockHassBus()


class TestHAHijackerAndSafety(unittest.IsolatedAsyncioTestCase):
    """Verify full HA onboarding bypass, .storage injection, safety interlocks, and Conductor DR."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.tmp_dir.name)
        self.config = OrchestraConfig(ha_config_dir=self.config_dir)
        self.engine = OrchestraEngine(self.config)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    async def test_full_hijack_pipeline_and_storage_files(self) -> None:
        report = await self.engine.discover_and_hijack_all(simulate_all_first_pass_brands=True)

        self.assertTrue(report["bootstrap"]["onboarding_bypassed"])
        self.assertEqual(report["total_devices_onboarded"], 23)
        self.assertGreaterEqual(report["total_entities_registered"], 50)

        # Verify .storage/onboarding has all 4 steps marked done
        onboarding_doc = json.loads((self.config_dir / ".storage" / "onboarding").read_text())
        self.assertEqual(
            set(onboarding_doc["data"]["done"]),
            {"user", "core_config", "analytics", "integration"},
        )

        # Verify active vs suppressed cloud config entries
        active_entries = self.engine.storage_injector.get_injected_entries(include_ignored=False)
        all_entries = self.engine.storage_injector.get_injected_entries(include_ignored=True)
        self.assertEqual(len(active_entries), 23)
        self.assertGreater(len(all_entries), len(active_entries))

        # Verify dnsmasq cloud-liberation file generated
        dnsmasq_text = (self.config_dir / "orchestra_dnsmasq_liberation.conf").read_text()
        self.assertIn("address=/a.tuya.com/192.168.1.250", dnsmasq_text)
        self.assertIn("address=/rheem.clearblade.com/0.0.0.0", dnsmasq_text)

    async def test_ai_commissioning_tools_and_contract_commit(self) -> None:
        await self.engine.discover_and_hijack_all(simulate_all_first_pass_brands=True)
        tools = self.engine.agent_tools

        appliances = await tools.get_discovered_appliances()
        self.assertEqual(len(appliances), 23)

        first_id = appliances[0].device_id
        renamed = await tools.assign_device_metadata(first_id, "Garage Powerwall 3", "Garage / Utility")
        self.assertTrue(renamed)

        prefs = UserEnergyPreferences(summer_ac_max_comfort_c=24.5, precool_target_c=20.5)
        contract = await tools.generate_household_load_contract(prefs)
        committed = await tools.commit_automations_to_node(contract)
        self.assertTrue(committed)

        automations_json = json.loads((self.config_dir / "automations.yaml").read_text())
        self.assertEqual(len(automations_json), 3)

    def test_in_process_safety_guard_dwell_and_clamping(self) -> None:
        guard = InProcessSafetyGuard(
            hvac_min_dwell_seconds=900,
            hvac_max_setpoint_offset_c=2.0,
            hpwh_min_temp_c=50.0,
            hpwh_max_temp_c=65.0,
        )

        # First compressor toggle succeeds at t=1000
        guard.validate_climate_service_call(
            "climate.airtouch5_ducted_ac",
            "set_hvac_mode",
            {"hvac_mode": "cool"},
            now_monotonic=1000.0,
        )

        # Second toggle 120 seconds later must raise HardwareSafetyInterlockError (Pitfall 3)
        with self.assertRaises(HardwareSafetyInterlockError):
            guard.validate_climate_service_call(
                "climate.airtouch5_ducted_ac",
                "turn_off",
                {},
                now_monotonic=1120.0,
            )

        # Setpoint offset > 2.0C during Conductor DR event is clamped to +2.0C
        guard.validate_climate_service_call(
            "climate.airtouch5_ducted_ac",
            "set_temperature",
            {"temperature": 22.0},
            now_monotonic=2000.0,
        )
        clamped_data = guard.validate_climate_service_call(
            "climate.airtouch5_ducted_ac",
            "set_temperature",
            {"temperature": 27.0, "conductor_dr_event": True},
            now_monotonic=2010.0,
        )
        self.assertEqual(clamped_data["temperature"], 24.0)

        # Heat pump water heater setpoint clamped to [50.0, 65.0]
        hp_clamped = guard.validate_water_heater_service_call(
            "water_heater.stiebel_eltron_hpwh",
            "set_temperature",
            {"temperature": 75.0},
        )
        self.assertEqual(hp_clamped["temperature"], 65.0)

    async def test_custom_component_aborts_cloud_discovery_flows(self) -> None:
        hass = _MockHass()
        ok = await async_setup(hass, {"orchestra": {"hvac_min_dwell_seconds": 900}})
        self.assertTrue(ok)

        await hass.bus.async_fire(
            "config_entry_flow_initiated",
            {"handler": "tuya", "flow_id": "cloud_flow_123"},
        )
        await hass.bus.async_fire(
            "config_entry_flow_initiated",
            {"handler": "sensibo", "flow_id": "cloud_flow_456"},
        )
        aborted = hass.data["orchestra"]["aborted_cloud_flows"]
        self.assertEqual(len(aborted), 2)
        self.assertEqual({item["domain"] for item in aborted}, {"tuya", "sensibo"})

    def test_conductor_counterfactual_and_opt_out(self) -> None:
        conductor = ConductorDemandResponseAdapter(self.config)
        cf = conductor.calculate_counterfactual_shed_kw(interval_idx=36, actual_load_kw=0.60)
        self.assertEqual(cf["verified_shed_kw"], 3.2)
        self.assertGreater(cf["estimated_dividend_aud"], 0.0)

        opt_out = conductor.opt_out_event("peak_event_2026_10_16")
        self.assertTrue(opt_out["opted_out"])
        self.assertEqual(opt_out["penalty_aud"], 0.0)
