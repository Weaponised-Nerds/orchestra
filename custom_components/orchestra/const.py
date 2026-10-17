"""Constants for the in-process Home Assistant `orchestra` custom component."""

DOMAIN = "orchestra"

CONF_CLOUD_LIBERATION_MODE = "cloud_liberation_mode"
CONF_OCPP_PORT = "ocpp_port"
CONF_NGANGK_API_URL = "ngangk_api_url"
CONF_HVAC_MIN_DWELL_SECONDS = "hvac_min_dwell_seconds"
CONF_HVAC_MAX_SETPOINT_OFFSET_C = "hvac_max_setpoint_offset_c"

# Cloud-dependent HA domains automatically intercepted and aborted when
# discovered via zeroconf/ssdp/dhcp so Orchestra's local adapters take over.
INTERCEPTED_CLOUD_FLOW_DOMAINS: frozenset[str] = frozenset(
    {
        "tuya",
        "sensibo",
        "tesla_fleet",
        "smartthings",
        "rheem_econet",
        "rinnai",
        "stiebel_eltron_cloud",
        "myair_cloud",
    }
)
