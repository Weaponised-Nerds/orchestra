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
import json
import logging
from typing import Any

from custom_components.orchestra.const import (
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
    ) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self.ws_url = ws_url
        self.webhook_id = webhook_id
        self.api_key = api_key
        self.node_id = node_id

        self._ws_task: asyncio.Task | None = None
        self._is_stopped = False
        self._webhook_registered = False
        self.last_ack_payload: dict[str, Any] | None = None

    async def async_start(self) -> None:
        """Start the WebSocket broadcast listener loop and register local webhook handler."""
        self._is_stopped = False
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
        """
        while not self._is_stopped:
            try:
                connected = await self._try_connect_and_listen_ws()
                if not connected:
                    await asyncio.sleep(60)
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.debug("Conductor broadcast WebSocket subscription error: %s", err)
                await asyncio.sleep(15)

    async def _try_connect_and_listen_ws(self) -> bool:
        """Helper to subscribe to broadcast WebSocket if client library is installed."""
        try:
            import websockets  # type: ignore

            headers = {}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            async with websockets.connect(self.ws_url, extra_headers=headers) as ws:
                _LOGGER.info("Subscribed to anonymous Conductor market broadcast at %s", self.ws_url)
                # Anonymous subscription request
                sub_frame = {
                    "action": "subscribe",
                    "channel": "swis_market_broadcast",
                }
                await ws.send(json.dumps(sub_frame))

                async for msg in ws:
                    if self._is_stopped:
                        break
                    try:
                        data = json.loads(msg)
                        self.process_inbound_dispatch(data)
                    except Exception as err:
                        _LOGGER.warning("Error processing Conductor broadcast frame: %s", err)
                return True
        except ImportError:
            return False
        except Exception as exc:
            _LOGGER.debug("Could not reach Conductor broadcast endpoint: %s", exc)
            return False

