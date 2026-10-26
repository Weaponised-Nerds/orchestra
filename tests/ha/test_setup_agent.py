"""
Runs the Orchestra setup agent inside a real Home Assistant core.

Needs `pytest-homeassistant-custom-component`, so it is skipped in the plain test run:
    pip install pytest-homeassistant-custom-component
    pytest tests/ha -p pytest_homeassistant_custom_component -o asyncio_mode=auto
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

import voluptuous as vol  # noqa: E402
import yaml  # noqa: E402

from homeassistant import config_entries  # noqa: E402
from homeassistant.core import HomeAssistant  # noqa: E402
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er  # noqa: E402
from pytest_homeassistant_custom_component.common import (  # noqa: E402
    MockConfigEntry,
    MockModule,
    mock_config_flow,
    mock_integration,
    mock_platform,
)

from custom_components.orchestra.setup_agent import AGENT_USERNAME, OrchestraSetupAgent  # noqa: E402


class ConfirmFlow(config_entries.ConfigFlow):
    """A discovered device that only needs a yes, like a Chromecast."""

    async def async_step_zeroconf(self, discovery_info=None):
        self.context["title_placeholders"] = {"name": "Kitchen speaker"}
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Kitchen speaker", data={})
        return self.async_show_form(step_id="confirm", data_schema=vol.Schema({}))


class PinFlow(config_entries.ConfigFlow):
    """A discovered device that needs a pairing code, like a HomeKit accessory."""

    async def async_step_zeroconf(self, discovery_info=None):
        self.context["title_placeholders"] = {"name": "Sensibo Sky", "host": "192.168.1.40"}
        return await self.async_step_pair()

    async def async_step_pair(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="Sensibo Sky", data=user_input)
        return self.async_show_form(step_id="pair", data_schema=vol.Schema({vol.Required("pairing_code"): str}))


@pytest.fixture
def agent(hass: HomeAssistant, tmp_path: Path) -> OrchestraSetupAgent:
    hass.config.config_dir = str(tmp_path)
    (tmp_path / "automations.yaml").write_text("[]\n")
    plan = {"users": [{"name": "Dee Ben", "username": "dee", "password": "hunter22-long", "admin": True}]}
    return OrchestraSetupAgent(hass, plan, {})


async def test_confirms_simple_flows_and_lists_the_rest(hass: HomeAssistant, agent) -> None:
    for domain in ("confirmdev", "pindev"):
        mock_integration(hass, MockModule(domain))
        mock_platform(hass, f"{domain}.config_flow", None)
    with mock_config_flow("confirmdev", ConfirmFlow), mock_config_flow("pindev", PinFlow):
        await hass.config_entries.flow.async_init("confirmdev", context={"source": "zeroconf"}, data={})
        await hass.config_entries.flow.async_init("pindev", context={"source": "zeroconf"}, data={})
        # A flow a person started is never touched
        await hass.config_entries.flow.async_init("pindev", context={"source": "user"}, data={})

        result = await agent.async_resolve_discovery_flows()
        await hass.async_block_till_done()

        assert len(result["confirmed"]) == 1
        assert len(result["asked"]) == 1
        assert hass.config_entries.async_entries("confirmdev")
        [item] = agent.open_items
        assert item["summary"] == "Add Sensibo Sky (192.168.1.40): needs the pairing code or PIN"
        assert item["domain"] == "pindev"

        # A second pass doesn't repeat itself
        again = await agent.async_resolve_discovery_flows()
        assert again == {"confirmed": [], "asked": [], "skipped": []}

        # Once the person enters the code, the item ticks itself off
        pin_flow = next(
            f for f in hass.config_entries.flow.async_progress()
            if f["handler"] == "pindev" and f["context"]["source"] == "zeroconf"
        )
        await hass.config_entries.flow.async_configure(pin_flow["flow_id"], {"pairing_code": "1234-5678"})
        await hass.async_block_till_done()
        agent._async_tick_off_finished_items()
        assert agent.open_items == []


async def test_sorts_devices_and_prefills_blueprints(hass: HomeAssistant, agent) -> None:
    entry = MockConfigEntry(domain="airtouch")
    entry.add_to_hass(hass)
    dev_reg = dr.async_get(hass)
    ent_reg = er.async_get(hass)

    unit = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("airtouch", "ac")}, name="AirTouch AC")
    zone = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("airtouch", "z1")}, name="Lounge zone")
    zone = dev_reg.async_update_device(zone.id, via_device_id=unit.id)
    pool = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("tuya", "hp")}, name="Pool heat pump")
    charger = dev_reg.async_get_or_create(config_entry_id=entry.entry_id, identifiers={("tesla", "wc")}, name="Wall Connector")

    def add(entity_id: str, device_id: str, state: str, attrs: dict) -> None:
        domain, object_id = entity_id.split(".")
        ent_reg.async_get_or_create(domain, "test", object_id, device_id=device_id, config_entry=entry, suggested_object_id=object_id)
        hass.states.async_set(entity_id, state, attrs)

    add("climate.ac", unit.id, "cool", {"friendly_name": "AC"})
    add("climate.lounge_zone", zone.id, "cool", {"friendly_name": "Lounge zone"})
    add("water_heater.pool_heat_pump", pool.id, "boost", {"friendly_name": "Pool heat pump"})
    add("switch.pool_heat_pump_power", pool.id, "on", {"friendly_name": "Pool heat pump power"})
    add("number.wall_connector_charging_current", charger.id, "16", {"unit_of_measurement": "A", "friendly_name": "Charging current"})
    add("switch.wall_connector_charging", charger.id, "on", {"friendly_name": "Charging"})

    organized = await agent.async_organize_devices()
    assert organized["rooms"] >= 3
    area_reg = ar.async_get(hass)
    assert area_reg.async_get_area(dev_reg.async_get(zone.id).area_id).name == "Living room"
    assert area_reg.async_get_area(dev_reg.async_get(pool.id).area_id).name == "Pool"
    assert area_reg.async_get_area(dev_reg.async_get(charger.id).area_id).name == "Driveway"

    notes = await agent.async_prefill_automations()
    assert len(notes) == 3
    automations = yaml.safe_load(Path(hass.config.path("automations.yaml")).read_text())
    by_id = {a["id"]: a["use_blueprint"] for a in automations}
    # Zoned A/C: drift the zone, not the unit that follows it
    assert by_id["orchestra_hvac_offset"]["input"] == {"climate_entity": ["climate.lounge_zone"]}
    assert by_id["orchestra_heat_pump_pool_heat_pump"]["input"] == {
        "water_heater": "water_heater.pool_heat_pump",
        "power_switch": "switch.pool_heat_pump_power",
    }
    assert by_id["orchestra_ev_rate_wall_connector_charging_current"]["input"] == {
        "charger_current": "number.wall_connector_charging_current",
        "charger_switch": "switch.wall_connector_charging",
    }
    assert by_id["orchestra_hvac_offset"]["path"] == "orchestra/orchestra_hvac_setpoint_offset.yaml"

    # Later passes leave existing automations (and any edits to them) alone
    assert await agent.async_prefill_automations() == []


async def test_creates_logins_then_rotates_agent_password(hass: HomeAssistant, agent, local_auth) -> None:
    await local_auth.async_add_auth(AGENT_USERNAME, "OrchestraAgent2026!")
    creds_file = Path(hass.config.path("orchestra_agent_credentials.json"))
    creds_file.write_text("{}")

    created = await agent.async_setup_users()
    assert created == ["dee"]

    await local_auth.async_validate_login("dee", "hunter22-long")
    with pytest.raises(Exception):
        await local_auth.async_validate_login(AGENT_USERNAME, "OrchestraAgent2026!")
    users = {u.name: u for u in await hass.auth.async_get_users()}
    assert any(g.id == "system-admin" for g in users["Dee Ben"].groups)
    assert not creds_file.exists()
    assert "password" not in agent.plan["users"][0]

    # Runs once
    assert await agent.async_setup_users() == []


async def test_no_logins_keeps_agent_password(hass: HomeAssistant, tmp_path, local_auth) -> None:
    hass.config.config_dir = str(tmp_path)
    await local_auth.async_add_auth(AGENT_USERNAME, "OrchestraAgent2026!")
    agent = OrchestraSetupAgent(hass, {"users": []}, {})
    assert await agent.async_setup_users() == []
    await local_auth.async_validate_login(AGENT_USERNAME, "OrchestraAgent2026!")
    assert [i["key"] for i in agent.open_items] == ["users"]


async def test_image_first_boot_end_to_end(
    hass: HomeAssistant, tmp_path, hass_storage, local_auth, enable_custom_integrations
) -> None:
    """`orchestra:` in YAML plus a setup plan: entry, agent pass, punch list and notification."""
    from unittest.mock import AsyncMock, patch

    from homeassistant.setup import async_setup_component

    hass.config.config_dir = str(tmp_path)
    (tmp_path / "automations.yaml").write_text("[]\n")
    await local_auth.async_add_auth(AGENT_USERNAME, "OrchestraAgent2026!")
    hass_storage["orchestra.setup_plan"] = {
        "version": 1,
        "minor_version": 1,
        "key": "orchestra.setup_plan",
        "data": {
            "users": [{"name": "Dee Ben", "username": "dee", "password": "hunter22-long"}],
            "min_wait_seconds": 0,
            "quiet_seconds": 0,
            "max_wait_seconds": 0,
            "entry_settle_seconds": 0,
        },
    }
    (tmp_path / "custom_components" / "hacs").mkdir(parents=True)

    with (
        patch("custom_components.orchestra.coordinator.OrchestraDataUpdateCoordinator.async_start", AsyncMock()),
        patch("custom_components.orchestra.conductor_subscriber.ConductorSubscriber.async_start", AsyncMock()),
        patch.object(OrchestraSetupAgent, "_async_onboarding_integrations", AsyncMock(return_value=[])),
    ):
        assert await async_setup_component(hass, "orchestra", {"orchestra": None})
        await hass.async_block_till_done()
        assert hass.config_entries.async_entries("orchestra")
        await hass.async_start()
        await hass.async_block_till_done()
        agent = hass.data["orchestra"]["setup_agent"]
        await agent._task
        await hass.async_block_till_done()

        state = hass.states.get("todo.orchestra_setup")
        assert state is not None
        assert state.state == "1"  # Connect HACS to GitHub
        from homeassistant.components.persistent_notification import _async_get_or_create_notifications

        notification = _async_get_or_create_notifications(hass)["orchestra_setup"]
        assert "Connect HACS to GitHub" in notification["message"]
        assert "Created logins: dee." in notification["message"]
        assert agent.state["users_done"] and agent.state["completed_at"]
        await local_auth.async_validate_login("dee", "hunter22-long")
        await agent._async_stop()
