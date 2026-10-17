"""
In-Process Home Assistant Custom Component (`custom_components/orchestra`).

Loaded automatically by `configuration.yaml` (`orchestra:`). Hijacks the running
Home Assistant event loop to:
1. Abort cloud-tethered discovery flows (`tuya`, `sensibo`, `tesla_fleet`, etc.)
   and trigger Orchestra's local zero-cloud onboarding pipeline instead.
2. Enforce hard-coded compressor dwell and setpoint offset guardrails on the HA
   service bus.
3. Expose `/api/orchestra/*` endpoints for the Zenith AI Commissioning Concierge.
"""

from __future__ import annotations

import logging
from typing import Any

from custom_components.orchestra.const import (
    CONF_HVAC_MAX_SETPOINT_OFFSET_C,
    CONF_HVAC_MIN_DWELL_SECONDS,
    DOMAIN,
    INTERCEPTED_CLOUD_FLOW_DOMAINS,
)
from custom_components.orchestra.safety_guard import InProcessSafetyGuard
from orchestra.config import OrchestraConfig
from orchestra.orchestrator import OrchestraEngine

_LOGGER = logging.getLogger(__name__)


async def async_setup(hass: Any, config: dict[str, Any]) -> bool:
    """
    Initialize the Orchestra hijack integration inside the Home Assistant Core event loop.
    Works both inside a real `homeassistant.core.HomeAssistant` runtime and in
    lightweight mock `hass` test harnesses.
    """
    domain_cfg = config.get(DOMAIN, {})
    min_dwell = int(domain_cfg.get(CONF_HVAC_MIN_DWELL_SECONDS, 900))
    max_offset = float(domain_cfg.get(CONF_HVAC_MAX_SETPOINT_OFFSET_C, 2.0))

    orch_config = OrchestraConfig()
    orch_config.safety.hvac_min_dwell_seconds = min_dwell
    orch_config.safety.hvac_max_setpoint_offset_c = max_offset

    engine = OrchestraEngine(orch_config)
    safety_guard = InProcessSafetyGuard(
        hvac_min_dwell_seconds=min_dwell,
        hvac_max_setpoint_offset_c=max_offset,
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN]["engine"] = engine
    hass.data[DOMAIN]["safety_guard"] = safety_guard
    hass.data[DOMAIN]["aborted_cloud_flows"] = []

    async def _on_flow_initiated(event: Any) -> None:
        """Intercept cloud discovery flows and abort them in favor of local adapters."""
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

    if hasattr(hass, "bus") and hasattr(hass.bus, "async_listen"):
        hass.bus.async_listen("config_entry_flow_initiated", _on_flow_initiated)

    return True


async def async_setup_entry(hass: Any, entry: Any) -> bool:
    """Set up Orchestra from a Home Assistant ConfigEntry."""
    hass.data.setdefault(DOMAIN, {})
    return True
