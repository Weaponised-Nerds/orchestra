"""
Runs the in-HA setup assistant against a fake Conductor relay inside a real Home Assistant core.

Needs `pytest-homeassistant-custom-component`, so it is skipped in the plain test run:
    pip install pytest-homeassistant-custom-component
    pytest tests/ha -p pytest_homeassistant_custom_component -o asyncio_mode=auto
"""

from __future__ import annotations

import copy
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

import voluptuous as vol  # noqa: E402

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.setup import async_setup_component  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
)

from custom_components.orchestra.agent_chat import RELAY_URL, OrchestraChatAgent, async_register_websocket_commands  # noqa: E402
from custom_components.orchestra.const import DOMAIN  # noqa: E402
from custom_components.orchestra.setup_agent import OrchestraSetupAgent  # noqa: E402
from custom_components.orchestra.setup_tools import async_register_setup_tools  # noqa: E402


class TvFlow(config_entries.ConfigFlow):
    async def async_step_zeroconf(self, discovery_info=None):
        self.context["title_placeholders"] = {"name": "Lounge TV"}
        return await self.async_step_pair()

    async def async_step_pair(self, user_input=None):
        if user_input is not None and user_input.get("pin") == "1234":
            return self.async_create_entry(title="Lounge TV", data={})
        return self.async_show_form(step_id="pair", data_schema=vol.Schema({vol.Required("pin"): str}))


def _tool_use(tool_id, name, args):
    return {"stop_reason": "tool_use", "content": [{"type": "tool_use", "id": tool_id, "name": name, "input": args}]}


@pytest.fixture
async def chat(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "automations.yaml").write_text("[]\n")
    assert await async_setup_component(hass, "websocket_api", {})
    hass.data.setdefault(DOMAIN, {})["setup_agent"] = OrchestraSetupAgent(hass, {"users": []}, {})
    unregister = async_register_setup_tools(hass)
    agent = await OrchestraChatAgent.async_load(hass)
    hass.data[DOMAIN]["chat_agent"] = agent
    async_register_websocket_commands(hass)
    mock_integration(hass, MockModule("tvdev", async_setup_entry=AsyncMock(return_value=True)))
    mock_platform(hass, "tvdev.config_flow", None)
    with mock_config_flow("tvdev", TvFlow):
        yield agent
    unregister()


async def test_assistant_pairs_a_tv_through_the_relay(
    hass: HomeAssistant, chat, aioclient_mock, hass_ws_client, hass_admin_user
) -> None:
    result = await hass.config_entries.flow.async_init("tvdev", context={"source": "zeroconf"}, data={})
    flow_id = result["flow_id"]
    replies = [
        _tool_use("t1", "orchestra_get_setup_list", {}),
        _tool_use("t2", "orchestra_submit_setup_form", {"flow_id": flow_id, "values": {"pin": "1234"}}),
        {"stop_reason": "end_turn", "content": [{"type": "text", "text": "Your Lounge TV is set up."}]},
    ]
    sent: list[dict] = []

    async def relay(method, url, data):
        sent.append(copy.deepcopy(data))
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse

        return AiohttpClientMockResponse(method, url, json=replies[len(sent) - 1])

    aioclient_mock.post(RELAY_URL, side_effect=relay)

    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "orchestra/agent/send", "text": "The TV shows 1234", "images": [
        {"media_type": "image/jpeg", "data": "aGVsbG8="}
    ]})
    assert (await client.receive_json())["success"]
    events = []
    while True:
        msg = await client.receive_json()
        events.append(msg["event"])
        if msg["event"]["type"] == "done":
            break

    assert [e["type"] for e in events] == ["tool", "tool", "text", "done"]
    assert events[2]["text"] == "Your Lounge TV is set up."
    assert hass.config_entries.async_entries("tvdev")
    # The relay gets the photo, the tools and the box id; tool results go back in order
    first = sent[0]
    assert first["messages"][0]["content"][0]["type"] == "image"
    assert {t["name"] for t in first["tools"]} >= {"orchestra_get_setup_list", "orchestra_submit_setup_form"}
    assert aioclient_mock.mock_calls[0][3]["X-Orchestra-Box"] == chat.box_id
    assert sent[2]["messages"][-1]["content"][0]["type"] == "tool_result"
    assert '"added": "Lounge TV"' in sent[2]["messages"][-1]["content"][0]["content"]

    await client.send_json({"id": 2, "type": "orchestra/agent/history"})
    history = (await client.receive_json())["result"]["messages"]
    assert [m["role"] for m in history] == ["user", "assistant"]
    assert history[0]["photos"] == 1


async def test_relay_errors_reach_the_person_and_can_be_retried(
    hass: HomeAssistant, chat, aioclient_mock, hass_ws_client
) -> None:
    aioclient_mock.post(RELAY_URL, status=429, json={"error": "This box has used its setup agent allowance for today."})
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "orchestra/agent/send", "text": "hi"})
    assert (await client.receive_json())["success"]
    error = (await client.receive_json())["event"]
    assert error == {"type": "error", "text": "This box has used its setup agent allowance for today."}
    assert (await client.receive_json())["event"]["type"] == "done"
    # The failed message is dropped, so the conversation stays valid for the next try
    assert chat._conversations[next(iter(chat._conversations))] == []


async def test_status_counts_what_is_waiting(hass: HomeAssistant, chat, hass_ws_client) -> None:
    await hass.config_entries.flow.async_init("tvdev", context={"source": "zeroconf"}, data={})
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": "orchestra/agent/status"})
    assert (await client.receive_json())["result"] == {"available": True, "open_items": 0, "waiting_devices": 1}


async def test_box_id_is_kept(hass: HomeAssistant, chat) -> None:
    again = await OrchestraChatAgent.async_load(hass)
    assert again.box_id == chat.box_id and len(chat.box_id) == 32
