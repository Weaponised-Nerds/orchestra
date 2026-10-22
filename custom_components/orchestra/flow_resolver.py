"""
Autonomous Hardware Flow Resolver & Dynamic Dashboard Curator for Orchestra.

Runs inside the Home Assistant Core event loop to:
1. Automatically resolve and complete discovered hardware flows without user intervention
   (e.g., Enphase Envoy serial digest derivation, Brother/IPP printers, unauthenticated media).
2. Suppress hostile cloud discovery prompts (Tuya, Sensibo, Tesla Fleet, etc.).
3. Dynamically curate the default Home Assistant Overview dashboard (.storage/lovelace)
   so newly discovered hardware is organized into clean, zero-jargon room cards.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from custom_components.orchestra.const import DOMAIN, INTERCEPTED_CLOUD_FLOW_DOMAINS

_LOGGER = logging.getLogger(__name__)


def derive_envoy_installer_password(serial_number: str) -> str:
    """Derive the deterministic local installer digest password from an Enphase Envoy serial number."""
    digits = "".join(ch for ch in str(serial_number) if ch.isdigit()) or "122300045678"
    digest_input = f"installer:enphaseenergy.com:{digits}".encode("utf-8")
    return hashlib.md5(digest_input).hexdigest()[-6:]


class AutonomousHardwareHijacker:
    """Monitors active config flows and automatically onboard discovered hardware."""

    def __init__(self, hass: Any) -> None:
        self.hass = hass
        self._running = False
        self._task: asyncio.Task | None = None
        self._processed_flows: set[str] = set()

    def start(self) -> None:
        """Start the autonomous flow resolver loop."""
        self._running = True
        if hasattr(self.hass, "async_create_background_task"):
            self._task = self.hass.async_create_background_task(
                self._hijacker_loop(), "orchestra_autonomous_hardware_hijacker"
            )
        elif hasattr(self.hass, "loop"):
            self._task = self.hass.loop.create_task(self._hijacker_loop())

    def stop(self) -> None:
        """Stop the loop."""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()

    async def _hijacker_loop(self) -> None:
        """Periodic loop that sweeps active config flows and synchronizes the dashboard."""
        # Initial settle delay for Home Assistant startup
        await asyncio.sleep(5)

        while self._running:
            try:
                await self.sweep_and_resolve_flows()
                await self.sync_default_dashboard()
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.warning("[Orchestra Hijacker] Error during autonomous sweep: %s", err)

            await asyncio.sleep(10)

    async def sweep_and_resolve_flows(self) -> int:
        """Inspect all pending discovery flows in Home Assistant and auto-advance them."""
        if not hasattr(self.hass, "config_entries") or not hasattr(self.hass.config_entries, "flow"):
            return 0

        flows = self.hass.config_entries.flow.async_progress()
        resolved_count = 0

        for flow in flows:
            flow_id = flow.get("flow_id")
            if not flow_id or flow_id in self._processed_flows:
                continue

            domain = flow.get("handler") or flow.get("domain") or ""
            step_id = flow.get("step_id") or ""
            context = flow.get("context", {})

            # 1. Abort hostile cloud-tethered flows
            if domain in INTERCEPTED_CLOUD_FLOW_DOMAINS:
                _LOGGER.info(
                    "[Orchestra Hijacker] Aborting hostile cloud flow for '%s' (flow_id=%s)",
                    domain,
                    flow_id,
                )
                try:
                    self.hass.config_entries.flow.async_abort(flow_id)
                    self._processed_flows.add(flow_id)
                    resolved_count += 1
                except Exception as e:
                    _LOGGER.debug("Could not abort flow %s: %s", flow_id, e)
                continue

            # 2. Auto-resolve Enphase Envoy Solar/Storage Gateway
            if domain == "enphase_envoy":
                success = await self._resolve_enphase_flow(flow_id, flow)
                if success:
                    self._processed_flows.add(flow_id)
                    resolved_count += 1
                continue

            # 3. Auto-confirm Network Printers (Brother, IPP, SNMP)
            if domain in ("brother", "ipp", "snmp"):
                success = await self._confirm_standard_flow(flow_id, domain)
                if success:
                    self._processed_flows.add(flow_id)
                    resolved_count += 1
                continue

            # 4. Auto-confirm unauthenticated media / entertainment devices
            if domain in ("apple_tv", "cast", "sonos", "dlna_dmr"):
                # Only auto-confirm if it's a confirmation step (no PIN required)
                if step_id in ("confirm", "zeroconf_confirm", "discovery_confirm"):
                    success = await self._confirm_standard_flow(flow_id, domain)
                    if success:
                        self._processed_flows.add(flow_id)
                        resolved_count += 1
                continue

        return resolved_count

    async def _resolve_enphase_flow(self, flow_id: str, flow: dict[str, Any]) -> bool:
        """Derive installer credentials and submit Enphase Envoy flow silently."""
        context = flow.get("context", {})
        title_placeholders = context.get("title_placeholders", {})
        desc_placeholders = flow.get("description_placeholders", {})

        serial = (
            title_placeholders.get("serial")
            or desc_placeholders.get("serial")
            or context.get("unique_id")
            or "122206103673"
        )
        serial = "".join(ch for ch in str(serial) if ch.isdigit())
        installer_password = derive_envoy_installer_password(serial)

        _LOGGER.info(
            "[Orchestra Hijacker] Auto-authenticating Enphase Envoy (serial %s) with derived local installer password",
            serial,
        )

        try:
            res = await self.hass.config_entries.flow.async_configure(
                flow_id,
                user_input={
                    "username": "installer",
                    "password": installer_password,
                },
            )
            _LOGGER.info("[Orchestra Hijacker] Enphase Envoy flow result: %s", res.get("type"))
            return True
        except Exception as e:
            _LOGGER.warning("[Orchestra Hijacker] Failed to auto-configure Enphase Envoy: %s", e)
            return False

    async def _confirm_standard_flow(self, flow_id: str, domain: str) -> bool:
        """Confirm a zero-credential discovery flow."""
        _LOGGER.info("[Orchestra Hijacker] Auto-approving discovered '%s' integration", domain)
        try:
            res = await self.hass.config_entries.flow.async_configure(flow_id, user_input={})
            _LOGGER.info("[Orchestra Hijacker] Flow '%s' confirmed: %s", domain, res.get("type"))
            return True
        except Exception as e:
            _LOGGER.debug("Could not confirm flow %s: %s", flow_id, e)
            return False

    async def sync_default_dashboard(self) -> None:
        """
        Dynamically categorize visible entities and ensure the default Overview dashboard
        (.storage/lovelace) has clean, curated cards with technical noise hidden.
        """
        if not hasattr(self.hass, "states"):
            return

        all_states = self.hass.states.async_all()
        if not all_states:
            return

        media_entities: list[str] = []
        climate_entities: list[str] = []
        light_switch_entities: list[str] = []
        printer_entities: list[str] = []
        solar_power_entities: list[str] = []

        for st in all_states:
            eid = st.entity_id
            domain = eid.split(".")[0]

            # Classify consumer entities
            if domain == "media_player":
                media_entities.append(eid)
            elif domain == "climate" or domain == "water_heater":
                climate_entities.append(eid)
            elif domain in ("light", "switch") and not eid.startswith("switch.orchestra_"):
                light_switch_entities.append(eid)
            elif "toner" in eid or "printer" in eid or "hl_l3230cdw" in eid:
                printer_entities.append(eid)
            elif (
                "production" in eid
                or "consumption" in eid
                or "power" in eid
                or "solar" in eid
                or "battery" in eid
            ) and domain == "sensor":
                if any(k in eid for k in ("current_power", "grid", "soc", "production", "consumption")):
                    solar_power_entities.append(eid)

        # Build cards
        cards: list[dict[str, Any]] = [
            {
                "type": "markdown",
                "title": "Welcome Home — Orchestra Energy Concierge Active",
                "content": (
                    "**Everything is running smoothly and automatically.**\n"
                    "- Household comfort and energy efficiency are actively managed.\n"
                    "- All your devices are organized below without diagnostic clutter."
                ),
            }
        ]

        if light_switch_entities:
            cards.append(
                {
                    "type": "entities",
                    "title": "Living Spaces & Lights",
                    "show_header_toggle": True,
                    "entities": light_switch_entities[:8],
                }
            )

        if media_entities:
            cards.append(
                {
                    "type": "entities",
                    "title": "Music & Entertainment",
                    "entities": media_entities[:6],
                }
            )

        if climate_entities:
            for c_ent in climate_entities[:2]:
                cards.append(
                    {
                        "type": "thermostat" if c_ent.startswith("climate.") else "entities",
                        "entity": c_ent,
                        "name": "Home Climate",
                    }
                )

        if solar_power_entities:
            cards.append(
                {
                    "type": "entities",
                    "title": "Solar & Grid Energy",
                    "entities": solar_power_entities[:6],
                }
            )

        if printer_entities:
            cards.append(
                {
                    "type": "entities",
                    "title": "Office & Equipment",
                    "entities": printer_entities[:5],
                }
            )

        # Inject into .storage/lovelace (Default Dashboard)
        storage_path = Path(self.hass.config.path(".storage/lovelace")) if hasattr(self.hass, "config") else None
        if not storage_path:
            return

        default_dashboard_payload = {
            "version": 1,
            "minor_version": 1,
            "key": "lovelace",
            "data": {
                "config": {
                    "title": "My Home",
                    "views": [
                        {
                            "title": "My Home",
                            "path": "default_view",
                            "icon": "mdi:home-heart",
                            "badges": [],
                            "cards": cards,
                        }
                    ],
                }
            },
        }

        try:
            current_raw = storage_path.read_text(encoding="utf-8") if storage_path.exists() else ""
            new_raw = json.dumps(default_dashboard_payload, indent=2)
            # Only rewrite if changed significantly
            if len(new_raw) != len(current_raw):
                storage_path.write_text(new_raw, encoding="utf-8")
                _LOGGER.info("[Orchestra Hijacker] Synchronized default Grandpa dashboard (%d cards)", len(cards))
        except Exception as e:
            _LOGGER.debug("[Orchestra Hijacker] Could not sync .storage/lovelace: %s", e)
