"""
In-Process Home Assistant Custom Component (`custom_components/orchestra`).

Loaded automatically by `configuration.yaml` (`orchestra:`) or Config Flow.
1. Queries the Ngangk WEM Predictive Energy Engine for real-time 30-minute SWIS
   operational demand, wholesale price RTP, and renewable availability.
2. Manages Conductor demand-response control signals (`Normal/Auto`, `Energy Soak`,
   `Load Shed`) and publishes granular asset directives (EV charge now, Heat Pump
   run now, A/C pre-cool/pre-heat, Home battery storm watch).
3. Subscribes to Conductor push events via outbound WebSocket client and local Webhook.
4. On a fresh Orchestra image, runs the setup agent (`setup_agent.py`) that finishes
   setup after Home Assistant's own discovery and keeps a punch list for the rest.
5. Enforces compressor dwell (10-min) and setpoint offset guardrails.
6. Provides 1-tap customer opt-out with zero financial or contractual penalty.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

try:
    from .conductor_subscriber import ConductorSubscriber
    from .const import (
        CONF_ASSET_CLASSES,
        CONF_CONDUCTOR_API_KEY,
        CONF_CONDUCTOR_WEBHOOK_ID,
        CONF_CONDUCTOR_WS_URL,
        CONF_HPWH_MAX_TEMP_C,
        CONF_HPWH_MIN_TEMP_C,
        CONF_HVAC_MAX_SETPOINT_OFFSET_C,
        CONF_HVAC_MIN_DWELL_SECONDS,
        CONF_NGANGK_API_URL,
        CONF_NODE_ID,
        CONF_POLL_INTERVAL_SECONDS,
        DEFAULT_CONDUCTOR_WEBHOOK_ID,
        DEFAULT_CONDUCTOR_WS_URL,
        DEFAULT_HPWH_MAX_TEMP_C,
        DEFAULT_HPWH_MIN_TEMP_C,
        DEFAULT_HVAC_MAX_SETPOINT_OFFSET_C,
        DEFAULT_HVAC_MIN_DWELL_SECONDS,
        DEFAULT_NGANGK_API_URL,
        DEFAULT_NODE_ID,
        DEFAULT_POLL_INTERVAL_SECONDS,
        DOMAIN,
        PLATFORMS,
    )
    from .blueprint_installer import install_blueprints
    from .coordinator import OrchestraDataUpdateCoordinator
    from .heat_pump_control import async_setup_heat_pump_control
    from .hvac_drift import async_setup_hvac_drift
    from .safety_guard import InProcessSafetyGuard
except ImportError:
    from custom_components.orchestra.conductor_subscriber import ConductorSubscriber
    from custom_components.orchestra.const import (
        CONF_ASSET_CLASSES,
        CONF_CONDUCTOR_API_KEY,
        CONF_CONDUCTOR_WEBHOOK_ID,
        CONF_CONDUCTOR_WS_URL,
        CONF_HPWH_MAX_TEMP_C,
        CONF_HPWH_MIN_TEMP_C,
        CONF_HVAC_MAX_SETPOINT_OFFSET_C,
        CONF_HVAC_MIN_DWELL_SECONDS,
        CONF_NGANGK_API_URL,
        CONF_NODE_ID,
        CONF_POLL_INTERVAL_SECONDS,
        DEFAULT_CONDUCTOR_WEBHOOK_ID,
        DEFAULT_CONDUCTOR_WS_URL,
        DEFAULT_HPWH_MAX_TEMP_C,
        DEFAULT_HPWH_MIN_TEMP_C,
        DEFAULT_HVAC_MAX_SETPOINT_OFFSET_C,
        DEFAULT_HVAC_MIN_DWELL_SECONDS,
        DEFAULT_NGANGK_API_URL,
        DEFAULT_NODE_ID,
        DEFAULT_POLL_INTERVAL_SECONDS,
        DOMAIN,
        PLATFORMS,
    )
    from custom_components.orchestra.blueprint_installer import install_blueprints
    from custom_components.orchestra.coordinator import OrchestraDataUpdateCoordinator
    from custom_components.orchestra.heat_pump_control import async_setup_heat_pump_control
    from custom_components.orchestra.hvac_drift import async_setup_hvac_drift
    from custom_components.orchestra.safety_guard import InProcessSafetyGuard

_LOGGER = logging.getLogger(__name__)


def _safe_async_create_task(hass: Any, coro: Any) -> Any:
    """Helper to dispatch async tasks across both real HA and mock test harnesses."""
    if hasattr(hass, "async_create_task") and callable(hass.async_create_task):
        return hass.async_create_task(coro)
    try:
        loop = asyncio.get_running_loop()
        return loop.create_task(coro)
    except RuntimeError:
        return None


async def _async_install_blueprints(hass: Any) -> None:
    """Copy the bundled asset automation blueprints into `<config>/blueprints/automation/orchestra`."""
    config_dir = getattr(getattr(hass, "config", None), "config_dir", None)
    if not isinstance(config_dir, str) or not hasattr(hass, "async_add_executor_job"):
        return
    try:
        written = await hass.async_add_executor_job(install_blueprints, config_dir)
    except OSError as err:
        _LOGGER.warning("Could not install Orchestra automation blueprints: %s", err)
        return
    if written:
        _LOGGER.info("Installed Orchestra automation blueprints: %s", [p.name for p in written])


async def async_setup(hass: Any, config: dict[str, Any]) -> bool:
    """
    Initialize the Orchestra hijack integration inside the Home Assistant Core event loop.
    Works both inside a real `homeassistant.core.HomeAssistant` runtime and in
    lightweight mock `hass` test harnesses.
    """
    domain_cfg = config.get(DOMAIN) or {}
    min_dwell = int(domain_cfg.get(CONF_HVAC_MIN_DWELL_SECONDS, DEFAULT_HVAC_MIN_DWELL_SECONDS))
    max_offset = float(domain_cfg.get(CONF_HVAC_MAX_SETPOINT_OFFSET_C, DEFAULT_HVAC_MAX_SETPOINT_OFFSET_C))
    min_hpwh = float(domain_cfg.get(CONF_HPWH_MIN_TEMP_C, DEFAULT_HPWH_MIN_TEMP_C))
    max_hpwh = float(domain_cfg.get(CONF_HPWH_MAX_TEMP_C, DEFAULT_HPWH_MAX_TEMP_C))
    ngangk_url = domain_cfg.get(CONF_NGANGK_API_URL, DEFAULT_NGANGK_API_URL)
    poll_interval = int(domain_cfg.get(CONF_POLL_INTERVAL_SECONDS, DEFAULT_POLL_INTERVAL_SECONDS))
    conductor_ws = domain_cfg.get(CONF_CONDUCTOR_WS_URL, DEFAULT_CONDUCTOR_WS_URL)
    conductor_wh = domain_cfg.get(CONF_CONDUCTOR_WEBHOOK_ID, DEFAULT_CONDUCTOR_WEBHOOK_ID)
    conductor_api_key = domain_cfg.get(CONF_CONDUCTOR_API_KEY)
    node_id = domain_cfg.get(CONF_NODE_ID, DEFAULT_NODE_ID)

    safety_guard = InProcessSafetyGuard(
        hvac_min_dwell_seconds=min_dwell,
        hvac_max_setpoint_offset_c=max_offset,
        hpwh_min_temp_c=min_hpwh,
        hpwh_max_temp_c=max_hpwh,
    )

    coordinator = OrchestraDataUpdateCoordinator(
        hass=hass,
        ngangk_api_url=ngangk_url,
        poll_interval_seconds=poll_interval,
        safety_guard=safety_guard,
        node_id=node_id,
    )

    subscriber = ConductorSubscriber(
        hass=hass,
        coordinator=coordinator,
        ws_url=conductor_ws,
        webhook_id=conductor_wh,
        api_key=conductor_api_key,
        node_id=node_id,
        asset_classes=domain_cfg.get(CONF_ASSET_CLASSES),
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["safety_guard"] = safety_guard
    hass.data[DOMAIN]["coordinator"] = coordinator
    hass.data[DOMAIN]["subscriber"] = subscriber

    _safe_async_create_task(hass, coordinator.async_start())
    _safe_async_create_task(hass, subscriber.async_start())

    await _async_start_setup_agent(hass)

    # `orchestra:` in configuration.yaml (as the image writes it) gets a config entry, so the
    # entities and the punch list load without anyone opening the integrations page
    entries = getattr(getattr(hass, "config_entries", None), "async_entries", None)
    if DOMAIN in config and callable(entries) and not entries(DOMAIN):
        _safe_async_create_task(
            hass,
            hass.config_entries.flow.async_init(DOMAIN, context={"source": "import"}, data={}),
        )

    return True


async def _async_start_setup_agent(hass: Any) -> None:
    """Start the setup agent where the Orchestra image left a setup plan."""
    if "setup_agent" in hass.data.get(DOMAIN, {}) or not hasattr(hass, "auth"):
        return
    try:
        from .setup_agent import OrchestraSetupAgent
    except ImportError:  # outside Home Assistant
        return
    agent = await OrchestraSetupAgent.async_load(hass)
    hass.data.setdefault(DOMAIN, {})["setup_agent"] = agent
    if agent is not None:
        agent.async_start()


def _async_register_setup_tools(hass: Any) -> None:
    """Offer the Orchestra Setup tools to Home Assistant's MCP Server (once per run)."""
    if "setup_tools_unregister" in hass.data.get(DOMAIN, {}) or not hasattr(hass, "auth"):
        return
    try:
        from .setup_tools import async_register_setup_tools
    except ImportError:  # outside Home Assistant
        return
    try:
        hass.data.setdefault(DOMAIN, {})["setup_tools_unregister"] = async_register_setup_tools(hass)
    except Exception as err:  # Home Assistant before the LLM API helpers
        _LOGGER.debug("Orchestra Setup tools not registered: %s", err)


async def async_setup_entry(hass: Any, entry: Any) -> bool:
    """Set up Orchestra from a Home Assistant ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})

    entry_data = getattr(entry, "data", {}) if not isinstance(entry, dict) else entry

    if "coordinator" not in hass.data[DOMAIN]:
        min_dwell = int(entry_data.get(CONF_HVAC_MIN_DWELL_SECONDS, DEFAULT_HVAC_MIN_DWELL_SECONDS))
        max_offset = float(entry_data.get(CONF_HVAC_MAX_SETPOINT_OFFSET_C, DEFAULT_HVAC_MAX_SETPOINT_OFFSET_C))
        min_hpwh = float(entry_data.get(CONF_HPWH_MIN_TEMP_C, DEFAULT_HPWH_MIN_TEMP_C))
        max_hpwh = float(entry_data.get(CONF_HPWH_MAX_TEMP_C, DEFAULT_HPWH_MAX_TEMP_C))

        safety_guard = InProcessSafetyGuard(
            hvac_min_dwell_seconds=min_dwell,
            hvac_max_setpoint_offset_c=max_offset,
            hpwh_min_temp_c=min_hpwh,
            hpwh_max_temp_c=max_hpwh,
        )

        coordinator = OrchestraDataUpdateCoordinator(
            hass=hass,
            ngangk_api_url=entry_data.get(CONF_NGANGK_API_URL, DEFAULT_NGANGK_API_URL),
            poll_interval_seconds=int(entry_data.get(CONF_POLL_INTERVAL_SECONDS, DEFAULT_POLL_INTERVAL_SECONDS)),
            safety_guard=safety_guard,
            node_id=entry_data.get(CONF_NODE_ID, DEFAULT_NODE_ID),
        )

        subscriber = ConductorSubscriber(
            hass=hass,
            coordinator=coordinator,
            ws_url=entry_data.get(CONF_CONDUCTOR_WS_URL, DEFAULT_CONDUCTOR_WS_URL),
            webhook_id=entry_data.get(CONF_CONDUCTOR_WEBHOOK_ID, DEFAULT_CONDUCTOR_WEBHOOK_ID),
            api_key=entry_data.get(CONF_CONDUCTOR_API_KEY),
            node_id=entry_data.get(CONF_NODE_ID, DEFAULT_NODE_ID),
            asset_classes=entry_data.get(CONF_ASSET_CLASSES),
        )

        hass.data[DOMAIN]["safety_guard"] = safety_guard
        hass.data[DOMAIN]["coordinator"] = coordinator
        hass.data[DOMAIN]["subscriber"] = subscriber

        _safe_async_create_task(hass, coordinator.async_start())
        _safe_async_create_task(hass, subscriber.async_start())

    await _async_install_blueprints(hass)
    await _async_start_setup_agent(hass)
    _async_register_setup_tools(hass)
    await async_setup_hvac_drift(hass)
    await async_setup_heat_pump_control(hass)

    if hasattr(hass, "config_entries") and hasattr(hass.config_entries, "async_forward_entry_setups"):
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: Any, entry: Any) -> bool:
    """Unload an Orchestra config entry."""
    if DOMAIN in hass.data:
        coordinator = hass.data[DOMAIN].get("coordinator")
        if coordinator:
            await coordinator.async_stop()
        subscriber = hass.data[DOMAIN].get("subscriber")
        if subscriber:
            await subscriber.async_stop()
        unregister_tools = hass.data[DOMAIN].pop("setup_tools_unregister", None)
        if unregister_tools:
            unregister_tools()

    if hasattr(hass, "config_entries") and hasattr(hass.config_entries, "async_unload_platforms"):
        return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return True
