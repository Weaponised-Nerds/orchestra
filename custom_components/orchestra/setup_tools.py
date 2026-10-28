"""
Orchestra Setup tools: lets an AI agent finish setup with the homeowner.

Registers an LLM tool set called "Orchestra Setup". Home Assistant's MCP Server publishes
it (pick it next to Assist when adding the MCP Server integration), so an agent such as
Claude can read the setup punch list, see what each discovered device is waiting for,
and submit the pairing code, serial number or choice the person gives it.

The tools drive config flows, name devices and entities, put them in rooms, and assign
equipment to the Orchestra blueprints: the same steps a person takes in Settings. They
cannot delete devices, integrations or entities, never see logins, and every tool needs an
admin user, because those settings pages do.
"""

from __future__ import annotations

from typing import Any, Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar, device_registry as dr, entity_registry as er, llm

from .const import DOMAIN
from .schema import to_field_list, vol

SETUP_API_ID = "orchestra_setup"
SETUP_API_NAME = "Orchestra Setup"
MAX_DEVICES = 200
MAX_ENTITIES = 150

SETUP_PROMPT = (
    "You are helping a homeowner finish setting up their Orchestra home energy system in "
    "Home Assistant. Many of them are not technical. Start with orchestra_get_setup_list. "
    "Work through one item at a time, say in plain words what you need and where to find "
    "it (for example 'the 4-digit code now showing on the TV' or 'the serial number on the "
    "sticker under the gateway'), and never guess or invent a code, serial or password. "
    "When the person sends a photo of a label, read the value, repeat it back and wait for "
    "a yes before you submit it. Use orchestra_get_setup_form before submitting so you know "
    "the exact field names. Steps that need a sign-in on a vendor's website are parked for "
    "now: tell the person it uses the maker's cloud and leave it on the list. Never ask for, "
    "repeat or submit the person's own Home Assistant password: if the list says the home "
    "has no owner login yet, point them to the 'Make this home yours' form in the Setup drawer. "
    "Before saying an integration isn't connected, check orchestra_list_integrations. "
    "Once devices are set up, organise the home: for the key energy equipment (solar, home "
    "batteries, EV chargers, air conditioning, heat pumps and hot water) and anything the person "
    "asks about, look at the devices and entities, give them short clear names a homeowner "
    "would use (room plus what it is, such as 'Garage EV charger'), put each device in its "
    "room and label it. Propose the changes in a short list and make them after the person "
    "says yes. Then assign each air conditioner (or its zones), heat pump or hot water "
    "system, EV charger and home battery to its Orchestra blueprint with "
    "orchestra_assign_equipment, checking orchestra_get_equipment_assignments first so "
    "nothing is assigned twice. Solar has no Orchestra blueprint yet: name it, place it and "
    "label it. Entity ids stay as they are, so existing automations keep working. "
    "Orchestra keeps HACS and itself up to date: use orchestra_check_updates and "
    "orchestra_install_update when asked or when HACS is being set up. Installing an update "
    "to something that is running needs a restart; ask before calling "
    "orchestra_restart_home_assistant and warn that this chat starts over afterwards. "
    "orchestra_apply_theme gives Home Assistant the Conductor look."
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
                ListIntegrationsTool(),
                ListEntitiesTool(),
                UpdateDeviceTool(),
                UpdateEntityTool(),
                GetEquipmentAssignmentsTool(),
                AssignEquipmentTool(),
                RemoveEquipmentAssignmentTool(),
                CheckUpdatesTool(),
                InstallUpdateTool(),
                RestartHomeAssistantTool(),
                ApplyThemeTool(),
            ],
        )


class _AdminTool(llm.Tool):
    """A tool that only an admin may call, as with the Devices & services page."""

    integration = DOMAIN  # Home Assistant 2026.10 asks which integration provides a tool

    async def async_call(
        self, hass: HomeAssistant, tool_input: llm.ToolInput, llm_context: llm.LLMContext
    ) -> Any:
        user_id = llm_context.context.user_id if llm_context.context else None
        result: dict[str, Any] | None = None
        if user_id is not None:
            user = await hass.auth.async_get_user(user_id)
            if user is None or not user.is_admin:
                result = {"error": "Only a Home Assistant administrator can set up devices."}
        if result is None:
            result = await self._async_run(hass, tool_input.tool_args)
        # Home Assistant 2026.10 wants a ToolResult; older versions take the plain dict
        tool_result = getattr(llm, "ToolResult", None)
        return tool_result(data=result, error="error" in result) if tool_result else result

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
        "List the devices Home Assistant knows, with device_id, maker, model, serial number, "
        "room, labels and integration, optionally only those in one room or from one integration."
    )
    parameters = vol.Schema({vol.Optional("area"): str, vol.Optional("integration"): str})

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        areas = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
        labels = _label_names(hass)
        wanted = (args.get("area") or "").strip().lower()
        wanted_integration = (args.get("integration") or "").strip().lower()
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
            if wanted_integration and wanted_integration not in integrations:
                continue
            devices.append(
                {
                    "device_id": device.id,
                    "name": device.name_by_user or device.name,
                    "manufacturer": device.manufacturer,
                    "model": device.model,
                    "serial_number": getattr(device, "serial_number", None),
                    "area": area,
                    "labels": sorted(labels.get(label, label) for label in device.labels),
                    "integrations": integrations,
                }
            )
        return {"devices": devices[:MAX_DEVICES], "total": len(devices)}


class ListIntegrationsTool(_AdminTool):
    name = "orchestra_list_integrations"
    description = "List the integrations that are set up in Home Assistant, with their title and whether they loaded."

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "integrations": [
                {
                    "integration": entry.domain,
                    "title": entry.title,
                    "state": str(getattr(entry.state, "value", entry.state)),
                }
                for entry in hass.config_entries.async_entries()
                if entry.domain not in ("orchestra", "backup", "go2rtc", "sun", "hassio")
            ]
        }


class ListEntitiesTool(_AdminTool):
    name = "orchestra_list_entities"
    description = (
        "List entities with their name, current state, unit, device class, device and room. "
        "Filter by device_id, integration, domain (such as 'sensor' or 'climate') or a search "
        "word; at least one filter is needed on a big home."
    )
    parameters = vol.Schema(
        {
            vol.Optional("device_id"): str,
            vol.Optional("integration"): str,
            vol.Optional("domain"): str,
            vol.Optional("search"): str,
            vol.Optional("include_disabled", default=False): bool,
        }
    )

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        ent_reg = er.async_get(hass)
        dev_reg = dr.async_get(hass)
        areas = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
        device_id = args.get("device_id")
        integration = (args.get("integration") or "").strip().lower()
        domain = (args.get("domain") or "").strip().lower()
        search = (args.get("search") or "").strip().lower()
        found: list[dict[str, Any]] = []
        for entry in ent_reg.entities.values():
            if entry.platform == DOMAIN or (entry.disabled_by and not args.get("include_disabled")):
                continue
            if device_id and entry.device_id != device_id:
                continue
            if integration and entry.platform != integration:
                continue
            if domain and entry.domain != domain:
                continue
            state = hass.states.get(entry.entity_id)
            name = entry.name or (state.attributes.get("friendly_name") if state else None) or entry.original_name
            device = dev_reg.async_get(entry.device_id) if entry.device_id else None
            device_name = (device.name_by_user or device.name) if device else None
            if search and search not in " ".join(
                str(x).lower() for x in (entry.entity_id, name, device_name) if x
            ):
                continue
            found.append(
                {
                    "entity_id": entry.entity_id,
                    "name": name,
                    "state": state.state if state else None,
                    "unit": state.attributes.get("unit_of_measurement") if state else None,
                    "device_class": entry.device_class or entry.original_device_class,
                    "device_id": entry.device_id,
                    "device": device_name,
                    "area": areas.get(entry.area_id or (device.area_id if device else None) or ""),
                    "disabled": bool(entry.disabled_by),
                }
            )
        if len(found) > MAX_ENTITIES:
            return {
                "entities": found[:MAX_ENTITIES],
                "total": len(found),
                "note": "Only the first entities are shown. Narrow it down with a filter.",
            }
        return {"entities": found, "total": len(found)}


class UpdateDeviceTool(_AdminTool):
    name = "orchestra_update_device"
    description = (
        "Rename a device, move it to a room (created if it doesn't exist yet) and/or add "
        "labels (created if new), by device_id from orchestra_list_devices. An empty name "
        "goes back to the maker's name."
    )
    parameters = vol.Schema(
        {
            vol.Required("device_id"): str,
            vol.Optional("name"): str,
            vol.Optional("area"): str,
            vol.Optional("labels"): [str],
        }
    )

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        dev_reg = dr.async_get(hass)
        device = dev_reg.async_get(args["device_id"])
        if device is None:
            return {"error": "There is no device with that device_id."}
        changes: dict[str, Any] = {}
        if "name" in args:
            changes["name_by_user"] = args["name"].strip() or None
        if "area" in args:
            changes["area_id"] = _area_id(hass, args["area"])
        if args.get("labels"):
            changes["labels"] = set(device.labels) | {_label_id(hass, name) for name in args["labels"]}
        if not changes:
            return {"error": "Nothing to change: give a name, area or labels."}
        device = dev_reg.async_update_device(device.id, **changes) or dev_reg.async_get(device.id)
        areas = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
        labels = _label_names(hass)
        return {
            "device_id": device.id,
            "name": device.name_by_user or device.name,
            "area": areas.get(device.area_id or ""),
            "labels": sorted(labels.get(label, label) for label in device.labels),
        }


class UpdateEntityTool(_AdminTool):
    name = "orchestra_update_entity"
    description = (
        "Rename an entity and/or put it in a different room from its device. The entity_id "
        "stays the same so automations keep working. An empty name goes back to the default; "
        "an empty area follows the device's room again."
    )
    parameters = vol.Schema({vol.Required("entity_id"): str, vol.Optional("name"): str, vol.Optional("area"): str})

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        ent_reg = er.async_get(hass)
        entry = ent_reg.async_get(args["entity_id"])
        if entry is None:
            return {"error": "That entity can't be renamed here (it doesn't exist or has no unique id)."}
        changes: dict[str, Any] = {}
        if "name" in args:
            changes["name"] = args["name"].strip() or None
        if "area" in args:
            changes["area_id"] = _area_id(hass, args["area"])
        if not changes:
            return {"error": "Nothing to change: give a name or area."}
        entry = ent_reg.async_update_entity(entry.entity_id, **changes)
        areas = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
        return {"entity_id": entry.entity_id, "name": entry.name or entry.original_name, "area": areas.get(entry.area_id or "")}


class GetEquipmentAssignmentsTool(_AdminTool):
    name = "orchestra_get_equipment_assignments"
    description = (
        "Show which equipment follows Conductor through the Orchestra blueprints, and the "
        "kinds of equipment that can be assigned with what each needs."
    )

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        from .asset_assignments import ASSET_KINDS, automations_path, describe_assignments
        from .setup_agent import _read_automations

        automations = await hass.async_add_executor_job(_read_automations, automations_path(hass))
        return {
            "assigned": describe_assignments(automations),
            "kinds": {
                name: {
                    "title": kind.title,
                    "entity": f"{kind.main_input} ({' or '.join(kind.main_domains)})",
                    "settings": list(kind.entity_inputs) + list(kind.value_inputs),
                    "help": kind.help,
                }
                for name, kind in ASSET_KINDS.items()
            },
        }


class AssignEquipmentTool(_AdminTool):
    name = "orchestra_assign_equipment"
    description = (
        "Make a piece of equipment follow Conductor by filling its Orchestra blueprint. kind is "
        "air_conditioning, heat_pump, ev_charger or home_battery; entities is the entity it "
        "controls (for air_conditioning, every climate to drift, which replaces the list). "
        "settings holds the kind's optional extras, such as power_switch or charger_switch. "
        "Updates the existing automation for that equipment instead of adding a second one."
    )
    parameters = vol.Schema(
        {vol.Required("kind"): str, vol.Required("entities"): [str], vol.Optional("settings", default={}): dict}
    )

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        from .asset_assignments import AssignmentError, apply_assignment, automations_path

        def domain_of(entity_id: str) -> str | None:
            return entity_id.split(".", 1)[0] if hass.states.get(entity_id) else None

        return await _async_edit_automations(
            hass,
            lambda automations: apply_assignment(
                automations, args["kind"], list(args["entities"]), dict(args.get("settings") or {}), domain_of
            ),
            AssignmentError,
            automations_path(hass),
        )


class RemoveEquipmentAssignmentTool(_AdminTool):
    name = "orchestra_remove_equipment_assignment"
    description = (
        "Stop a piece of equipment following Conductor, by the automation_id from "
        "orchestra_get_equipment_assignments. Only Orchestra blueprint automations can be removed."
    )
    parameters = vol.Schema({vol.Required("automation_id"): str})

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        from .asset_assignments import AssignmentError, automations_path, remove_assignment

        return await _async_edit_automations(
            hass,
            lambda automations: (
                remove_assignment(automations, args["automation_id"]),
                {"removed": args["automation_id"]},
            ),
            AssignmentError,
            automations_path(hass),
        )


class CheckUpdatesTool(_AdminTool):
    name = "orchestra_check_updates"
    description = "Check GitHub for newer releases of HACS and Orchestra, and whether HACS is set up."

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        from .component_updates import COMPONENTS, UpdateError, async_check

        out = []
        for name in COMPONENTS:
            try:
                out.append(await async_check(hass, name))
            except UpdateError as err:
                out.append({"component": name, "error": str(err)})
        return {"components": out}


class InstallUpdateTool(_AdminTool):
    name = "orchestra_install_update"
    description = (
        "Install the latest release of hacs or orchestra from GitHub. HACS works without "
        "a restart until it is set up; otherwise the result says a restart is needed."
    )
    parameters = vol.Schema({vol.Required("component"): vol.In(["hacs", "orchestra"])})

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        from .component_updates import UpdateError, async_install

        try:
            return await async_install(hass, args["component"])
        except UpdateError as err:
            return {"error": str(err)}


class RestartHomeAssistantTool(_AdminTool):
    name = "orchestra_restart_home_assistant"
    description = (
        "Restart Home Assistant so installed updates take effect. Only after the person says "
        "yes: it takes a minute or two and this chat starts over."
    )

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        from homeassistant.helpers.event import async_call_later

        async def _restart(_now: Any) -> None:
            await hass.services.async_call("homeassistant", "restart")

        # A moment's delay so this answer reaches the person first
        async_call_later(hass, 5, _restart)
        return {"restarting": True, "message": "Home Assistant restarts in a few seconds."}


class ApplyThemeTool(_AdminTool):
    name = "orchestra_apply_theme"
    description = "Install the Conductor theme (light and dark) and make it Home Assistant's default theme."

    async def _async_run(self, hass: HomeAssistant, args: dict[str, Any]) -> dict[str, Any]:
        from .theme_installer import async_install_theme

        try:
            return await async_install_theme(hass, apply=True)
        except Exception as err:
            return {"error": f"The theme couldn't be applied: {err}"}


# ---------------------------------------------------------------------- helpers


async def _async_edit_automations(hass: HomeAssistant, edit: Callable, error_type: type, path: Any) -> dict[str, Any]:
    from .setup_agent import _read_automations, _write_automations

    automations = await hass.async_add_executor_job(_read_automations, path)
    try:
        updated, result = edit(automations)
    except error_type as err:
        return {"error": str(err)}
    await hass.async_add_executor_job(_write_automations, path, updated)
    if hass.services.has_service("automation", "reload"):
        await hass.services.async_call("automation", "reload", blocking=True)
    return result


def _area_id(hass: HomeAssistant, name: str) -> str | None:
    name = name.strip()
    if not name:
        return None
    area_reg = ar.async_get(hass)
    return (area_reg.async_get_area_by_name(name) or area_reg.async_create(name)).id


def _label_names(hass: HomeAssistant) -> dict[str, str]:
    try:
        from homeassistant.helpers import label_registry as lr
    except ImportError:
        return {}
    return {label.label_id: label.name for label in lr.async_get(hass).async_list_labels()}


def _label_id(hass: HomeAssistant, name: str) -> str:
    from homeassistant.helpers import label_registry as lr

    label_reg = lr.async_get(hass)
    return (label_reg.async_get_label_by_name(name.strip()) or label_reg.async_create(name.strip())).label_id


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
        from homeassistant.helpers import config_validation as cv

        return to_field_list(data_schema, custom_serializer=cv.custom_serializer)
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
    else:  # progress steps, such as HACS waiting for its GitHub device code to be entered
        out.update(
            flow_id=result.get("flow_id"),
            step=step_id,
            instructions=_fill(strings.get(f"{base}.progress.{result.get('progress_action')}"), placeholders),
            details=placeholders or None,
            message="Home Assistant is waiting on this step. Check again with orchestra_get_setup_form.",
        )
    return out
