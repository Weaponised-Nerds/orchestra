"""
In-Process Hardware Safety Guardrail Interceptor for Home Assistant.

Enforces Pitfall 3 mitigations from `ZENITH_MICRO_NODE_ARCHITECTURE.md`:
1. Minimum HVAC compressor dwell time (default 600s / 10 minutes) between state transitions.
2. Setpoint-offset-only HVAC modulation (clamping single-step demand response shifts to <= 2.0°C).
3. Heat Pump Hot Water thermal storage bounds (50.0°C coast minimum to 65.0°C solar boost max).
"""

from __future__ import annotations

import time
from typing import Any


class HardwareSafetyInterlockError(RuntimeError):
    """Raised when a service call violates hard-coded appliance warranty guardrails."""


class InProcessSafetyGuard:
    """Tracks appliance transition timestamps and validates HA service calls before execution."""

    def __init__(
        self,
        hvac_min_dwell_seconds: int = 600,
        hvac_max_setpoint_offset_c: float = 2.0,
        hpwh_min_temp_c: float = 50.0,
        hpwh_max_temp_c: float = 65.0,
    ) -> None:
        self.hvac_min_dwell_seconds = hvac_min_dwell_seconds
        self.hvac_max_setpoint_offset_c = hvac_max_setpoint_offset_c
        self.hpwh_min_temp_c = hpwh_min_temp_c
        self.hpwh_max_temp_c = hpwh_max_temp_c
        self._last_hvac_transition_monotonic: dict[str, float] = {}
        self._last_hvac_setpoint_c: dict[str, float] = {}

    def validate_climate_service_call(
        self,
        entity_id: str,
        service: str,
        service_data: dict[str, Any],
        now_monotonic: float | None = None,
    ) -> dict[str, Any]:
        """
        Validate and clamp a `climate.*` service call.
        - Rejects compressor state toggles that violate the 15-minute dwell timer.
        - Clamps automated demand-response temperature nudges to `hvac_max_setpoint_offset_c`.
        """
        now = now_monotonic if now_monotonic is not None else time.monotonic()

        if service in ("turn_off", "turn_on", "set_hvac_mode"):
            last_ts = self._last_hvac_transition_monotonic.get(entity_id)
            if last_ts is not None and (now - last_ts) < self.hvac_min_dwell_seconds:
                remaining = int(self.hvac_min_dwell_seconds - (now - last_ts))
                raise HardwareSafetyInterlockError(
                    f"HVAC short-cycling protection active for {entity_id}: "
                    f"{remaining}s remaining in {self.hvac_min_dwell_seconds}s minimum dwell window."
                )
            self._last_hvac_transition_monotonic[entity_id] = now

        if service == "set_temperature" and "temperature" in service_data:
            requested_temp = float(service_data["temperature"])
            prev_temp = self._last_hvac_setpoint_c.get(entity_id)
            if prev_temp is not None and service_data.get("conductor_dr_event", False):
                delta = requested_temp - prev_temp
                if abs(delta) > self.hvac_max_setpoint_offset_c:
                    clamped = prev_temp + (
                        self.hvac_max_setpoint_offset_c if delta > 0 else -self.hvac_max_setpoint_offset_c
                    )
                    service_data = dict(service_data)
                    service_data["temperature"] = round(clamped, 1)
            self._last_hvac_setpoint_c[entity_id] = float(service_data["temperature"])

        return service_data

    def validate_water_heater_service_call(
        self,
        entity_id: str,
        service: str,
        service_data: dict[str, Any],
    ) -> dict[str, Any]:
        """Ensure Heat Pump Hot Water setpoints remain within [50°C, 65°C] thermal storage bounds."""
        if service == "set_temperature" and "temperature" in service_data:
            temp = float(service_data["temperature"])
            clamped = max(self.hpwh_min_temp_c, min(self.hpwh_max_temp_c, temp))
            service_data = dict(service_data)
            service_data["temperature"] = clamped
        return service_data
