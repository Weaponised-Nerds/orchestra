"""
Orchestra Setup tools: lets an AI agent finish setup with the homeowner.

Registers an LLM tool set called "Orchestra Setup". Home Assistant's MCP Server publishes
it (pick it next to Assist when adding the MCP Server integration), so an agent such as
Claude can read the setup punch list, see what each discovered device is waiting for,
and submit the pairing code, serial number or choice the person gives it.

The tools only read setup state and drive config flows, the same steps a person takes in
Settings → Devices & services. They cannot delete anything, and every tool needs an admin
user, because adding integrations does.
"""

from __future__ import annotations

from typing import Any, Callable

import voluptuous as vol

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, llm

from .const import DOMAIN

SETUP_API_ID = "orchestra_setup"
SETUP_API_NAME = "Orchestra Setup"
MAX_DEVICES = 200

SETUP_PROMPT = (
    "You are helping a homeowner finish setting up their Orchestra home energy system in "
    "Home Assistant. Many of them are not technical. Start with orchestra_get_setup_list. "
    "Work through one item at a time, say in plain words what you need and where to find "
    "it (for example 'the 4-digit code now showing on the TV' or 'the serial number on the "
    "sticker under the gateway'), and never guess or invent a code, serial or password. "
    "When the person sends a photo of a label, read the value, repeat it back and wait for "
    "a yes before you submit it. Use orchestra_get_setup_form before submitting so you know "
    "the exact field names. Steps that need a sign-in on a vendor's website are parked for "
    "now: tell the person it uses the maker's cloud and leave it on the list."
)


def async_register_setup_tools(hass: HomeAssistant) -> Callable[[], None]:
    """Make the Orchestra Setup tool set available to MCP Server and other LLM clients."""
    return llm.async_register_api(hass, OrchestraSetupAPI(hass=hass, id=SETUP_API_ID, name=SETUP_API_NAME))


class OrchestraSetupAPI(llm.API):
    """The Orchestra Setup tool set."""

    async def async_get_api_instance(self, llm_context: llm.LLMContext) -> llm.APIInstance:
        return llm.APIInstance(
            api=self,
            api_prompt=SETUP_PROMPT,
            llm_context=llm_context,
            tools=[
                GetSetupListTool(),
                GetSetupFormTool(),
                SubmitSetupFormTool(),
                StartAddingIntegrationTool(),
                MarkSetupItemDoneTool(),
                ListDevicesTool(),
            ],
        )


class _AdminTool(llm.Tool):
    """A tool that only an admin may call, as with the Devices & services page."""

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> dict[str, Any]:
        user_id = llm_context.context.user_id if llm_context.context else None
        if user_id is not None:
            user = await hass.auth.async_get_user(user_id)
            if user is None or not user.is_admin:
                return {"error": "Only a Home Assistant administrator can set up devices."}
        return await self._async_run(hass, tool_input.tool_args)

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError


class GetSetupListTool(_AdminTool):
    name = "orchestra_get_setup_list"
    description = (
        "List what still needs the homeowner: the Orchestra setup punch list and every "
        "discovered device whose setup is waiting, with the flow_id to use for it."
    )

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        agent = hass.data.get(DOMAIN, {}).get("setup_agent")
        items = [
            {
                "key": item["key"],
                "summary": item["summary"],
                "description": item.get("description"),
                "integration": item.get("domain"),
                "done": bool(item.get("done")),
            }
            for item in (agent.items if agent is not None else [])
        ]
        waiting = [
            {
                "flow_id": flow["flow_id"],
                "integration": flow.get("handler"),
                "name": _flow_name(flow),
                "found_by": (flow.get("context") or {}).get("source"),
                "step": flow.get("step_id"),
            }
            for flow in hass.config_entries.flow.async_progress()
        ]
        return {"setup_list": items, "waiting_devices": waiting}


class GetSetupFormTool(_AdminTool):
    name = "orchestra_get_setup_form"
    description = (
        "Show the current step of a device's setup: the fields it asks for (with labels, "
        "options and whether they are required), any error from the last attempt, and the "
        "step's own instructions."
    )
    parameters = vol.Schema({vol.Required("flow_id"): str})

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        flow_id = args["flow_id"]
        try:
            flow = hass.config_entries.flow.async_get(flow_id)
            result = await hass.config_entries.flow.async_configure(flow_id)
        except Exception as err:  # unknown or finished flow
            return {"error": f"No setup in progress with that flow_id ({err.__class__.__name__})."}
        return await _async_describe_step(hass, flow.get("handler", ""), result)


class SubmitSetupFormTool(_AdminTool):
    name = "orchestra_submit_setup_form"
    description = (
        "Submit the values for the current step of a device's setup, using the field names "
        "from orchestra_get_setup_form. Returns the next step, the error to fix, or the "
        "integration that was added."
    )
    parameters = vol.Schema({vol.Required("flow_id"): str, vol.Required("values"): dict})

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        flow_id = args["flow_id"]
        try:
            handler = hass.config_entries.flow.async_get(flow_id).get("handler", "")
            result = await hass.config_entries.flow.async_configure(flow_id, args["values"])
        except vol.Invalid as err:
            return {"error": f"Home Assistant did not accept those values: {err}"}
        except Exception as err:  # unknown flow, or the device rejected the input
            return {"error": f"That step failed: {err}"}
        if _step_type(result) == "create_entry":
            agent = hass.data.get(DOMAIN, {}).get("setup_agent")
            if agent is not None:
                await agent.async_set_item_done(f"flow:{flow_id}", True)
        return await _async_describe_step(hass, handler, result)


class StartAddingIntegrationTool(_AdminTool):
    name = "orchestra_start_adding_integration"
    description = (
        "Start adding an integration that Home Assistant did not discover by itself, by its "
        "domain (for example 'enphase_envoy' or 'airtouch5'). Returns the first step's form."
    )
    parameters = vol.Schema({vol.Required("integration"): str})

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        domain = str(args["integration"]).strip().lower()
        try:
            result = await hass.config_entries.flow.async_init(domain, context={"source": "user"})
        except Exception as err:  # not installed, or it has no setup flow
            return {"error": f"Could not start adding '{domain}': {err}"}
        return await _async_describe_step(hass, domain, result)


class MarkSetupItemDoneTool(_AdminTool):
    name = "orchestra_mark_setup_item_done"
    description = (
        "Tick an Orchestra setup list item off once the person has done it (or untick it), "
        "by its key from orchestra_get_setup_list."
    )
    parameters = vol.Schema({vol.Required("key"): str, vol.Optional("done", default=True): bool})

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        agent = hass.data.get(DOMAIN, {}).get("setup_agent")
        if agent is None or not any(i["key"] == args["key"] for i in agent.items):
            return {"error": "There is no setup list item with that key."}
        await agent.async_set_item_done(args["key"], bool(args.get("done", True)))
        return {"key": args["key"], "done": bool(args.get("done", True))}


class ListDevicesTool(_AdminTool):
    name = "orchestra_list_devices"
    description = (
        "List the devices Home Assistant knows, with maker, model, serial number, room and "
        "integration, optionally only those in one room."
    )
    parameters = vol.Schema({vol.Optional("area"): str})

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        areas = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
        wanted = (args.get("area") or "").strip().lower()
        devices: list[dict[str, Any]] = []
        for device in dr.async_get(hass).devices.values():
            area = areas.get(device.area_id or "")
            if wanted and (area or "").lower() != wanted:
                continue
            integrations = sorted(
                {
                    entry.domain
                    for entry_id in device.config_entries
                    if (entry := hass.config_entries.async_get_entry(entry_id)) is not None
                }
            )
            devices.append(
                {
                    "name": device.name_by_user or device.name,
                    "manufacturer": device.manufacturer,
                    "model": device.model,
                    "serial_number": getattr(device, "serial_number", None),
                    "area": area,
                    "integrations": integrations,
                }
            )
        return {"devices": devices[:MAX_DEVICES], "total": len(devices)}


# ---------------------------------------------------------------------- helpers


def _flow_name(flow: dict[str, Any]) -> str:
    placeholders = (flow.get("context") or {}).get("title_placeholders") or {}
    return str(placeholders.get("name") or placeholders.get("model") or flow.get("handler", "device"))


def _step_type(result: dict[str, Any]) -> str:
    kind = result.get("type", "")
    return str(getattr(kind, "value", kind))


def _fill(text: str | None, placeholders: dict[str, Any]) -> str | None:
    if not text:
        return None
    try:
        return text.format_map(_Blank(placeholders))
    except (ValueError, IndexError):
        return text


class _Blank(dict):
    """Leaves unknown {placeholders} as they are instead of failing."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


async def _async_strings(hass: HomeAssistant, domain: str) -> dict[str, str]:
    try:
        from homeassistant.helpers.translation import async_get_translations

        return await async_get_translations(hass, hass.config.language, "config", [domain])
    except Exception:  # translations are a nicety
        return {}


def _serialize_fields(data_schema: Any) -> list[dict[str, Any]]:
    if data_schema is None:
        return []
    try:
        import voluptuous_serialize

        from homeassistant.helpers import config_validation as cv

        return voluptuous_serialize.convert(data_schema, custom_serializer=cv.custom_serializer)
    except Exception:  # an unusual schema: still say which fields exist
        keys = getattr(data_schema, "schema", {}) or {}
        return [{"name": str(getattr(k, "schema", k)), "required": type(k).__name__ == "Required"} for k in keys]


async def _async_describe_step(hass: HomeAssistant, domain: str, result: dict[str, Any]) -> dict[str, Any]:
    """What the agent needs to know about a flow step, without echoing submitted values."""
    kind = _step_type(result)
    strings = await _async_strings(hass, domain)
    placeholders = result.get("description_placeholders") or {}
    base = f"component.{domain}.config"
    step_id = result.get("step_id")
    out: dict[str, Any] = {"integration": domain, "type": kind}

    if kind == "form":
        fields = _serialize_fields(result.get("data_schema"))
        for f in fields:
            label = strings.get(f"{base}.step.{step_id}.data.{f.get('name')}")
            hint = strings.get(f"{base}.step.{step_id}.data_description.{f.get('name')}")
            if label:
                f["label"] = label
            if hint:
                f["hint"] = _fill(hint, placeholders)
        errors = {
            name: strings.get(f"{base}.error.{code}") or code
            for name, code in (result.get("errors") or {}).items()
        }
        out.update(
            flow_id=result.get("flow_id"),
            step=step_id,
            title=_fill(strings.get(f"{base}.step.{step_id}.title"), placeholders),
            instructions=_fill(strings.get(f"{base}.step.{step_id}.description"), placeholders),
            fields=fields,
            errors=errors,
        )
    elif kind == "create_entry":
        out.update(added=result.get("title"), message="Added. This device is now set up.")
    elif kind == "abort":
        reason = result.get("reason")
        out.update(reason=reason, message=_fill(strings.get(f"{base}.abort.{reason}"), placeholders) or reason)
    elif kind == "menu":
        out.update(
            flow_id=result.get("flow_id"),
            step=step_id,
            options=result.get("menu_options"),
            message='Pick one by submitting {"next_step_id": <option>}.',
        )
    elif kind == "external":
        out.update(
            flow_id=result.get("flow_id"),
            message="This step is a sign-in on the maker's website. It is parked for now; leave it on the list.",
        )
    else:  # progress steps
        out.update(flow_id=result.get("flow_id"), step=step_id, message="Home Assistant is still working on this step.")
    return out
