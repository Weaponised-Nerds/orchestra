"""
Orchestra setup assistant: a Claude agent that runs inside Home Assistant.

The homeowner chats with it in the Orchestra drawer (on every HA page, including the
Companion app) or the "Setup assistant" sidebar panel. Nothing else needs installing: the
agent loop runs here, calls the Orchestra Setup tools (`setup_tools.py`) in-process, and
sends the conversation to Conductor's relay, which holds the API key and the per-box limits.

Conversations live in memory only, per HA user, and are never stored on disk or by Conductor.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable
import uuid

import aiohttp

from homeassistant.components import websocket_api
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import llm
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .schema import to_openapi, vol
from .setup_tools import SETUP_API_ID, SETUP_PROMPT

_LOGGER = logging.getLogger(__name__)

RELAY_URL = "https://conductor.deezlepower.com/api/orchestra/agent/messages"
BOX_STORE_KEY = f"{DOMAIN}.agent"
MAX_TOOL_ROUNDS = 15
MAX_IMAGES = 4
RELAY_TIMEOUT = aiohttp.ClientTimeout(total=300)

SYSTEM_PROMPT = (
    SETUP_PROMPT
    + " You are the Orchestra setup assistant, shown in a chat drawer inside Home Assistant. "
    "Keep replies short: a few sentences, plain words, no tables. The person can attach "
    "photos from their phone camera. When there is nothing left to do, say so."
)


class OrchestraChatAgent:
    """Runs one conversation per Home Assistant user."""

    def __init__(self, hass: HomeAssistant, box_id: str) -> None:
        self.hass = hass
        self.box_id = box_id
        self._conversations: dict[str, list[dict[str, Any]]] = {}
        self._transcripts: dict[str, list[dict[str, Any]]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    @classmethod
    async def async_load(cls, hass: HomeAssistant) -> OrchestraChatAgent:
        store: Store = Store(hass, 1, BOX_STORE_KEY)
        data = await store.async_load() or {}
        if not data.get("box_id"):
            data["box_id"] = uuid.uuid4().hex
            await store.async_save(data)
        return cls(hass, data["box_id"])

    def transcript(self, user_id: str) -> list[dict[str, Any]]:
        return self._transcripts.get(user_id, [])

    def reset(self, user_id: str) -> None:
        self._conversations.pop(user_id, None)
        self._transcripts.pop(user_id, None)

    async def async_send(
        self,
        user_id: str,
        text: str,
        images: list[dict[str, str]],
        on_event: Callable[[dict[str, Any]], None],
    ) -> None:
        """Add the person's message, then run the agent until it answers."""
        lock = self._locks.setdefault(user_id, asyncio.Lock())
        async with lock:
            messages = self._conversations.setdefault(user_id, [])
            transcript = self._transcripts.setdefault(user_id, [])
            content: list[dict[str, Any]] = [
                {"type": "image", "source": {"type": "base64", "media_type": i["media_type"], "data": i["data"]}}
                for i in images[:MAX_IMAGES]
            ]
            content.append({"type": "text", "text": text or "(photo attached)"})
            messages.append({"role": "user", "content": content})
            transcript.append({"role": "user", "text": text, "photos": len(images[:MAX_IMAGES])})

            llm_context = llm.LLMContext(
                platform=DOMAIN,
                context=Context(user_id=user_id),
                language=self.hass.config.language,
                assistant=None,
                device_id=None,
            )
            api = await llm.async_get_api(self.hass, SETUP_API_ID, llm_context)
            tools = {tool.name: tool for tool in api.tools}
            tool_defs = [
                {"name": t.name, "description": t.description or "", "input_schema": to_openapi(t.parameters)}
                for t in api.tools
            ]

            for _ in range(MAX_TOOL_ROUNDS):
                try:
                    reply = await self._async_call_relay(messages, tool_defs)
                except RelayError as err:
                    messages.pop()  # let the person try the same message again
                    transcript.pop()
                    event = {"type": "error", "text": str(err)}
                    transcript.append({"role": "error", "text": str(err)})
                    on_event(event)
                    return

                messages.append({"role": "assistant", "content": reply.get("content", [])})
                for block in reply.get("content", []):
                    if block.get("type") == "text" and block.get("text", "").strip():
                        transcript.append({"role": "assistant", "text": block["text"]})
                        on_event({"type": "text", "text": block["text"]})

                if reply.get("stop_reason") != "tool_use":
                    if reply.get("stop_reason") == "refusal":
                        on_event({"type": "error", "text": "The assistant couldn't help with that one."})
                    return

                results = []
                for block in reply.get("content", []):
                    if block.get("type") != "tool_use":
                        continue
                    on_event({"type": "tool", "name": block["name"]})
                    results.append(await self._async_run_tool(tools, block, llm_context))
                messages.append({"role": "user", "content": results})

            on_event({"type": "error", "text": "That took too many steps. Try asking again in smaller pieces."})

    async def _async_run_tool(
        self, tools: dict[str, llm.Tool], block: dict[str, Any], llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        tool = tools.get(block["name"])
        result: Any
        is_error = False
        if tool is None:
            result, is_error = {"error": f"Unknown tool {block['name']}"}, True
        else:
            try:
                args = tool.parameters(block.get("input") or {})
                result = await tool.async_call(
                    self.hass, llm.ToolInput(tool_name=tool.name, tool_args=args), llm_context
                )
                if hasattr(result, "data"):  # a ToolResult (Home Assistant 2026.10)
                    result, is_error = result.data, bool(result.error)
            except vol.Invalid as err:
                result, is_error = {"error": f"Invalid arguments: {err}"}, True
            except Exception as err:  # a tool must never end the conversation
                _LOGGER.exception("Orchestra setup tool %s failed", block["name"])
                result, is_error = {"error": str(err)}, True
        return {
            "type": "tool_result",
            "tool_use_id": block["id"],
            "content": json.dumps(result, ensure_ascii=False, default=str),
            "is_error": is_error,
        }

    async def _async_call_relay(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict[str, Any]:
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                RELAY_URL,
                json={"system": SYSTEM_PROMPT, "tools": tools, "messages": messages},
                headers={"X-Orchestra-Box": self.box_id},
                timeout=RELAY_TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
                if resp.status != 200:
                    detail = body.get("error") if isinstance(body, dict) else None
                    if isinstance(detail, dict):
                        detail = detail.get("message")
                    raise RelayError(detail or f"The assistant is unavailable right now ({resp.status}).")
                return body
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise RelayError("Couldn't reach the Orchestra assistant. Check the internet connection and try again.") from err


class RelayError(Exception):
    """The relay or the model could not answer."""


# ---------------------------------------------------------------------- websocket API


@callback
def async_register_websocket_commands(hass: HomeAssistant) -> None:
    websocket_api.async_register_command(hass, ws_status)
    websocket_api.async_register_command(hass, ws_history)
    websocket_api.async_register_command(hass, ws_reset)
    websocket_api.async_register_command(hass, ws_send)
    websocket_api.async_register_command(hass, ws_claim)


def _agent(hass: HomeAssistant) -> OrchestraChatAgent | None:
    return hass.data.get(DOMAIN, {}).get("chat_agent")


@websocket_api.websocket_command({vol.Required("type"): "orchestra/agent/status"})
@websocket_api.require_admin
@callback
def ws_status(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    setup_agent = hass.data.get(DOMAIN, {}).get("setup_agent")
    open_items = len(setup_agent.open_items) if setup_agent is not None else 0
    waiting = len(hass.config_entries.flow.async_progress())
    connection.send_result(
        msg["id"],
        {
            "available": _agent(hass) is not None,
            "open_items": open_items,
            "waiting_devices": waiting,
            "claim_needed": bool(setup_agent and setup_agent.claim_needed),
        },
    )


@websocket_api.websocket_command({vol.Required("type"): "orchestra/agent/history"})
@websocket_api.require_admin
@callback
def ws_history(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    agent = _agent(hass)
    connection.send_result(msg["id"], {"messages": agent.transcript(connection.user.id) if agent else []})


@websocket_api.websocket_command({vol.Required("type"): "orchestra/agent/reset"})
@websocket_api.require_admin
@callback
def ws_reset(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    agent = _agent(hass)
    if agent:
        agent.reset(connection.user.id)
    connection.send_result(msg["id"])


@websocket_api.websocket_command(
    {
        vol.Required("type"): "orchestra/agent/send",
        vol.Optional("text", default=""): str,
        vol.Optional("images", default=[]): [
            {
                vol.Required("media_type"): vol.In(["image/jpeg", "image/png", "image/webp", "image/gif"]),
                vol.Required("data"): str,
            }
        ],
    }
)
@websocket_api.require_admin
@callback
def ws_send(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """A subscription: each step of the agent's answer arrives as an event, then `done`."""
    agent = _agent(hass)
    if agent is None:
        connection.send_error(msg["id"], "not_ready", "The Orchestra assistant is not running.")
        return
    msg_id = msg["id"]

    @callback
    def _send(event: dict[str, Any]) -> None:
        connection.send_message(websocket_api.event_message(msg_id, event))

    async def _run() -> None:
        try:
            await agent.async_send(connection.user.id, msg["text"], msg["images"], _send)
        finally:
            _send({"type": "done"})

    # Closing the drawer only stops the events: the agent finishes its turn, so the
    # conversation never ends on a tool call without its result
    connection.subscriptions[msg_id] = lambda: None
    connection.send_result(msg_id)
    hass.async_create_background_task(_run(), "orchestra_agent_chat")


@websocket_api.websocket_command(
    {
        vol.Required("type"): "orchestra/agent/claim",
        vol.Required("name"): str,
        vol.Required("username"): str,
        vol.Required("password"): str,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_claim(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict[str, Any]) -> None:
    """'Make this home yours': handled here, never by the assistant, so the password stays out of the chat."""
    from .setup_agent import ClaimError

    setup_agent = hass.data.get(DOMAIN, {}).get("setup_agent")
    if setup_agent is None:
        connection.send_error(msg["id"], "not_ready", "This box has no Orchestra setup to finish.")
        return
    try:
        username = await setup_agent.async_claim(msg["name"], msg["username"], msg["password"])
    except ClaimError as err:
        connection.send_error(msg["id"], "invalid", str(err))
        return
    connection.send_result(msg["id"], {"username": username})
