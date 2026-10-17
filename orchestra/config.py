"""Configuration and hardware safety guardrails for the Zenith Micro-Node (Z-Node) Orchestra."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SafetyGuardrailsConfig:
    """
    Hard-coded electrical and compressor safety interlocks (Pitfall 3 mitigation).
    Cannot be overridden by cloud dispatch or AI agent commands.
    """

    # Minimum time an HVAC compressor must remain in its current state before toggling (15 mins)
    hvac_min_dwell_seconds: int = 900
    # Maximum setpoint nudge during a demand response event (+1.5°C to +2.0°C)
    hvac_max_setpoint_offset_c: float = 2.0
    # Never cut raw mains power to inverter-driven HVAC compressors
    forbid_raw_mains_cut_on_hvac: bool = True
    # Minimum OCPP pilot current before pausing EV charging (IEC 61851 standard = 6A)
    evse_min_pilot_amps: int = 6
    evse_max_pilot_amps: int = 32
    # Minimum home battery SoC reserve for backup/blackout protection
    bess_min_backup_soc_pct: float = 15.0
    # Heat Pump Hot Water (HPWH) thermal storage & Legionella guardrails (AS/NZS 3500.4)
    hpwh_solar_boost_temp_c: float = 65.0
    hpwh_peak_coast_min_temp_c: float = 50.0
    hpwh_legionella_min_weekly_temp_c: float = 60.0
    forbid_hpwh_dry_element_cycling: bool = True


@dataclass
class OrchestraConfig:
    """Global runtime configuration for the Orchestra Hijack & Liberation Daemon."""

    # Home Assistant runtime & config paths
    ha_config_dir: Path = field(default_factory=lambda: Path("./ha_config_sandbox"))
    ha_base_url: str = "http://127.0.0.1:8123"

    # Default locale for Western Australia (SWIS / WEM)
    location_name: str = "Zenith Active Home (WA)"
    latitude: float = -31.9505  # Perth / Peel region default
    longitude: float = 115.8605
    elevation_m: int = 25
    time_zone: str = "Australia/Perth"
    currency: str = "AUD"
    country: str = "AU"

    # Local Protocol Liberation Endpoints hosted on the Z-Node
    node_hostname: str = "zenith-node.local"
    node_local_ip: str = "192.168.1.250"
    ocpp_server_port: int = 9000
    tuya_fake_cloud_port: int = 8886
    matter_bridge_port: int = 5540
    homekit_bridge_port: int = 21063

    # Ngangk WEM Predictive Energy Engine & Conductor Demand Response settings
    ngangk_api_base_url: str = "https://ngangk.deeben.workers.dev"
    conductor_vpp_ws_url: str = "wss://conductor.zenith.energy/v1/dispatch"
    forecast_poll_interval_seconds: int = 900  # 15 minutes
    baseline_rolling_days: int = 10

    # Hardware Safety Interlocks
    safety: SafetyGuardrailsConfig = field(default_factory=SafetyGuardrailsConfig)
