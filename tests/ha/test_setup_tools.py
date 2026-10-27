"""
Drives the Orchestra Setup tools the way an MCP client would, inside a real Home Assistant core.

Needs `pytest-homeassistant-custom-component`, so it is skipped in the plain test run:
    pip install pytest-homeassistant-custom-component
    pytest tests/ha -p pytest_homeassistant_custom_component -o asyncio_mode=auto
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

import voluptuous as vol  # noqa: E402

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import Context, HomeAssistant  # noqa: E402
from homeassistant.helpers import device_registry as dr, llm  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
)

from custom_components.orchestra.const import DOMAIN  # noqa: E402
from custom_components.orchestra.setup_agent import OrchestraSetupAgent  # noqa: E402
from custom_components.orchestra.setup_tools import SETUP_API_ID, async_register_setup_tools  # noqa: E402


class TvFlow(config_entries.ConfigFlow):
    """A discovered TV that shows a 4-digit code on screen, like an Apple TV."""

    async def async_step_zeroconf(self, discovery_info=None):
        self.context["title_placeholders"] = {"name": "Lounge TV"}
        return await self.async_step_pair()

    async def async_step_pair(self, user_input=None):
        errors = {}
        if user_input is not None:
            if user_input["pin"] == "1234":
                return self.async_create_entry(title="Lounge TV", data={})
            errors["base"] = "invalid_auth"
        return self.async_show_form(
            step_id="pair", data_schema=vol.Schema({vol.Required("pin"): str}), errors=errors
        )

    async def async_step_user(self, user_input=None):
        return await self.async_step_pair(user_input)


def _context(user_id: str | None) -> llm.LLMContext:
    return llm.LLMContext(
        platform="mcp_server", context=Context(user_id=user_id), language="*", assistant="conversation", device_id=None
    )


async def _call(hass: HomeAssistant, user_id: str | None, tool: str, **args):
    api = await llm.async_get_api(hass, SETUP_API_ID, _context(user_id))
    return await api.async_call_tool(llm.ToolInput(tool_name=tool, tool_args=args))


@pytest.fixture
async def tools(hass: HomeAssistant, tmp_path: Path):
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "automations.yaml").write_text("[]\n")
    agent = OrchestraSetupAgent(hass, {"users": []}, {})
    hass.data.setdefault(DOMAIN, {})["setup_agent"] = agent
    unregister = async_register_setup_tools(hass)
    mock_integration(hass, MockModule("tvdev"))
    mock_platform(hass, "tvdev.config_flow", None)
    with mock_config_flow("tvdev", TvFlow):
        yield agent
    unregister()


async def test_tool_set_is_offered_to_mcp_server(hass: HomeAssistant, tools) -> None:
    assert SETUP_API_ID in {api.id for api in llm.async_get_apis(hass)}
    api = await llm.async_get_api(hass, SETUP_API_ID, _context(None))
    assert "orchestra_get_setup_list" in {t.name for t in api.tools}
    assert "never guess" in api.api_prompt
    # MCP Server turns each tool's parameters into a JSON schema when a client lists them
    from homeassistant.components.mcp_server.server import _format_tool

    for tool in api.tools:
        formatted = _format_tool(tool, api.custom_serializer).model_dump(by_alias=True)
        assert formatted["inputSchema"]["type"] == "object"


async def test_agent_pairs_a_tv_from_the_punch_list(hass: HomeAssistant, tools, hass_admin_user) -> None:
    await hass.config_entries.flow.async_init("tvdev", context={"source": "zeroconf"}, data={})
    await tools.async_resolve_discovery_flows()
    uid = hass_admin_user.id

    listing = await _call(hass, uid, "orchestra_get_setup_list")
    assert [i["summary"] for i in listing["setup_list"]] == ["Add Lounge TV: needs the pairing code or PIN"]
    flow_id = listing["waiting_devices"][0]["flow_id"]
    assert listing["waiting_devices"][0]["name"] == "Lounge TV"

    form = await _call(hass, uid, "orchestra_get_setup_form", flow_id=flow_id)
    assert form["type"] == "form"
    assert [f["name"] for f in form["fields"]] == ["pin"]

    wrong = await _call(hass, uid, "orchestra_submit_setup_form", flow_id=flow_id, values={"pin": "9999"})
    assert wrong["type"] == "form" and wrong["errors"] == {"base": "invalid_auth"}

    done = await _call(hass, uid, "orchestra_submit_setup_form", flow_id=flow_id, values={"pin": "1234"})
    assert done == {"integration": "tvdev", "type": "create_entry", "added": "Lounge TV", "message": done["message"]}
    assert tools.open_items == []


async def test_bad_values_and_unknown_flows_come_back_as_errors(hass: HomeAssistant, tools, hass_admin_user) -> None:
    uid = hass_admin_user.id
    assert "error" in await _call(hass, uid, "orchestra_get_setup_form", flow_id="nope")
    started = await _call(hass, uid, "orchestra_start_adding_integration", integration="tvdev")
    assert started["type"] == "form" and started["step"] == "pair"
    bad = await _call(hass, uid, "orchestra_submit_setup_form", flow_id=started["flow_id"], values={"code": "1"})
    assert "error" in bad
    assert "error" in await _call(hass, uid, "orchestra_start_adding_integration", integration="not_a_real_thing")


async def test_only_admins_can_use_the_tools(hass: HomeAssistant, tools, hass_read_only_user) -> None:
    result = await _call(hass, hass_read_only_user.id, "orchestra_get_setup_list")
    assert result == {"error": "Only a Home Assistant administrator can set up devices."}


async def test_lists_devices_with_serials_and_rooms(hass: HomeAssistant, tools, hass_admin_user) -> None:
    entry = MockConfigEntry(domain="tvdev")
    entry.add_to_hass(hass)
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("tvdev", "1")},
        name="Gateway",
        manufacturer="Tesla",
        model="Backup Gateway 2",
        serial_number="1118431-00-L--TG123456",
        suggested_area="Garage",
    )
    result = await _call(hass, hass_admin_user.id, "orchestra_list_devices", area="garage")
    assert result["devices"] == [
        {
            "name": "Gateway",
            "manufacturer": "Tesla",
            "model": "Backup Gateway 2",
            "serial_number": "1118431-00-L--TG123456",
            "area": "Garage",
            "integrations": ["tvdev"],
        }
    ]
