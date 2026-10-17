"""
Tests for the Grandpa-Simple Autonomous Agent Framework:
- Active load-stepping circuit identification (`ActiveCircuitIdentifier`)
- Semantic device/entity naming & standard HA discovery organization (`SemanticEntityOrganizer`)
- Conductor Demand Response entity binding (`ConductorEntityBinder`)
- Custom Lovelace & Energy dashboard generation (`GrandpaDashboardBuilder`)
- iStore 180L/270L Heat Pump LAN prober (`IStoreHeatPumpAdapter`)
"""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from orchestra.adapters.heat_pumps.istore import IStoreHeatPumpAdapter
from orchestra.agent import (
    ActiveCircuitIdentifier,
    CommissioningStage,
    GrandpaCommissioningAgent,
    UnidentifiedPowerChannel,
)
from orchestra.config import OrchestraConfig
from orchestra.orchestrator import OrchestraEngine


class TestGrandpaAgentFramework(unittest.IsolatedAsyncioTestCase):
    """Verify the end-to-end 6-stage Grandpa-Simple Agent Commissioning Pipeline."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.config_dir = Path(self.tmp_dir.name)
        self.config = OrchestraConfig(ha_config_dir=self.config_dir)
        self.engine = OrchestraEngine(self.config)
        self.agent = GrandpaCommissioningAgent(self.engine)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    async def test_active_load_stepping_circuit_identification(self) -> None:
        """Verify power monitors are identified by stepping loads and watching live ΔW."""
        channels = [
            UnidentifiedPowerChannel("sensor.ct_ch1", "Channel 1", "ct_box", baseline_watts=100.0),
            UnidentifiedPowerChannel("sensor.ct_ch2", "Channel 2", "ct_box", baseline_watts=80.0),
            UnidentifiedPowerChannel("sensor.ct_ch3", "Channel 3", "ct_box", baseline_watts=50.0),
            UnidentifiedPowerChannel("sensor.ct_ch4", "Channel 4", "ct_box", baseline_watts=110.0),
            UnidentifiedPowerChannel("sensor.ct_ch5", "Channel 5", "ct_box", baseline_watts=90.0),
        ]
        identifier = ActiveCircuitIdentifier()
        report = await identifier.identify_all_power_monitors(
            channels=channels,
            run_interactive_kettle_step=True,
        )

        self.assertEqual(report.total_channels_scanned, 5)
        self.assertEqual(len(report.identified_circuits), 5)
        self.assertEqual(len(report.unidentified_channels), 0)

        by_sensor = {m.sensor_entity_id: m for m in report.identified_circuits}
        # Channel 1 matched iStore Heat Pump (+950W)
        self.assertEqual(
            by_sensor["sensor.ct_ch1"].assigned_friendly_name,
            "iStore Hot Water Heat Pump Circuit Power",
        )
        self.assertEqual(
            by_sensor["sensor.ct_ch1"].linked_appliance_entity_id,
            "water_heater.istore_heat_pump_hot_water",
        )
        self.assertEqual(by_sensor["sensor.ct_ch1"].delta_watts, 950.0)

        # Channel 2 matched Main Ducted AC (+2650W)
        self.assertEqual(
            by_sensor["sensor.ct_ch2"].assigned_friendly_name,
            "Main Ducted Air Conditioner Circuit Power",
        )
        self.assertEqual(by_sensor["sensor.ct_ch2"].delta_watts, 2650.0)

        # Channel 5 matched Kitchen & General Power via the 10-second Kettle test (+2150W)
        self.assertEqual(
            by_sensor["sensor.ct_ch5"].identification_method,
            "grandpa_kettle_test",
        )

    async def test_full_6_stage_grandpa_autonomous_setup(self) -> None:
        """Verify all 6 stages run end-to-end and write Lovelace, Energy, and Conductor bindings."""
        result = await self.agent.run_autonomous_grandpa_setup()

        self.assertEqual(len(result.completed_stages), 6)
        self.assertIn(CommissioningStage.STAGE_2_CIRCUIT_LOAD_PROBING, result.completed_stages)
        self.assertIn(CommissioningStage.STAGE_5_CUSTOM_DASHBOARD_GENERATION, result.completed_stages)

        # Diagnostic noise (RSSI, reboot buttons) must be hidden from Grandpa
        self.assertGreaterEqual(result.total_hidden_noise_entities, 2)

        # Verify Conductor entity bindings file written and contains linked circuit sensors
        bindings_file = self.config_dir / ".storage" / "conductor.entity_bindings"
        self.assertTrue(bindings_file.exists())
        bindings_doc = json.loads(bindings_file.read_text())
        loads = bindings_doc["data"]["controllable_loads"]
        self.assertGreater(len(loads), 0)

        istore_load = next(
            (l for l in loads if l["control_entity_id"] == "water_heater.istore_heat_pump_hot_water"),
            None,
        )
        self.assertIsNotNone(istore_load)
        assert istore_load is not None
        self.assertEqual(
            istore_load["verified_power_sensor_entity_id"],
            "sensor.shelly_pro_3em_channel_a_power",
        )

        # Verify Lovelace and Energy dashboards generated
        lovelace_file = self.config_dir / ".storage" / "lovelace"
        energy_file = self.config_dir / ".storage" / "energy"
        self.assertTrue(lovelace_file.exists())
        self.assertTrue(energy_file.exists())

        lovelace_doc = json.loads(lovelace_file.read_text())
        views = lovelace_doc["data"]["config"]["views"]
        self.assertEqual([v["title"] for v in views], ["My Home", "Energy & Money Saved"])

    async def test_istore_live_lan_prober(self) -> None:
        """Verify the iStore Heat Pump Wi-Fi & Modbus prober executes cleanly."""
        probe = await IStoreHeatPumpAdapter.live_probe_istore_on_lan(timeout=0.05)
        self.assertTrue(probe["istore_probe_executed"])
        self.assertEqual(probe["udp_48899_query_sent"], "HF-A11ASSISTHREAD")
        self.assertIn("board_pairing_guide", probe)
