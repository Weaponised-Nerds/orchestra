"""
Privacy-Preserving Conductor Broadcast Signal Subscriber for Home Assistant.

Conductor operates as an anonymous broadcast publisher for the Western Australian WEM.
It does NOT address homes directly, track individual users, or collect private household telemetry.
Homes connect as anonymous subscribers to regional market signals and asset class recommendations.
Conductor observes aggregate response directly on the electrical grid (via WEM Operational Demand MW,
system frequency, and clearing price feedback) and dynamically adjusts its broadcast signals.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Any

try:
    from .const import (
        DEFAULT_ASSET_CLASSES,
        DEFAULT_CONDUCTOR_WEBHOOK_ID,
        DEFAULT_CONDUCTOR_WS_URL,
        DOMAIN,
    )
    from .coordinator import OrchestraDataUpdateCoordinator
except ImportError:
    from custom_components.orchestra.const import (
        DEFAULT_ASSET_CLASSES,
        DEFAULT_CONDUCTOR_WEBHOOK_ID,
        DEFAULT_CONDUCTOR_WS_URL,
        DOMAIN,
    )
    from custom_components.orchestra.coordinator import OrchestraDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class ConductorSubscriber:
    """
    Subscribes anonymously to Conductor market broadcast signals via WebSocket
    and exposes a local Webhook ingress for HTTP POST signals.
    Guarantees zero collection or transmission of private household data.
    """

    def __init__(
        self,
        hass: Any,
        coordinator: OrchestraDataUpdateCoordinator,
        ws_url: str = DEFAULT_CONDUCTOR_WS_URL,
        webhook_id: str = DEFAULT_CONDUCTOR_WEBHOOK_ID,
        api_key: str | None = None,
        node_id: str = "anonymous_subscriber",
        asset_classes: list[str] | tuple[str, ...] | None = None,
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.ws_url = ws_url
        self.webhook_id = webhook_id
        self.api_key = api_key
        self.node_id = node_id
        self.asset_classes = [a for a in (asset_classes or DEFAULT_ASSET_CLASSES) if a in DEFAULT_ASSET_CLASSES]

        self._ws_task: asyncio.Task | None = None
        self._is_stopped = False
        self._webhook_registered = False
        self.last_ack_payload: dict[str, Any] | None = None

    async def async_start(self) -> None:
        """Start the WebSocket broadcast listener loop and register local webhook handler."""
        self._is_stopped = False
        if self.coordinator.cohort is None:
            self.coordinator.cohort = await self._resolve_cohort()
        self._register_webhook_if_available()

        if self.ws_url and self._ws_task is None:
            self._ws_task = asyncio.create_task(self._ws_client_loop())

    async def async_stop(self) -> None:
        """Stop subscriber and unregister webhook."""
        self._is_stopped = True
        self._unregister_webhook_if_available()
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
            self._ws_task = None

    async def _resolve_cohort(self) -> int:
        """
        Stable anonymous cohort 0-99, derived from the HA installation id (or the node id).
        Conductor targets tranches by cohort range and never learns which home is which.
        """
        seed: str | None = None
        try:
            from homeassistant.helpers import instance_id

            seed = await instance_id.async_get(self.hass)
        except Exception:  # noqa: BLE001 - outside HA or instance id unavailable
            seed = None
        if not seed:
            seed = self.node_id
        digest = hashlib.sha256(f"orchestra-cohort:{seed}".encode("utf-8")).hexdigest()
        return int(digest, 16) % 100

    def _register_webhook_if_available(self) -> None:
        """Register HA Webhook endpoint `/api/webhook/<webhook_id>`."""
        if hasattr(self.hass, "components") and hasattr(self.hass.components, "webhook"):
            try:
                self.hass.components.webhook.async_register(
                    DOMAIN,
                    "Orchestra Conductor Dispatch Ingress",
                    self.webhook_id,
                    self.handle_webhook_request,
                )
                self._webhook_registered = True
                _LOGGER.info(
                    "Registered Orchestra Conductor webhook: /api/webhook/%s",
                    self.webhook_id,
                )
            except Exception as err:
                _LOGGER.debug("Could not register HA webhook: %s", err)

    def _unregister_webhook_if_available(self) -> None:
        """Unregister HA Webhook endpoint."""
        if self._webhook_registered and hasattr(self.hass, "components") and hasattr(self.hass.components, "webhook"):
            try:
                self.hass.components.webhook.async_unregister(self.webhook_id)
                self._webhook_registered = False
            except Exception as err:
                _LOGGER.debug("Error unregistering HA webhook: %s", err)

    async def handle_webhook_request(self, hass: Any, webhook_id: str, request: Any) -> Any:
        """
        Processes HTTP POST broadcast signals sent directly to
        `/api/webhook/orchestra_conductor_dispatch`.
        """
        try:
            if hasattr(request, "json"):
                payload = await request.json()
            elif hasattr(request, "text"):
                body = await request.text()
                payload = json.loads(body)
            elif isinstance(request, dict):
                payload = request
            else:
                payload = {}
        except Exception as exc:
            _LOGGER.error("Failed to parse incoming Conductor broadcast JSON: %s", exc)
            return {"error": "invalid_json"}

        return self.process_inbound_dispatch(payload)

    def process_inbound_dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Validates and applies an inbound Conductor broadcast message.
        Enforces privacy: no private home energy usage or PII is returned.
        """
        # Acks, pongs and other control frames carry no dispatch
        if "control_signal" not in payload:
            return {"status": "ignored", "reason": "not_a_dispatch"}

        # Validate optional API key if configured
        if self.api_key:
            auth_header = payload.get("auth_token") or payload.get("api_key")
            if auth_header != self.api_key:
                return {"status": "unauthorized", "reason": "invalid_api_key"}

        result = self.coordinator.apply_conductor_dispatch(payload)

        # Anonymous acknowledgment: no personal household telemetry
        ack = {
            "status": result.get("status"),
            "dispatch_id": result.get("dispatch_id"),
            "reason": result.get("reason", ""),
            "control_signal": result.get("control_signal"),
            "safety_adjustments": result.get("safety_adjustments", []),
            "opted_out": self.coordinator.data.dispatch.opted_out,
        }
        self.last_ack_payload = ack
        return ack

    async def _ws_client_loop(self) -> None:
        """
        Anonymous WebSocket broadcast subscriber loop. Listens to regional WEM feed.
        Enforces Item 3.4: on WAN disconnect or link failure, reverts to Normal/Auto.
        """
        while not self._is_stopped:
            try:
                connected = await self._try_connect_and_listen_ws()
                if not connected and not self._is_stopped:
                    # Item 3.4 WAN Outage: immediately revert to Normal/Auto
                    self.coordinator.apply_wan_fallback_normal(
                        "Conductor broadcast connection lost. Reverted to standard schedule."
                    )
                    await asyncio.sleep(30)
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.debug("Conductor broadcast WebSocket subscription error: %s", err)
                if not self._is_stopped:
                    self.coordinator.apply_wan_fallback_normal(
                        "Conductor broadcast error. Reverted to standard schedule."
                    )
                await asyncio.sleep(15)

    async def _try_connect_and_listen_ws(self) -> bool:
        """Helper to subscribe to broadcast WebSocket via aiohttp or websockets."""
        headers = {}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        # Anonymous capability declaration: lets Conductor size a tranche, nothing more
        sub_frame = json.dumps({
            "action": "subscribe",
            "channel": "swis_market_broadcast",
            "assets": self.asset_classes,
            "cohort": self.coordinator.cohort,
        })

        # 1. Primary: Try Home Assistant native aiohttp client
        try:
            import aiohttp

            session = None
            if hasattr(self.hass, "helpers") and hasattr(self.hass.helpers, "aiohttp_client"):
                session = self.hass.helpers.aiohttp_client.async_get_clientsession(self.hass)
            elif hasattr(self.hass, "data") and "aiohttp_session" in self.hass.data:
                session = self.hass.data["aiohttp_session"]

            close_session = False
            if session is None or session.closed:
                session = aiohttp.ClientSession()
                close_session = True

            try:
                async with session.ws_connect(self.ws_url, headers=headers, heartbeat=30.0) as ws:
                    _LOGGER.info("Subscribed to anonymous Conductor market broadcast at %s", self.ws_url)
                    await ws.send_str(sub_frame)

                    async for msg in ws:
                        if self._is_stopped:
                            break
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            try:
                                data = json.loads(msg.data)
                                self.process_inbound_dispatch(data)
                            except Exception as err:
                                _LOGGER.warning("Error processing Conductor broadcast frame: %s", err)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                            break
                    return True
            finally:
                if close_session and not session.closed:
                    await session.close()

        except (ImportError, Exception) as exc:
            _LOGGER.debug("aiohttp WebSocket connection to %s failed: %s; trying fallback", self.ws_url, exc)

        # 2. Secondary fallback: Try `websockets` library if installed
        try:
            import websockets  # type: ignore

            async with websockets.connect(self.ws_url, extra_headers=headers) as ws:
                _LOGGER.info("Subscribed to anonymous Conductor market broadcast via websockets at %s", self.ws_url)
                await ws.send(sub_frame)

                async for msg in ws:
                    if self._is_stopped:
                        break
                    try:
                        data = json.loads(msg)
                        self.process_inbound_dispatch(data)
                    except Exception as err:
                        _LOGGER.warning("Error processing Conductor broadcast frame: %s", err)
                return True
        except (ImportError, Exception) as exc:
            _LOGGER.debug("Could not reach Conductor broadcast endpoint: %s", exc)
            return False

