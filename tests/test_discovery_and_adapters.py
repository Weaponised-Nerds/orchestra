"""Tests for hardware discovery fingerprints and all 19 zero-cloud brand adapters."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from orchestra.adapters import (
    ADAPTER_CLASS_REGISTRY,
    EnphaseSolarBatteryAdapter,
    FroniusWattpilotAdapter,
    LocalOCPP16JServer,
    PolyaireAirTouch5Adapter,
    SigenergySolarBatteryAdapter,
    TuyaCloudFreeActivator,
    get_adapter_for_hardware,
)
from orchestra.config import OrchestraConfig
from orchestra.discovery.fingerprints import (
    HARDWARE_FINGERPRINT_CATALOG,
    match_hardware_fingerprint,
)
from orchestra.discovery.scanner import ZeroConfigDiscoveryScanner
from orchestra.models import DeviceCategory, HardwareBrand


class TestDiscoveryAndAdapters(unittest.IsolatedAsyncioTestCase):
    """Verify discovery and zero-cloud credential extraction across all target brands."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.config = OrchestraConfig(ha_config_dir=Path(self.tmp_dir.name))

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_all_19_brand_category_pairs_registered(self) -> None:
        """Ensure every brand & category requested in the first pass is present in the catalog."""
        expected_pairs = {
            # Solar / Batteries
            (DeviceCategory.SOLAR_BATTERY, HardwareBrand.TESLA),
            (DeviceCategory.SOLAR_BATTERY, HardwareBrand.SIGENERGY),
            (DeviceCategory.SOLAR_BATTERY, HardwareBrand.FRONIUS),
            (DeviceCategory.SOLAR_BATTERY, HardwareBrand.ENPHASE),
            # Airconditioning
            (DeviceCategory.AIRCONDITIONING, HardwareBrand.POLYAIRE_AIRTOUCH5),
            (DeviceCategory.AIRCONDITIONING, HardwareBrand.SENSIBO),
            (DeviceCategory.AIRCONDITIONING, HardwareBrand.ADVANTAGE_AIR),
            # Heat Pumps
            (DeviceCategory.HEAT_PUMP, HardwareBrand.STIEBEL_ELTRON),
            (DeviceCategory.HEAT_PUMP, HardwareBrand.RINNAI),
            (DeviceCategory.HEAT_PUMP, HardwareBrand.RHEEM),
            (DeviceCategory.HEAT_PUMP, HardwareBrand.TUYA_WHITELABEL_HP),
            (DeviceCategory.HEAT_PUMP, HardwareBrand.ISTORE),
            # EV Chargers
            (DeviceCategory.EV_CHARGER, HardwareBrand.TESLA),
            (DeviceCategory.EV_CHARGER, HardwareBrand.SIGENERGY),
            (DeviceCategory.EV_CHARGER, HardwareBrand.FRONIUS),
            (DeviceCategory.EV_CHARGER, HardwareBrand.EVNEX),
            # Other Integrations
            (DeviceCategory.OTHER_INTEGRATION, HardwareBrand.TUYA),
            (DeviceCategory.OTHER_INTEGRATION, HardwareBrand.BOM),
            (DeviceCategory.OTHER_INTEGRATION, HardwareBrand.CONDUCTOR),
            # System Ecosystem Bridges
            (DeviceCategory.SYSTEM_ECOSYSTEM, HardwareBrand.SMARTTHINGS),
            (DeviceCategory.SYSTEM_ECOSYSTEM, HardwareBrand.APPLE_HOMEKIT),
            (DeviceCategory.SYSTEM_ECOSYSTEM, HardwareBrand.GOOGLE_HOME),
            (DeviceCategory.SYSTEM_ECOSYSTEM, HardwareBrand.ALEXA_RING),
        }
        catalog_pairs = {(fp.category, fp.brand) for fp in HARDWARE_FINGERPRINT_CATALOG}
        self.assertEqual(catalog_pairs, expected_pairs)
        self.assertEqual(set(ADAPTER_CLASS_REGISTRY.keys()), expected_pairs)

    def test_fingerprint_matching_by_hostname_and_mac(self) -> None:
        fp_fronius = match_hardware_fingerprint(hostname="fronius-gen24.local")
        self.assertIsNotNone(fp_fronius)
        assert fp_fronius is not None
        self.assertEqual(fp_fronius.brand, HardwareBrand.FRONIUS)

        fp_airtouch = match_hardware_fingerprint(mac_address="30:AE:A4:11:22:33", open_port=9005)
        self.assertIsNotNone(fp_airtouch)
        assert fp_airtouch is not None
        self.assertEqual(fp_airtouch.brand, HardwareBrand.POLYAIRE_AIRTOUCH5)

    def test_tuya_fake_cloud_activator_and_3_4_binary_framing(self) -> None:
        """Verify Tuya local_key generation without Tuya Cloud and HMAC-SHA256 frame integrity."""
        act = TuyaCloudFreeActivator.activate_device_without_cloud(
            device_id="tuya_plug_01",
            mac_address="D8:1F:12:AA:BB:CC",
        )
        self.assertEqual(len(act["local_key"]), 16)
        self.assertTrue(act["gw_id"].startswith("eb"))

        frame = TuyaCloudFreeActivator.build_local_dp_command_frame(
            gw_id=act["gw_id"],
            local_key=act["local_key"],
            dps={"1": True, "19": 1850},
        )
        self.assertTrue(TuyaCloudFreeActivator.verify_frame_integrity(frame, act["local_key"]))
        self.assertFalse(TuyaCloudFreeActivator.verify_frame_integrity(frame, "0000000000000000"))

    def test_protocol_specific_frame_builders(self) -> None:
        """Verify AirTouch 5 CRC-16, Sigenergy Modbus ADU, Enphase digest, OCPP 1.6J, and Wattpilot hash."""
        # Polyaire AirTouch 5 TCP 9005 packet
        at5_pkt = PolyaireAirTouch5Adapter.build_ac_status_request_packet()
        self.assertTrue(at5_pkt.startswith(b"\x55\x55\x55\xaa"))

        # Sigenergy Modbus TCP Read Holding Register 40029
        modbus_frame = SigenergySolarBatteryAdapter.build_modbus_read_holding_frame(247, 40029, 1)
        self.assertEqual(len(modbus_frame), 12)

        # Enphase deterministic installer digest password
        enphase_pwd = EnphaseSolarBatteryAdapter.derive_envoy_installer_password("122300098765")
        self.assertEqual(len(enphase_pwd), 6)

        # Local OCPP 1.6J SetChargingProfile call
        ocpp_call = LocalOCPP16JServer.build_set_charging_profile_call(limit_amps=16)
        self.assertEqual(ocpp_call[0], 2)
        self.assertEqual(ocpp_call[2], "SetChargingProfile")

        # Fronius Wattpilot local WebSocket SHA256 auth hash
        wp_hash = FroniusWattpilotAdapter.compute_wattpilot_auth_hash("WP-1234", "secret", "rand123")
        self.assertEqual(len(wp_hash), 64)

    async def test_all_adapters_extract_credentials_and_verify_control(self) -> None:
        scanner = ZeroConfigDiscoveryScanner(self.config)
        discovered = await scanner.scan(simulate_all_first_pass_brands=True)
        self.assertEqual(len(discovered), 23)

        for hw in discovered:
            adapter = get_adapter_for_hardware(hw.category, hw.brand, self.config)
            creds = await adapter.extract_local_credentials(hw)
            self.assertEqual(creds.device_id, hw.device_id)

            entry_spec = adapter.build_ha_config_entry(hw, creds)
            self.assertTrue(entry_spec.domain)
            self.assertGreater(len(entry_spec.entities), 0)

            diag = await adapter.verify_local_control(hw, creds)
            self.assertTrue(diag.success)
            self.assertTrue(diag.cloud_free_verified)
