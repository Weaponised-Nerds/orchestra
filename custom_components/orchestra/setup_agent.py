"""
Orchestra setup agent: finishes a fresh Orchestra image's setup inside Home Assistant.

It only runs where the image left a setup plan (`.storage/orchestra.setup_plan`), so a
normal HACS install never has flows confirmed, devices moved or passwords changed.

As soon as Home Assistant has started, it creates the human logins from the plan, so they
work from the first minute. Then, once Home Assistant's own discovery has settled, it:
1. sets up the integrations stock onboarding would have (weather, radio, TTS, shopping list),
2. confirms every discovered device that needs no input, and puts the rest on a punch list,
3. sorts devices into rooms and labels them by kind of equipment,
4. fills the Conductor asset blueprints with the matching entities,
5. retires the image's shared agent credentials: the Orchestra-Agent account gets a random
   password nobody knows, and a fresh long-lived token (written to
   `<config>/orchestra_agent_token.txt`) replaces the one baked into the image.

It then re-checks every few minutes for a day, so late discoveries are picked up and punch
list items tick themselves off once the person finishes them.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
import logging
from pathlib import Path
import secrets
import time
from typing import Any, Callable

from homeassistant.components import persistent_notification
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store

try:
    from homeassistant.helpers import label_registry as lr
except ImportError:  # Home Assistant before 2024.4
    lr = None  # type: ignore

from .const import DOMAIN
from .setup_planner import (
    DISCOVERY_SOURCES,
    ORCHESTRA_LABELS,
    EntityInfo,
    PunchItem,
    asset_label,
    decide_flow_step,
    infer_area,
    plan_asset_automations,
)

_LOGGER = logging.getLogger(__name__)

SETUP_PLAN_KEY = "orchestra.setup_plan"
SETUP_STATE_KEY = "orchestra.setup"
STORAGE_VERSION = 1

AGENT_USERNAME = "orchestra-agent"
ONBOARDING_INTEGRATIONS = ("google_translate", "met", "radio_browser", "shopping_list")

# How long to let Home Assistant's discovery run before the agent takes over
DEFAULT_MIN_WAIT_SECONDS = 180
DEFAULT_QUIET_SECONDS = 90
DEFAULT_MAX_WAIT_SECONDS = 900
DEFAULT_RECHECK_MINUTES = 10
DEFAULT_RECHECK_HOURS = 24
MAX_STEPS_PER_FLOW = 4

PUNCH_HACS = "hacs"
PUNCH_NO_USERS = "users"
NOTIFICATION_ID = "orchestra_setup"

UpdateListener = Callable[[], None]


class OrchestraSetupAgent:
    """Runs the image's first-boot setup and keeps the punch list."""

    def __init__(self, hass: HomeAssistant, plan: dict[str, Any], state: dict[str, Any]) -> None:
        self.hass = hass
        self.plan = plan
        self.state = state
        self.state.setdefault("punch_list", [])
        self.state.setdefault("handled_flows", [])
        self.state.setdefault("done", [])
        self._plan_store: Store = Store(hass, STORAGE_VERSION, SETUP_PLAN_KEY, private=True)
        self._state_store: Store = Store(hass, STORAGE_VERSION, SETUP_STATE_KEY)
        self._listeners: list[UpdateListener] = []
        self._lock = asyncio.Lock()
        self._unsub_recheck: Callable[[], None] | None = None
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    async def async_load(cls, hass: HomeAssistant) -> OrchestraSetupAgent | None:
        """The agent for this install, or None when the image left no setup plan."""
        plan = await Store(hass, STORAGE_VERSION, SETUP_PLAN_KEY, private=True).async_load()
        if not plan:
            return None
        state = await Store(hass, STORAGE_VERSION, SETUP_STATE_KEY).async_load() or {}
        return cls(hass, plan, state)

    @callback
    def async_start(self) -> None:
        """Start once Home Assistant has finished starting."""

        @callback
        def _started(_hass: HomeAssistant) -> None:
            self._task = self.hass.async_create_background_task(
                self._async_first_run(), "orchestra_setup_agent"
            )

        async_at_started(self.hass, _started)
        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._async_stop)

    async def _async_stop(self, _event: Any = None) -> None:
        if self._unsub_recheck:
            self._unsub_recheck()
            self._unsub_recheck = None
        if self._task and not self._task.done():
            self._task.cancel()

    async def _async_first_run(self) -> None:
        if not self.state.get("completed_at"):
            created = await self.async_create_logins()
            if created:
                await self._async_save_state()
            self._async_notify_starting(created)
            await self._async_wait_for_discovery()
            await self.async_run_pass(first=True)
        self._schedule_rechecks()

    def _schedule_rechecks(self) -> None:
        completed = float(self.state.get("completed_at") or time.time())
        hours = float(self.plan.get("recheck_hours", DEFAULT_RECHECK_HOURS))
        if time.time() - completed > hours * 3600 and not self.open_items:
            return
        minutes = float(self.plan.get("recheck_minutes", DEFAULT_RECHECK_MINUTES))

        async def _recheck(_now: Any) -> None:
            await self.async_run_pass(first=False)

        self._unsub_recheck = async_track_time_interval(
            self.hass, _recheck, timedelta(minutes=minutes)
        )

    async def _async_wait_for_discovery(self) -> None:
        """Wait until discovery has run for a while and no new flows have appeared lately."""
        min_wait = float(self.plan.get("min_wait_seconds", DEFAULT_MIN_WAIT_SECONDS))
        quiet = float(self.plan.get("quiet_seconds", DEFAULT_QUIET_SECONDS))
        max_wait = float(self.plan.get("max_wait_seconds", DEFAULT_MAX_WAIT_SECONDS))
        poll = max(1.0, min(15.0, quiet / 3))
        start = time.monotonic()
        last_ids: set[str] = set()
        last_change = start
        while True:
            ids = {f["flow_id"] for f in self.hass.config_entries.flow.async_progress()}
            now = time.monotonic()
            if ids != last_ids:
                last_ids, last_change = ids, now
            elapsed = now - start
            if elapsed >= max_wait or (elapsed >= min_wait and now - last_change >= quiet):
                return
            await asyncio.sleep(poll)

    # ------------------------------------------------------------------ the pass

    async def async_run_pass(self, first: bool) -> dict[str, Any]:
        """One full pass of the agent. Safe to repeat."""
        async with self._lock:
            report: dict[str, Any] = {}
            if first:
                report["onboarding"] = await self._async_onboarding_integrations()
            report["flows"] = await self.async_resolve_discovery_flows()
            if report["flows"]["confirmed"]:
                # Give the new entries a moment to create their devices and entities
                await asyncio.sleep(float(self.plan.get("entry_settle_seconds", 20)))
            report["organized"] = await self.async_organize_devices()
            report["automations"] = await self.async_prefill_automations()
            self._async_check_hacs()
            if first:
                report["users"] = list(self.state.get("logins_created", []))
                await self.async_retire_agent_credentials()
                self.state["completed_at"] = time.time()
            self._async_tick_off_finished_items()
            await self._async_save_state()
            if first:
                self._async_notify(report)
            self._async_update_listeners()
            return report

    async def _async_onboarding_integrations(self) -> list[str]:
        """The integrations stock onboarding sets up, which skipping it would otherwise lose."""
        started: list[str] = []
        for domain in ONBOARDING_INTEGRATIONS:
            if self.hass.config_entries.async_entries(domain):
                continue
            try:
                await self.hass.config_entries.flow.async_init(domain, context={"source": "onboarding"})
                started.append(domain)
            except Exception as err:  # an onboarding extra must never stop setup
                _LOGGER.debug("Orchestra could not set up %s: %s", domain, err)
        return started

    async def async_resolve_discovery_flows(self) -> dict[str, list[str]]:
        """Confirm discovered devices that need no input; punch-list the rest."""
        flow_mgr = self.hass.config_entries.flow
        handled = set(self.state["handled_flows"])
        result: dict[str, list[str]] = {"confirmed": [], "asked": [], "skipped": []}
        for flow in flow_mgr.async_progress():
            flow_id = flow["flow_id"]
            source = (flow.get("context") or {}).get("source")
            if source not in DISCOVERY_SOURCES or flow_id in handled:
                continue
            outcome = await self._async_drive_flow(flow)
            result[outcome].append(f"{flow.get('handler')}:{flow_id}")
            if outcome != "skipped":
                handled.add(flow_id)
        self.state["handled_flows"] = sorted(handled)[-500:]
        return result

    async def _async_drive_flow(self, flow: dict[str, Any]) -> str:
        flow_mgr = self.hass.config_entries.flow
        flow_id = flow["flow_id"]
        try:
            step = await flow_mgr.async_configure(flow_id)
            for _ in range(MAX_STEPS_PER_FLOW):
                if step.get("type") in ("create_entry", "abort"):
                    return "confirmed" if step.get("type") == "create_entry" else "skipped"
                decision = decide_flow_step(flow, step)
                if decision.action != "submit":
                    if decision.punch:
                        self._add_punch(decision.punch, domain=flow.get("handler"))
                        return "asked"
                    return "skipped"
                step = await flow_mgr.async_configure(flow_id, decision.user_input)
        except Exception as err:  # unknown flow ids, validation errors, flaky devices
            _LOGGER.info("Orchestra left %s for later: %s", flow.get("handler"), err)
            return "skipped"
        return "skipped"

    async def async_organize_devices(self) -> dict[str, int]:
        """Put each device without a room into one, and label it by what it is."""
        dev_reg = dr.async_get(self.hass)
        area_reg = ar.async_get(self.hass)
        entities = self._entity_infos()
        by_device: dict[str, list[EntityInfo]] = {}
        for info in entities:
            if info.device_id:
                by_device.setdefault(info.device_id, []).append(info)

        label_ids: dict[str, str] = {}
        if lr is not None:
            label_reg = lr.async_get(self.hass)
            for name in ORCHESTRA_LABELS:
                existing = label_reg.async_get_label_by_name(name)
                label_ids[name] = (existing or label_reg.async_create(name)).label_id

        rooms = labelled = 0
        # Every device that belongs to an integration (newer cores no longer let `devices` be read as a dict)
        devices = {
            device.id: device
            for entry in self.hass.config_entries.async_entries()
            for device in dr.async_entries_for_config_entry(dev_reg, entry.entry_id)
        }
        for device in devices.values():
            if device.disabled_by or getattr(device, "entry_type", None) == dr.DeviceEntryType.SERVICE:
                continue
            infos = by_device.get(device.id, [])
            changes: dict[str, Any] = {}
            if device.area_id is None:
                text = " ".join([device.name_by_user or device.name or "", device.model or ""] + [i.text for i in infos])
                area_name = infer_area(text.replace(" ", "_"))
                if area_name:
                    changes["area_id"] = area_reg.async_get_or_create(area_name).id
            label = asset_label(infos)
            # Only label unlabelled devices, so labels a person changed stay changed
            if label and label in label_ids and not device.labels:
                changes["labels"] = {label_ids[label]}
            if changes:
                dev_reg.async_update_device(device.id, **changes)
                rooms += "area_id" in changes
                labelled += "labels" in changes
        return {"rooms": rooms, "labels": labelled}

    async def async_prefill_automations(self) -> list[str]:
        """Create blueprint automations for the home's A/Cs, EV chargers, heat pumps and batteries."""
        path = Path(self.hass.config.path("automations.yaml"))
        existing = await self.hass.async_add_executor_job(_read_automations, path)
        existing_ids = {str(a.get("id")) for a in existing if isinstance(a, dict)}
        existing_ids |= set(self.state.get("automations_created", []))
        new, notes = plan_asset_automations(self._entity_infos(), existing_ids)
        if not new:
            return []
        await self.hass.async_add_executor_job(_write_automations, path, existing + new)
        self.state.setdefault("automations_created", []).extend(a["id"] for a in new)
        self.state.setdefault("automation_notes", []).extend(notes)
        if self.hass.services.has_service("automation", "reload"):
            await self.hass.services.async_call("automation", "reload", blocking=True)
        return notes

    def _async_check_hacs(self) -> None:
        """HACS ships in the image but needs a GitHub sign-in that only a person can do."""
        if not (Path(self.hass.config.path("custom_components", "hacs")).exists()):
            return
        if self.hass.config_entries.async_entries("hacs"):
            return
        self._add_punch(
            PunchItem(
                PUNCH_HACS,
                "Connect HACS to GitHub",
                "HACS is installed so Orchestra can update itself. Open Settings → Devices & "
                "services → Add integration → HACS, tick the boxes and sign in to GitHub with "
                "the code it shows. Then add Weaponised-Nerds/orchestra under HACS → Custom "
                "repositories (type: Integration).",
            ),
            domain="hacs",
        )

    async def async_setup_users(self) -> list[str]:
        """Create the plan's human logins, then retire the image's agent credentials."""
        created = await self.async_create_logins()
        await self.async_retire_agent_credentials()
        return created

    async def async_create_logins(self) -> list[str]:
        """Create the plan's human logins. Safe to repeat: existing usernames are left alone."""
        users = [u for u in self.plan.get("users", []) if u.get("username") and u.get("password")]
        if not users:
            return []
        provider = await self._async_login_provider()
        if provider is None:
            return []

        created: list[str] = []
        for spec in users:
            username = str(spec["username"]).strip().lower()
            try:
                await provider.async_add_auth(username, str(spec["password"]))
            except Exception as err:  # most often: the username already exists
                _LOGGER.info("Orchestra did not add login %s: %s", username, err)
                continue
            group = "system-admin" if spec.get("admin", True) else "system-users"
            user = await self.hass.auth.async_create_user(spec.get("name") or username, group_ids=[group])
            credentials = await provider.async_get_or_create_credentials({"username": username})
            await self.hass.auth.async_link_user(user, credentials)
            await self._async_create_person(user.name, user.id)
            created.append(username)

        self.state["logins_created"] = sorted(set(self.state.get("logins_created", [])) | set(created))
        # The plan's passwords were only needed once
        self.plan["users"] = [{k: v for k, v in u.items() if k != "password"} for u in self.plan.get("users", [])]
        await self._plan_store.async_save(self.plan)
        return created

    async def async_retire_agent_credentials(self) -> None:
        """
        Once setup has finished, swap the image's shared agent password and token for new ones.

        Only done once a human login exists, so nobody is ever locked out.
        """
        if self.state.get("users_done"):
            return
        if not self.state.get("logins_created"):
            self._add_punch(
                PunchItem(
                    PUNCH_NO_USERS,
                    "Create your own login",
                    "No logins were set when this image was built, so the Orchestra-Agent "
                    "password has not been changed. Add a person under Settings → People.",
                ),
            )
            return
        provider = await self._async_login_provider()
        if provider is None:
            return

        try:
            await provider.async_change_password(AGENT_USERNAME, secrets.token_urlsafe(32))
        except Exception as err:
            _LOGGER.warning("Orchestra could not change the agent password: %s", err)
        else:
            self.state["agent_password_rotated_at"] = time.time()
        await self._async_reissue_agent_token()
        await self.hass.async_add_executor_job(_remove_file, Path(self.hass.config.path("orchestra_agent_credentials.json")))
        self.state["users_done"] = True

    async def _async_reissue_agent_token(self) -> None:
        """Give the agent a new long-lived token and revoke the one every image shares."""
        from homeassistant.auth.models import TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN

        agent = None
        for user in await self.hass.auth.async_get_users():
            if any(c.data.get("username") == AGENT_USERNAME for c in user.credentials):
                agent = user
                break
        if agent is None:
            _LOGGER.warning("Orchestra could not find the agent account to re-issue its token")
            return
        old = [t for t in agent.refresh_tokens.values() if t.token_type == TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN]
        refresh = await self.hass.auth.async_create_refresh_token(
            agent,
            client_name=f"Orchestra agent ({time.strftime('%Y-%m-%d')})",
            token_type=TOKEN_TYPE_LONG_LIVED_ACCESS_TOKEN,
            access_token_expiration=timedelta(days=3650),
        )
        token = self.hass.auth.async_create_access_token(refresh)
        await self.hass.async_add_executor_job(
            _write_private_file, Path(self.hass.config.path("orchestra_agent_token.txt")), token + "\n"
        )
        for stale in old:
            self.hass.auth.async_remove_refresh_token(stale)
        self.state["agent_token_reissued_at"] = time.time()

    async def _async_login_provider(self) -> Any:
        provider = next((p for p in self.hass.auth.auth_providers if p.type == "homeassistant"), None)
        if provider is None:
            _LOGGER.warning("Orchestra could not find the Home Assistant login provider")
            return None
        await provider.async_initialize()
        return provider

    async def _async_create_person(self, name: str, user_id: str) -> None:
        try:
            from homeassistant.components import person
            from homeassistant.setup import async_setup_component

            if await async_setup_component(self.hass, "person", {}):
                await person.async_create_person(self.hass, name, user_id=user_id)
        except Exception as err:
            _LOGGER.debug("Orchestra could not create a person for %s: %s", name, err)

    # ------------------------------------------------------------------ punch list

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.state["punch_list"]

    @property
    def open_items(self) -> list[dict[str, Any]]:
        return [i for i in self.items if not i.get("done")]

    def _add_punch(self, punch: PunchItem, domain: str | None = None) -> None:
        for item in self.items:
            if item["key"] == punch.key:
                return
        self.items.append(
            {
                "key": punch.key,
                "summary": punch.summary,
                "description": punch.description,
                "domain": domain,
                "done": False,
                "added_at": time.time(),
            }
        )

    @callback
    def _async_tick_off_finished_items(self) -> None:
        """Close items the person has finished: the flow is gone and its integration is set up."""
        live_flows = {f["flow_id"] for f in self.hass.config_entries.flow.async_progress()}
        for item in self.open_items:
            key, domain = item["key"], item.get("domain")
            if key.startswith("flow:") and key[5:] not in live_flows:
                if domain and self.hass.config_entries.async_entries(domain):
                    item["done"] = True
            elif key == PUNCH_HACS and self.hass.config_entries.async_entries("hacs"):
                item["done"] = True

    async def async_set_item_done(self, key: str, done: bool) -> None:
        for item in self.items:
            if item["key"] == key:
                item["done"] = done
        await self._async_save_state()
        self._async_update_listeners()

    async def async_remove_items(self, keys: list[str]) -> None:
        self.state["punch_list"] = [i for i in self.items if i["key"] not in keys]
        await self._async_save_state()
        self._async_update_listeners()

    @callback
    def async_add_listener(self, listener: UpdateListener) -> Callable[[], None]:
        self._listeners.append(listener)

        @callback
        def _remove() -> None:
            self._listeners.remove(listener)

        return _remove

    @callback
    def _async_update_listeners(self) -> None:
        for listener in list(self._listeners):
            listener()

    # ------------------------------------------------------------------ helpers

    def _entity_infos(self) -> list[EntityInfo]:
        ent_reg = er.async_get(self.hass)
        dev_reg = dr.async_get(self.hass)
        infos: list[EntityInfo] = []
        for state in self.hass.states.async_all():
            if state.domain in ("automation", "todo"):
                continue
            entry = ent_reg.async_get(state.entity_id)
            if entry and (entry.disabled_by or entry.platform == DOMAIN):
                continue
            device = dev_reg.async_get(entry.device_id) if entry and entry.device_id else None
            infos.append(
                EntityInfo(
                    entity_id=state.entity_id,
                    name=str(state.attributes.get("friendly_name") or ""),
                    device_id=device.id if device else None,
                    device_name=(device.name_by_user or device.name or "") if device else "",
                    via_device_id=device.via_device_id if device else None,
                    config_entry_id=entry.config_entry_id if entry else None,
                    platform=entry.platform if entry else "",
                    unit=state.attributes.get("unit_of_measurement"),
                    attributes=dict(state.attributes),
                )
            )
        return infos

    async def _async_save_state(self) -> None:
        await self._state_store.async_save(self.state)

    @callback
    def _async_notify_starting(self, created: list[str]) -> None:
        logins = self.state.get("logins_created", [])
        lines = ["Orchestra is waiting for Home Assistant to finish finding your devices, then it "
                 "will set them up. This takes up to 15 minutes."]
        if logins:
            lines.append("Your logins are ready: " + ", ".join(logins) + ".")
        persistent_notification.async_create(
            self.hass, "\n\n".join(lines), title="Orchestra is setting up your home", notification_id=NOTIFICATION_ID
        )

    @callback
    def _async_notify(self, report: dict[str, Any]) -> None:
        flows = report.get("flows", {})
        lines = [
            f"Added {len(flows.get('confirmed', []))} discovered devices on its own.",
            f"Sorted {report.get('organized', {}).get('rooms', 0)} devices into rooms.",
        ]
        notes = report.get("automations") or []
        if notes:
            lines.append("Connected these to Conductor:\n" + "\n".join(f"- {n}" for n in notes))
        users = report.get("users") or []
        if users:
            lines.append("Created logins: " + ", ".join(users) + ".")
        if self.state.get("users_done"):
            lines.append("The Orchestra-Agent password and token from the image have been replaced.")
        open_items = self.open_items
        if open_items:
            lines.append(
                f"**{len(open_items)} things need you.** They are on the Orchestra setup "
                "punch list (To-do lists → Orchestra setup):\n"
                + "\n".join(f"- {i['summary']}" for i in open_items)
            )
        else:
            lines.append("Nothing needs you. Your home is ready.")
        persistent_notification.async_create(
            self.hass,
            "\n\n".join(lines),
            title="Orchestra has set up your home",
            notification_id=NOTIFICATION_ID,
        )


def _read_automations(path: Path) -> list[Any]:
    import yaml

    if not path.exists():
        return []
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or []
    return data if isinstance(data, list) else []


def _write_automations(path: Path, automations: list[Any]) -> None:
    import yaml

    path.write_text(yaml.safe_dump(automations, sort_keys=False, allow_unicode=True), encoding="utf-8")


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _write_private_file(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
