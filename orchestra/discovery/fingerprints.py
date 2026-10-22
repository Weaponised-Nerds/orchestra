"""
Hardware Fingerprint Catalog for Zero-Cloud Local Identification.

Maps mDNS service types, IEEE MAC OUI prefixes, local TCP/UDP ports, and HTTP/UDP
banners to Orchestra hardware adapters across all target brands:
- Solar / Batteries: Tesla, Sigenergy, Fronius, Enphase
- Airconditioning: Polyaire AirTouch 5, Sensibo, Advantage Air MyAir
- Heat Pumps: STIEBEL ELTRON, Rinnai, Rheem, Tuya (White label)
- EV Chargers: Tesla, Sigenergy, Fronius, EVNex
- Other Integrations: Tuya, BOM, Conductor
- System Integrations: Samsung SmartThings, Apple HomeKit, Google Home, Alexa / Ring
"""

from __future__ import annotations

from dataclasses import dataclass, field

from orchestra.models import DeviceCategory, HardwareBrand, LocalProtocol


@dataclass(frozen=True)
class HardwareFingerprint:
    """Signature rules used by `zenith-local-discover` to identify hardware without cloud lookups."""

    fingerprint_id: str
    brand: HardwareBrand
    category: DeviceCategory
    model_name: str
    protocol: LocalProtocol
    default_port: int
    suggested_area: str
    mdns_service_types: tuple[str, ...] = field(default_factory=tuple)
    hostname_prefixes: tuple[str, ...] = field(default_factory=tuple)
    mac_oui_prefixes: tuple[str, ...] = field(default_factory=tuple)
    udp_broadcast_ports: tuple[int, ...] = field(default_factory=tuple)
    http_probe_paths: tuple[str, ...] = field(default_factory=tuple)
    cloud_domains_to_sinkhole: tuple[str, ...] = field(default_factory=tuple)


HARDWARE_FINGERPRINT_CATALOG: tuple[HardwareFingerprint, ...] = (
    # =========================================================================
    # 1. SOLAR / BATTERIES
    # =========================================================================
    HardwareFingerprint(
        fingerprint_id="tesla_powerwall_gateway",
        brand=HardwareBrand.TESLA,
        category=DeviceCategory.SOLAR_BATTERY,
        model_name="Tesla Powerwall 2/3 & Backup Gateway",
        protocol=LocalProtocol.LOCAL_HTTPS_REST,
        default_port=443,
        suggested_area="Garage / Utility",
        mdns_service_types=("_https._tcp.local.", "_tedapi._tcp.local."),
        hostname_prefixes=("teg-", "powerwall-", "teslagateway"),
        mac_oui_prefixes=("4C:FC:AA", "98:ED:5C", "DC:44:27"),
        http_probe_paths=("/api/meters/aggregates", "/api/system_status/soe", "/tedapi/v1"),
    ),
    HardwareFingerprint(
        fingerprint_id="sigenergy_sigenstor_bess",
        brand=HardwareBrand.SIGENERGY,
        category=DeviceCategory.SOLAR_BATTERY,
        model_name="Sigenergy SigenStor 5-in-1 Energy System",
        protocol=LocalProtocol.MODBUS_TCP,
        default_port=502,
        suggested_area="Garage / Utility",
        mdns_service_types=("_modbus._tcp.local.", "_sigen._tcp.local."),
        hostname_prefixes=("sigenstor-", "sigen-gateway-", "sigenergy-"),
        mac_oui_prefixes=("A4:C1:38", "70:B3:D5"),
    ),
    HardwareFingerprint(
        fingerprint_id="fronius_gen24_solar_storage",
        brand=HardwareBrand.FRONIUS,
        category=DeviceCategory.SOLAR_BATTERY,
        model_name="Fronius Symo GEN24 Plus + Smart Meter",
        protocol=LocalProtocol.SUNSPEC_MODBUS,
        default_port=502,
        suggested_area="Roof / Solar Array",
        mdns_service_types=("_fronius._tcp.local.", "_http._tcp.local."),
        hostname_prefixes=("fronius-", "symo-", "primo-"),
        mac_oui_prefixes=("00:03:AC", "00:1C:2B"),
        http_probe_paths=("/solar_api/v1/GetPowerFlowRealtimeData.fcgi",),
    ),
    HardwareFingerprint(
        fingerprint_id="enphase_iq_gateway_envoy",
        brand=HardwareBrand.ENPHASE,
        category=DeviceCategory.SOLAR_BATTERY,
        model_name="Enphase IQ Gateway (Envoy-S Metered) + IQ Battery",
        protocol=LocalProtocol.LOCAL_HTTPS_REST,
        default_port=443,
        suggested_area="Roof / Solar Array",
        mdns_service_types=("_enphase-envoy._tcp.local.",),
        hostname_prefixes=("envoy", "enphase-"),
        mac_oui_prefixes=("00:1D:C0", "AC:1F:09"),
        http_probe_paths=("/info.xml", "/api/v1/production", "/ivp/meters/readings"),
    ),

    # =========================================================================
    # 2. AIRCONDITIONING (HVAC)
    # =========================================================================
    HardwareFingerprint(
        fingerprint_id="polyaire_airtouch5_console",
        brand=HardwareBrand.POLYAIRE_AIRTOUCH5,
        category=DeviceCategory.AIRCONDITIONING,
        model_name="Polyaire AirTouch 5 Ducted Zone Controller",
        protocol=LocalProtocol.AIRTOUCH5_BINARY_TCP,
        default_port=9005,
        suggested_area="Living & Bedrooms",
        mdns_service_types=("_airtouch._tcp.local.",),
        hostname_prefixes=("airtouch5-", "airtouch-", "polyaire-"),
        mac_oui_prefixes=("30:AE:A4", "24:6F:28"),
        udp_broadcast_ports=(49005,),
    ),
    HardwareFingerprint(
        fingerprint_id="sensibo_air_local_hap",
        brand=HardwareBrand.SENSIBO,
        category=DeviceCategory.AIRCONDITIONING,
        model_name="Sensibo Air PRO Split-System Controller",
        protocol=LocalProtocol.HOMEKIT_HAP_LOCAL,
        default_port=80,
        suggested_area="Living & Bedrooms",
        mdns_service_types=("_hap._tcp.local.",),
        hostname_prefixes=("sensibo-", "sensibo-air-"),
        mac_oui_prefixes=("84:F3:EB", "ECFABC"),
        cloud_domains_to_sinkhole=("firmware.sensibo.com",),
    ),
    HardwareFingerprint(
        fingerprint_id="advantage_air_myair5",
        brand=HardwareBrand.ADVANTAGE_AIR,
        category=DeviceCategory.AIRCONDITIONING,
        model_name="Advantage Air MyAir 5 / MyPlace Controller",
        protocol=LocalProtocol.LOCAL_HTTP_REST,
        default_port=2025,
        suggested_area="Living & Bedrooms",
        mdns_service_types=("_myair._tcp.local.", "_http._tcp.local."),
        hostname_prefixes=("myair-", "myplace-", "ezone-"),
        mac_oui_prefixes=("B4:E6:2D", "54:B8:0A"),
        http_probe_paths=("/getSystemData",),
        cloud_domains_to_sinkhole=("ota.myair.com.au",),
    ),

    # =========================================================================
    # 3. HEAT PUMP HOT WATER (HPWH)
    # =========================================================================
    HardwareFingerprint(
        fingerprint_id="stiebel_eltron_isg_wwk",
        brand=HardwareBrand.STIEBEL_ELTRON,
        category=DeviceCategory.HEAT_PUMP,
        model_name="STIEBEL ELTRON WWK 302 Electronic (ISG Modbus/SG-Ready)",
        protocol=LocalProtocol.MODBUS_TCP,
        default_port=502,
        suggested_area="Garage / Utility",
        mdns_service_types=("_servicewelt._tcp.local.", "_modbus._tcp.local."),
        hostname_prefixes=("servicewelt", "stiebel-", "isg-"),
        mac_oui_prefixes=("00:0C:C6", "00:1E:C0"),
        http_probe_paths=("/?s=1,0",),
    ),
    HardwareFingerprint(
        fingerprint_id="rinnai_enviroflo_hpwh",
        brand=HardwareBrand.RINNAI,
        category=DeviceCategory.HEAT_PUMP,
        model_name="Rinnai Enviroflo GR Series Heat Pump",
        protocol=LocalProtocol.MODBUS_TCP,
        default_port=502,
        suggested_area="Garage / Utility",
        mdns_service_types=("_rinnai._tcp.local.", "_modbus._tcp.local."),
        hostname_prefixes=("rinnai-enviroflo-", "rinnai-hp-"),
        mac_oui_prefixes=("D8:F1:5B", "60:01:94"),
        udp_broadcast_ports=(6666, 6667),
        cloud_domains_to_sinkhole=("ota.rinnai.com.au",),
    ),
    HardwareFingerprint(
        fingerprint_id="rheem_ambipower_econet",
        brand=HardwareBrand.RHEEM,
        category=DeviceCategory.HEAT_PUMP,
        model_name="Rheem AmbiPower 280e / MPi-325 Heat Pump",
        protocol=LocalProtocol.MODBUS_TCP,
        default_port=502,
        suggested_area="Garage / Utility",
        mdns_service_types=("_econet._tcp.local.", "_modbus._tcp.local."),
        hostname_prefixes=("rheem-", "ambipower-", "econet-"),
        mac_oui_prefixes=("00:23:A7", "F0:FE:6B"),
        cloud_domains_to_sinkhole=("rheem.clearblade.com",),
    ),
    HardwareFingerprint(
        fingerprint_id="tuya_whitelabel_heatpump",
        brand=HardwareBrand.TUYA_WHITELABEL_HP,
        category=DeviceCategory.HEAT_PUMP,
        model_name="Tuya White-Label OEM Inverter Heat Pump (Emerald/Midea/Chromagen)",
        protocol=LocalProtocol.TUYA_LOCAL_UDP_AES,
        default_port=6668,
        suggested_area="Garage / Utility",
        hostname_prefixes=("wlan0", "tuya-hp-", "smartlife-hp-"),
        mac_oui_prefixes=("D8:1F:12", "10:D5:61", "50:8A:06"),
        udp_broadcast_ports=(6666, 6667, 7000),
        cloud_domains_to_sinkhole=("a.tuya.com", "m2.tuya.com", "ota.tuya.com"),
    ),
    HardwareFingerprint(
        fingerprint_id="istore_270l_wifi_heatpump",
        brand=HardwareBrand.ISTORE,
        category=DeviceCategory.HEAT_PUMP,
        model_name="iStore 270L Solar Heat Pump (Onboard Wi-Fi / RS485 Bridge)",
        protocol=LocalProtocol.MODBUS_TCP,
        default_port=8899,
        suggested_area="Garage / Utility",
        hostname_prefixes=("istore-", "hf-lpb", "hf-lpt", "f11-"),
        mac_oui_prefixes=("AC:CF:23", "F0:FE:6B", "D8:1F:12"),
        udp_broadcast_ports=(48899, 6666, 6667),
        cloud_domains_to_sinkhole=("a.tuya.com", "m2.tuya.com"),
    ),

    # =========================================================================
    # 4. EV CHARGERS (EVSE)
    # =========================================================================
    HardwareFingerprint(
        fingerprint_id="tesla_wall_connector_gen3",
        brand=HardwareBrand.TESLA,
        category=DeviceCategory.EV_CHARGER,
        model_name="Tesla Wall Connector Gen 3",
        protocol=LocalProtocol.LOCAL_HTTP_REST,
        default_port=80,
        suggested_area="Driveway / EV Bay",
        mdns_service_types=("_http._tcp.local.",),
        hostname_prefixes=("teslawallconnector_", "twc-"),
        mac_oui_prefixes=("4C:FC:AA", "98:ED:5C"),
        http_probe_paths=("/api/1/vitals", "/api/1/lifetime", "/api/1/version"),
    ),
    HardwareFingerprint(
        fingerprint_id="sigenergy_sigen_evac",
        brand=HardwareBrand.SIGENERGY,
        category=DeviceCategory.EV_CHARGER,
        model_name="Sigenergy Sigen EVAC 22kW / DC Charger",
        protocol=LocalProtocol.OCPP_16J_WS,
        default_port=9000,
        suggested_area="Driveway / EV Bay",
        mdns_service_types=("_ocpp._tcp.local.", "_sigen-ev._tcp.local."),
        hostname_prefixes=("sigen-evac-", "sigen-evse-"),
        mac_oui_prefixes=("A4:C1:38",),
    ),
    HardwareFingerprint(
        fingerprint_id="fronius_wattpilot_evse",
        brand=HardwareBrand.FRONIUS,
        category=DeviceCategory.EV_CHARGER,
        model_name="Fronius Wattpilot Home 22J",
        protocol=LocalProtocol.WATTPILOT_LOCAL_WS,
        default_port=80,
        suggested_area="Driveway / EV Bay",
        mdns_service_types=("_wattpilot._tcp.local.", "_go-echarger._tcp.local."),
        hostname_prefixes=("wattpilot-", "fronius-ev-"),
        mac_oui_prefixes=("00:03:AC", "E8:DB:84"),
        http_probe_paths=("/ws", "/api/status"),
    ),
    HardwareFingerprint(
        fingerprint_id="evnex_e2_smart_charger",
        brand=HardwareBrand.EVNEX,
        category=DeviceCategory.EV_CHARGER,
        model_name="EVNex E2 Plus Smart EV Charger",
        protocol=LocalProtocol.OCPP_16J_WS,
        default_port=9000,
        suggested_area="Driveway / EV Bay",
        mdns_service_types=("_evnex._tcp.local.", "_ocpp._tcp.local."),
        hostname_prefixes=("evnex-", "e2-"),
        mac_oui_prefixes=("EC:62:60", "80:7D:3A"),
    ),

    # =========================================================================
    # 5. OTHER INTEGRATIONS (TUYA, BOM, CONDUCTOR)
    # =========================================================================
    HardwareFingerprint(
        fingerprint_id="tuya_local_smart_plug_relay",
        brand=HardwareBrand.TUYA,
        category=DeviceCategory.OTHER_INTEGRATION,
        model_name="Tuya Local DIN Contactor & Energy Sub-Meter",
        protocol=LocalProtocol.TUYA_LOCAL_UDP_AES,
        default_port=6668,
        suggested_area="Garage / Utility",
        hostname_prefixes=("tuya-", "smartlife-", "esp_"),
        mac_oui_prefixes=("D8:1F:12", "10:D5:61", "84:E3:42"),
        udp_broadcast_ports=(6666, 6667, 7000),
        cloud_domains_to_sinkhole=("a.tuya.com", "a.tuyaeu.com", "m2.tuya.com", "ota.tuya.com"),
    ),
    HardwareFingerprint(
        fingerprint_id="bom_wa_weather_solar",
        brand=HardwareBrand.BOM,
        category=DeviceCategory.OTHER_INTEGRATION,
        model_name="Bureau of Meteorology (BOM) WA Station & ACCESS-G Irradiance",
        protocol=LocalProtocol.BOM_ANON_FEED,
        default_port=443,
        suggested_area="Grid & Weather Intelligence",
        hostname_prefixes=("api.weather.bom.gov.au",),
    ),
    HardwareFingerprint(
        fingerprint_id="conductor_demand_response_vpp",
        brand=HardwareBrand.CONDUCTOR,
        category=DeviceCategory.OTHER_INTEGRATION,
        model_name="Conductor Demand Response & Ngangk WEM Orchestrator",
        protocol=LocalProtocol.NGANGK_REST_WS,
        default_port=443,
        suggested_area="Grid & Weather Intelligence",
        hostname_prefixes=("conductor.deezlepower.com", "ngangk.deeben.workers.dev", "conductor.zenith.energy"),
    ),

    # =========================================================================
    # 6. SYSTEM INTEGRATIONS (ECOSYSTEM BRIDGES)
    # =========================================================================
    HardwareFingerprint(
        fingerprint_id="samsung_smartthings_edge_bridge",
        brand=HardwareBrand.SMARTTHINGS,
        category=DeviceCategory.SYSTEM_ECOSYSTEM,
        model_name="Samsung SmartThings Local Edge LAN & Matter Bridge",
        protocol=LocalProtocol.SSDP_EDGE_LAN,
        default_port=39500,
        suggested_area="System Ecosystem Bridges",
        mdns_service_types=("_smartthings._tcp.local.", "_matter._tcp.local."),
        hostname_prefixes=("smartthings-hub-", "st-edge-"),
    ),
    HardwareFingerprint(
        fingerprint_id="apple_homekit_local_bridge",
        brand=HardwareBrand.APPLE_HOMEKIT,
        category=DeviceCategory.SYSTEM_ECOSYSTEM,
        model_name="Apple HomeKit Native HAP Bridge (Zero-Cloud)",
        protocol=LocalProtocol.HOMEKIT_HAP_LOCAL,
        default_port=21063,
        suggested_area="System Ecosystem Bridges",
        mdns_service_types=("_hap._tcp.local.",),
        hostname_prefixes=("zenith-homekit-bridge",),
    ),
    HardwareFingerprint(
        fingerprint_id="google_home_matter_local_bridge",
        brand=HardwareBrand.GOOGLE_HOME,
        category=DeviceCategory.SYSTEM_ECOSYSTEM,
        model_name="Google Home Local Matter & Local Fulfillment Bridge",
        protocol=LocalProtocol.MATTER_THREAD_WIFI,
        default_port=5540,
        suggested_area="System Ecosystem Bridges",
        mdns_service_types=("_matter._tcp.local.", "_googlecast._tcp.local."),
        hostname_prefixes=("google-nest-", "zenith-matter-"),
    ),
    HardwareFingerprint(
        fingerprint_id="alexa_ring_local_bridge",
        brand=HardwareBrand.ALEXA_RING,
        category=DeviceCategory.SYSTEM_ECOSYSTEM,
        model_name="Amazon Alexa Local Matter/Hue & Ring RTSP Intercom Bridge",
        protocol=LocalProtocol.EMULATED_HUE_RTSP,
        default_port=80,
        suggested_area="System Ecosystem Bridges",
        mdns_service_types=("_matter._tcp.local.", "_rtsp._tcp.local."),
        hostname_prefixes=("echo-", "ring-doorbell-", "zenith-hue-"),
    ),
)


def match_hardware_fingerprint(
    hostname: str = "",
    mac_address: str = "",
    mdns_service: str = "",
    open_port: int | None = None,
) -> HardwareFingerprint | None:
    """
    Match raw network observations (hostname, MAC OUI, mDNS service, or open port)
    against the Orchestra Hardware Fingerprint Catalog.
    """
    host_lower = hostname.lower()
    mac_upper = mac_address.upper()[:8]
    mdns_lower = mdns_service.lower()

    for fp in HARDWARE_FINGERPRINT_CATALOG:
        if host_lower and any(host_lower.startswith(prefix) for prefix in fp.hostname_prefixes):
            return fp
        if mdns_lower and any(mdns_lower == svc.lower() for svc in fp.mdns_service_types):
            if open_port is None or open_port == fp.default_port:
                return fp
        if mac_upper and any(mac_upper.startswith(oui) for oui in fp.mac_oui_prefixes):
            if open_port is None or open_port == fp.default_port:
                return fp
    return None
