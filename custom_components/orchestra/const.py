"""Constants for the in-process Home Assistant `orchestra` custom component."""

DOMAIN = "orchestra"

CONF_CLOUD_LIBERATION_MODE = "cloud_liberation_mode"
CONF_OCPP_PORT = "ocpp_port"
CONF_NGANGK_API_URL = "ngangk_api_url"
CONF_CONDUCTOR_WS_URL = "conductor_ws_url"
CONF_CONDUCTOR_API_KEY = "conductor_api_key"
CONF_CONDUCTOR_WEBHOOK_ID = "conductor_webhook_id"
CONF_POLL_INTERVAL_SECONDS = "poll_interval_seconds"
CONF_NODE_ID = "node_id"
CONF_HVAC_MIN_DWELL_SECONDS = "hvac_min_dwell_seconds"
CONF_HVAC_MAX_SETPOINT_OFFSET_C = "hvac_max_setpoint_offset_c"
CONF_HPWH_MIN_TEMP_C = "hpwh_min_temp_c"
CONF_HPWH_MAX_TEMP_C = "hpwh_max_temp_c"

DEFAULT_NGANGK_API_URL = "https://conductor.deezlepower.com/api/conductor/forecast"
FALLBACK_NGANGK_API_URL = "https://ngangk.deeben.workers.dev/api/ngangk/forecast"
DEFAULT_CONDUCTOR_WS_URL = "wss://conductor.deezlepower.com/v1/broadcast"
DEFAULT_CONDUCTOR_WEBHOOK_ID = "orchestra_conductor_dispatch"
DEFAULT_POLL_INTERVAL_SECONDS = 900  # 15 minutes
DEFAULT_HVAC_MIN_DWELL_SECONDS = 600  # 10 minutes
DEFAULT_HVAC_MAX_SETPOINT_OFFSET_C = 2.0
DEFAULT_HPWH_MIN_TEMP_C = 50.0
DEFAULT_HPWH_MAX_TEMP_C = 65.0
DEFAULT_NODE_ID = "anonymous_subscriber"

# Dispatch Control Signals
SIGNAL_NORMAL_AUTO = "Normal/Auto"
SIGNAL_ENERGY_SOAK = "Energy Soak"
SIGNAL_LOAD_SHED = "Load Shed"
SIGNAL_OPT_OUT = "Opt-Out"
VALID_CONTROL_SIGNALS = (
    SIGNAL_NORMAL_AUTO,
    SIGNAL_ENERGY_SOAK,
    SIGNAL_LOAD_SHED,
    SIGNAL_OPT_OUT,
)

# Granular Asset Classes & Directives
ASSET_EV = "ev"
ASSET_HEAT_PUMP = "heat_pump"
ASSET_HVAC = "hvac"
ASSET_HOME_BATTERY = "home_battery"

ACTION_EV_CHARGE_NOW = "CHARGE_NOW"
ACTION_EV_PAUSE_OR_MIN_6A = "PAUSE_OR_MIN_6A"
ACTION_EV_LOAD_SHED_PAUSED = "Load Shed - Charging Paused"


ACTION_HPWH_RUN_NOW = "RUN_NOW"
ACTION_HPWH_THERMAL_BOOST = "THERMAL_BOOST_65C"
ACTION_HPWH_THERMAL_COAST = "THERMAL_COAST"

ACTION_HVAC_PRE_COOL = "PRE_COOL"
ACTION_HVAC_PRE_HEAT = "PRE_HEAT"
ACTION_HVAC_SHED_OFFSET = "SHED_OFFSET"

ACTION_BATTERY_STORM_WATCH_HOLD = "STORM_WATCH_HOLD"
ACTION_BATTERY_CHARGE_FROM_GRID = "CHARGE_FROM_GRID"
ACTION_BATTERY_PEAK_DISCHARGE = "PEAK_DISCHARGE"

# Entity Unique IDs and Object IDs
SENSOR_WEM_OPERATIONAL_DEMAND = "wem_operational_demand"
SENSOR_WEM_WHOLESALE_PRICE = "wem_wholesale_price"
SENSOR_WEM_RENEWABLE_AVAILABILITY = "wem_renewable_availability"
SENSOR_CONDUCTOR_CONTROL_SIGNAL = "conductor_control_signal"

BINARY_SENSOR_EV_CHARGE_NOW = "ev_charge_now"
BINARY_SENSOR_HEAT_PUMP_RUN_NOW = "heat_pump_run_now"
BINARY_SENSOR_AC_PRECOOL = "ac_precool"
BINARY_SENSOR_AC_PREHEAT = "ac_preheat"
BINARY_SENSOR_HOME_BATTERY_STORM_WATCH = "home_battery_storm_watch"

SELECT_CONDUCTOR_DISPATCH_MODE = "conductor_dispatch_mode"
SWITCH_CONDUCTOR_OPT_OUT = "conductor_opt_out"

# Events
EVENT_ORCHESTRA_CONDUCTOR_DISPATCH = "orchestra_conductor_dispatch_event"
EVENT_ORCHESTRA_ASSET_DIRECTIVE = "orchestra_asset_directive_event"

PLATFORMS: list[str] = ["sensor", "binary_sensor", "select", "switch"]

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
