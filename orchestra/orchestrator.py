"""
Main Orchestra Engine.

Coordinates the end-to-end Home Assistant process hijack & cloud-free hardware
onboarding pipeline:
1. Bypasses Home Assistant's interactive onboarding wizard (`HAProcessHijacker`).
2. Configures local DNS sinkholes & fake-cloud activation redirects (`LocalCloudBlocker`).
3. Scans the local subnet for Solar/Battery, HVAC, Heat Pump, EV Charger, Tuya,
   BOM, Conductor, and System Ecosystem bridges (`ZeroConfigDiscoveryScanner`).
4. Runs each brand's `HardwareAdapter` to extract/synthesize local credentials
   without vendor cloud logins.
5. Injects ConfigEntries, Devices, Areas, and Curated Entities directly into
   Home Assistant's `.storage` registries (`HAStorageInjector`).
6. Verifies local controllability and enforces electrical/compressor safety interlocks.
"""

from __future__ import annotations

from typing import Any

from orchestra.adapters import get_adapter_for_hardware
from orchestra.agent.tools import ZenithAgentCommissioningTools
from orchestra.config import OrchestraConfig
from orchestra.discovery.scanner import ZeroConfigDiscoveryScanner
from orchestra.hijacker.bootstrap import HAProcessHijacker
from orchestra.hijacker.cloud_blocker import LocalCloudBlocker
from orchestra.hijacker.storage_injector import HAStorageInjector
from orchestra.models import (
    DiagnosticResult,
    DiscoveredHardware,
    HAConfigEntrySpec,
    HardwareBrand,
    LocalCredentials,
)


class OrchestraEngine:
    """Top-level coordinator for the Zenith Micro-Node (`Z-Node`) Orchestra suite."""

    def __init__(self, config: OrchestraConfig | None = None) -> None:
        self.config = config or OrchestraConfig()
        self.process_hijacker = HAProcessHijacker(self.config)
        self.storage_injector = HAStorageInjector(self.config)
        self.cloud_blocker = LocalCloudBlocker(self.config.node_local_ip)
        self.scanner = ZeroConfigDiscoveryScanner(self.config)
        self.agent_tools = ZenithAgentCommissioningTools(self)

        self.discovered_devices: dict[str, DiscoveredHardware] = {}
        self.extracted_credentials: dict[str, LocalCredentials] = {}
        self.injected_specs: dict[str, HAConfigEntrySpec] = {}
        self.diagnostics: dict[str, DiagnosticResult] = {}

    async def discover_and_hijack_all(
        self,
        simulate_all_first_pass_brands: bool = True,
        filter_brands: list[HardwareBrand] | None = None,
    ) -> dict[str, Any]:
        """
        Execute the full zero-cloud Home Assistant hijack and hardware onboarding
        pipeline across all discovered devices.
        """
        # Step 1: Hijack HA onboarding & seed WA locale + system owner token
        bootstrap_report = self.process_hijacker.bypass_onboarding()
        self.storage_injector.initialize_registries()

        # Step 2: Scan local network (or reference home inventory)
        hardware_list = await self.scanner.scan(
            simulate_all_first_pass_brands=simulate_all_first_pass_brands,
            filter_brands=filter_brands,
        )

        onboarded_summary: list[dict[str, Any]] = []

        # Step 3: For each discovered hardware item, extract local credentials,
        # sinkhole hostile cloud domains, inject into HA .storage, and verify control
        for hw in hardware_list:
            self.discovered_devices[hw.device_id] = hw
            if hw.cloud_domains_to_sinkhole:
                self.cloud_blocker.register_hardware_domains(hw.cloud_domains_to_sinkhole)

            adapter = get_adapter_for_hardware(hw.category, hw.brand, self.config)
            creds = await adapter.extract_local_credentials(hw)
            self.extracted_credentials[hw.device_id] = creds

            entry_spec = adapter.build_ha_config_entry(hw, creds)
            injection_result = self.storage_injector.inject_config_entry(entry_spec, hw)
            self.injected_specs[hw.device_id] = entry_spec

            diag = await adapter.verify_local_control(hw, creds)
            self.diagnostics[hw.device_id] = diag

            onboarded_summary.append(
                {
                    "device_id": hw.device_id,
                    "brand": hw.brand.value,
                    "category": hw.category.value,
                    "model_name": hw.model_name,
                    "protocol": hw.protocol.value,
                    "auth_mode": creds.auth_mode,
                    "ha_domain": entry_spec.domain,
                    "ha_entry_id": injection_result["entry_id"],
                    "entities_count": injection_result["entities_count"]
                    if "entities_count" in injection_result
                    else injection_result["entities_registered"],
                    "cloud_free_verified": diag.cloud_free_verified,
                    "diagnostic_latency_ms": diag.latency_ms,
                }
            )

        # Write out the dnsmasq cloud-liberation configuration file inside ha_config_dir
        dnsmasq_path = self.config.ha_config_dir / "orchestra_dnsmasq_liberation.conf"
        dnsmasq_path.write_text(self.cloud_blocker.render_dnsmasq_conf(), encoding="utf-8")

        return {
            "bootstrap": bootstrap_report,
            "total_devices_onboarded": len(onboarded_summary),
            "total_entities_registered": len(self.storage_injector.get_registered_entities()),
            "dns_intercept_rules_count": len(self.cloud_blocker.rules),
            "onboarded_devices": onboarded_summary,
        }

    async def verify_device_control(
        self,
        device_id: str,
        action: str = "default_diagnostic",
    ) -> DiagnosticResult:
        """Run a targeted local controllability diagnostic for a specific device."""
        hw = self.discovered_devices[device_id]
        creds = self.extracted_credentials[device_id]
        adapter = get_adapter_for_hardware(hw.category, hw.brand, self.config)
        diag = await adapter.verify_local_control(hw, creds, action=action)
        self.diagnostics[device_id] = diag
        return diag
