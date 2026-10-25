"""
In-Process Home Assistant Custom Component (`custom_components/orchestra`).

Loaded automatically by `configuration.yaml` (`orchestra:`) or Config Flow.
1. Queries the Ngangk WEM Predictive Energy Engine for real-time 30-minute SWIS
   operational demand, wholesale price RTP, and renewable availability.
2. Manages Conductor demand-response control signals (`Normal/Auto`, `Energy Soak`,
   `Load Shed`) and publishes granular asset directives (EV charge now, Heat Pump
   run now, A/C pre-cool/pre-heat, Home battery storm watch).
3. Subscribes to Conductor push events via outbound WebSocket client and local Webhook.
4. Aborts cloud-tethered discovery flows (`tuya`, `sensibo`, `tesla_fleet`, etc.)
   and triggers Orchestra's local zero-cloud onboarding pipeline instead.
5. Enforces hard-coded compressor dwell (15-min) and setpoint offset guardrails on the HA bus.
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
        INTERCEPTED_CLOUD_FLOW_DOMAINS,
        PLATFORMS,
    )
    from .blueprint_installer import install_blueprints
    from .coordinator import OrchestraDataUpdateCoordinator
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
        INTERCEPTED_CLOUD_FLOW_DOMAINS,
        PLATFORMS,
    )
    from custom_components.orchestra.blueprint_installer import install_blueprints
    from custom_components.orchestra.coordinator import OrchestraDataUpdateCoordinator
    from custom_components.orchestra.safety_guard import InProcessSafetyGuard
try:
    from orchestra.config import OrchestraConfig
    from orchestra.orchestrator import OrchestraEngine
except ImportError:
    OrchestraConfig = None  # type: ignore
    OrchestraEngine = None  # type: ignore

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
    domain_cfg = config.get(DOMAIN, {})
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

    if OrchestraConfig is not None and OrchestraEngine is not None:
        orch_config = OrchestraConfig()
        orch_config.safety.hvac_min_dwell_seconds = min_dwell
        orch_config.safety.hvac_max_setpoint_offset_c = max_offset
        engine = OrchestraEngine(orch_config)
    else:
        engine = None
    safety_guard = InProcessSafetyGuard(
        hvac_min_dwell_seconds=min_dwell,
        hvac_max_setpoint_offset_c=max_offset,
        hpwh_min_temp_c=min_hpwh,
        hpwh_max_temp_c=max_hpwh,
    )

    try:
        from .flow_resolver import AutonomousHardwareHijacker
    except ImportError:
        from custom_components.orchestra.flow_resolver import AutonomousHardwareHijacker

    hardware_hijacker = AutonomousHardwareHijacker(hass)
    hardware_hijacker.start()

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
    hass.data[DOMAIN]["engine"] = engine
    hass.data[DOMAIN]["safety_guard"] = safety_guard
    hass.data[DOMAIN]["hardware_hijacker"] = hardware_hijacker
    hass.data[DOMAIN]["coordinator"] = coordinator
    hass.data[DOMAIN]["subscriber"] = subscriber
    hass.data[DOMAIN]["aborted_cloud_flows"] = []

    _safe_async_create_task(hass, coordinator.async_start())
    _safe_async_create_task(hass, subscriber.async_start())

    async def _on_flow_initiated(event: Any) -> None:
        """Intercept cloud discovery flows and auto-resolve hardware flows."""
        data = getattr(event, "data", event if isinstance(event, dict) else {})
        flow_domain = data.get("handler") or data.get("domain")
        flow_id = data.get("flow_id")
        if flow_domain in INTERCEPTED_CLOUD_FLOW_DOMAINS:
            _LOGGER.info(
                "Orchestra hijacked and suppressed cloud config flow for domain '%s' (flow_id=%s)",
                flow_domain,
                flow_id,
            )
            hass.data[DOMAIN]["aborted_cloud_flows"].append(
                {"domain": flow_domain, "flow_id": flow_id}
            )
            if hasattr(hass, "config_entries") and hasattr(hass.config_entries, "flow"):
                try:
                    hass.config_entries.flow.async_abort(flow_id)
                except Exception:
                    pass
        else:
            # Trigger immediate sweep to auto-resolve discovered hardware
            if hardware_hijacker:
                _safe_async_create_task(hass, hardware_hijacker.sweep_and_resolve_flows())

    if hasattr(hass, "bus") and hasattr(hass.bus, "async_listen"):
        hass.bus.async_listen("config_entry_flow_initiated", _on_flow_initiated)

    return True


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

    if hasattr(hass, "config_entries") and hasattr(hass.config_entries, "async_unload_platforms"):
        return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    return True
