"""Core domain models for Orchestra hardware discovery, cloud-liberation, and HA hijacking."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
import uuid


class DeviceCategory(str, Enum):
    """Primary hardware/integration categories supported by Orchestra."""

    SOLAR_BATTERY = "solar_battery"
    AIRCONDITIONING = "airconditioning"
    HEAT_PUMP = "heat_pump"
    EV_CHARGER = "ev_charger"
    OTHER_INTEGRATION = "other_integration"
    SYSTEM_ECOSYSTEM = "system_ecosystem"


class HardwareBrand(str, Enum):
    """First-pass supported hardware and ecosystem brands."""

    # Solar / Batteries (& EV Chargers where applicable)
    TESLA = "tesla"
    SIGENERGY = "sigenergy"
    FRONIUS = "fronius"
    ENPHASE = "enphase"

    # Airconditioning
    POLYAIRE_AIRTOUCH5 = "polyaire_airtouch5"
    SENSIBO = "sensibo"
    ADVANTAGE_AIR = "advantage_air"

    # Heat Pump Hot Water (HPWH)
    STIEBEL_ELTRON = "stiebel_eltron"
    RINNAI = "rinnai"
    RHEEM = "rheem"
    TUYA_WHITELABEL_HP = "tuya_whitelabel_hp"
    ISTORE = "istore"

    # Dedicated EV Chargers
    EVNEX = "evnex"

    # Other Integrations
    TUYA = "tuya"
    BOM = "bom"
    CONDUCTOR = "conductor"

    # System Integrations (Ecosystem Bridges)
    SMARTTHINGS = "samsung_smartthings"
    APPLE_HOMEKIT = "apple_homekit"
    GOOGLE_HOME = "google_home"
    ALEXA_RING = "alexa_ring"


class LocalProtocol(str, Enum):
    """Zero-cloud local communication protocols used by Orchestra adapters."""

    MODBUS_TCP = "modbus_tcp"
    SUNSPEC_MODBUS = "sunspec_modbus"
    LOCAL_HTTPS_REST = "local_https_rest"
    LOCAL_HTTP_REST = "local_http_rest"
    TEDAPI_LOCAL = "tedapi_local"
    AIRTOUCH5_BINARY_TCP = "airtouch5_binary_tcp"
    HOMEKIT_HAP_LOCAL = "homekit_hap_local"
    OCPP_16J_WS = "ocpp_16j_ws"
    WATTPILOT_LOCAL_WS = "wattpilot_local_ws"
    TUYA_LOCAL_UDP_AES = "tuya_local_udp_aes"
    MATTER_THREAD_WIFI = "matter_thread_wifi"
    SSDP_EDGE_LAN = "ssdp_edge_lan"
    EMULATED_HUE_RTSP = "emulated_hue_rtsp"
    NGANGK_REST_WS = "ngangk_rest_ws"
    BOM_ANON_FEED = "bom_anon_feed"


@dataclass
class DiscoveredHardware:
    """Represents a physical device or local service discovered on the home subnet."""

    device_id: str
    brand: HardwareBrand
    category: DeviceCategory
    model_name: str
    protocol: LocalProtocol
    ip_address: str
    port: int
    mac_address: str = "00:00:00:00:00:00"
    hostname: str = ""
    serial_number: str = ""
    firmware_version: str = "1.0.0"
    SuggestedArea: str = "Garage / Utility"
    discovery_method: str = "mdns"  # mdns, arp_oui, udp_broadcast, tcp_probe, ble, virtual
    raw_metadata: dict[str, Any] = field(default_factory=dict)
    cloud_domains_to_sinkhole: list[str] = field(default_factory=list)


@dataclass
class LocalCredentials:
    """Locally extracted or synthesized credentials replacing cloud OAuth/accounts."""

    device_id: str
    protocol: LocalProtocol
    auth_mode: str  # e.g., "tuya_fake_ap_local_key", "enphase_serial_digest", "hap_local_pair", "none_required"
    local_key: str = ""
    token: str = ""
    username: str = ""
    password: str = ""
    pin_code: str = ""
    ocpp_ws_url: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class HAEntitySpec:
    """Represents a Home Assistant entity automatically registered and curated by Orchestra."""

    entity_id: str
    domain: str  # sensor, switch, climate, number, select, binary_sensor
    friendly_name: str
    device_class: str | None = None
    unit_of_measurement: str | None = None
    initial_state: Any = None
    attributes: dict[str, Any] = field(default_factory=dict)


@dataclass
class HAConfigEntrySpec:
    """
    Represents a Home Assistant ConfigEntry ready for direct injection into
    `.storage/core.config_entries` or via `/api/config/config_entries/flow`.
    """

    domain: str
    title: str
    data: dict[str, Any]
    source: str = "orchestra_hijack"
    version: int = 1
    minor_version: int = 1
    unique_id: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    entry_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    suggested_area: str = "Utility"
    entities: list[HAEntitySpec] = field(default_factory=list)
    suppressed_cloud_domains: list[str] = field(default_factory=list)

    def to_ha_storage_dict(self) -> dict[str, Any]:
        """Serialize to the exact JSON schema expected by Home Assistant `.storage/core.config_entries`."""
        now_iso = datetime.now(timezone.utc).isoformat()
        return {
            "entry_id": self.entry_id,
            "version": self.version,
            "minor_version": self.minor_version,
            "domain": self.domain,
            "title": self.title,
            "data": self.data,
            "options": self.options,
            "pref_disable_new_entities": False,
            "pref_disable_polling": False,
            "source": self.source,
            "unique_id": self.unique_id or self.entry_id,
            "disabled_by": None,
            "created_at": now_iso,
            "modified_at": now_iso,
            "discovery_keys": {},
        }


@dataclass
class DiagnosticResult:
    """Result of a silent local controllability check during commissioning."""

    device_id: str
    brand: HardwareBrand
    action_tested: str
    success: bool
    latency_ms: float
    cloud_free_verified: bool
    telemetry_snapshot: dict[str, Any] = field(default_factory=dict)
    safety_guardrails_active: list[str] = field(default_factory=list)
    message: str = ""


@dataclass
class UserEnergyPreferences:
    """Homeowner comfort and schedule boundaries collected by the Zenith AI Concierge."""

    departure_time: str = "07:30"
    return_time: str = "17:30"
    summer_ac_max_comfort_c: float = 24.0
    precool_target_c: float = 20.5
    ev_target_soc_pct: float = 80.0
    bess_evening_reserve_soc_pct: float = 80.0
    allow_autonomous_solar_soak: bool = True
    allow_peak_load_shed: bool = True


@dataclass
class LoadShedContract:
    """Compiled local automation & Conductor demand response contract."""

    contract_id: str
    created_at: str
    preferences: UserEnergyPreferences
    enrolled_device_ids: list[str]
    estimated_monthly_savings_aud: float
    sheddable_capacity_kw: float
    ha_automations: list[dict[str, Any]]
    safety_interlocks: dict[str, Any]
