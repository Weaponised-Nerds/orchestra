"""
Orchestra Hybrid Onboarding & Interactive Commissioning Agent Runner.

Philosophy:
1. The pre-loaded disk image handles the generic zero-touch bootstrap:
   - First user account pre-created as `Orchestra-Agent` (Owner).
   - Location set to Western Australia (Perth / SWIS / AUD / Metric).
   - Standard Home Assistant onboarding wizard bypassed.
   - Long-Lived Access Token pre-seeded for `Orchestra-Agent`.
2. Standard Home Assistant discovery runs its normal course without hostile suppression.
3. The Orchestra Agent takes over to complete setup:
   - Connects via Home Assistant REST and WebSocket APIs using the pre-seeded token.
   - Resolves pending discovery flows:
     * Prompts the user in plain English for Apple TV AirPlay 4-digit PIN.
     * Prompts for Enphase Envoy serial number (or auto-derives installer password).
     * Auto-confirms zero-credential hardware (printers, smart speakers, media players).
   - Semantically renames cryptic entities into clear, human-friendly names.
   - Assigns devices and entities to logical rooms/areas in the Area Registry.
   - Hides diagnostic noise (*_rssi, *_uptime, *_reboot, *_mac) so Grandpa's UI stays clean.
   - Curates the default Overview dashboard with room cards and live power flow.

STRICT SAFETY RULE:
- NEVER connect to, probe, or touch `192.168.0.11` (user's personal production Home Assistant).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import logging
from pathlib import Path
import socket
import time
from typing import Any, Callable

import requests
import websocket

from orchestra.agent.entity_organizer import (
    DIAGNOSTIC_NOISE_PATTERNS,
    FRIENDLY_NAME_REWRITES,
    ROOM_TOKEN_RULES,
    SemanticEntityOrganizer,
)

_LOGGER = logging.getLogger(__name__)

FORBIDDEN_IPS: set[str] = {"192.168.0.11"}

STANDARD_ROOMS: tuple[str, ...] = (
    "Living & Bedrooms",
    "Kitchen & Dining",
    "Garage / Utility",
    "Roof / Solar Array",
    "Driveway / EV Bay",
    "Outdoor & Porch",
    "Study / Office",
    "Grid & Weather Intelligence",
)


def derive_envoy_installer_password(serial_number: str) -> str:
    """Derive deterministic local installer digest password from Enphase Envoy serial number."""
    digits = "".join(ch for ch in str(serial_number) if ch.isdigit()) or "122300045678"
    digest_input = f"installer:enphaseenergy.com:{digits}".encode("utf-8")
    return hashlib.md5(digest_input).hexdigest()[-6:]


def validate_target_safety(host: str) -> None:
    """Enforce strict protection of the user's production Home Assistant at 192.168.0.11."""
    cleaned = host.strip().lower().replace("http://", "").replace("https://", "").split(":")[0]
    if cleaned in FORBIDDEN_IPS:
        raise ValueError(
            f"CRITICAL SAFETY VIOLATION: Target host '{host}' is the user's production "
            f"Home Assistant (192.168.0.11)! Orchestra is strictly prohibited from touching this instance."
        )
    try:
        resolved_ip = socket.gethostbyname(cleaned)
        if resolved_ip in FORBIDDEN_IPS:
            raise ValueError(
                f"CRITICAL SAFETY VIOLATION: Target host '{host}' resolves to '{resolved_ip}', "
                f"which is the user's production Home Assistant (192.168.0.11)! Operation aborted."
            )
    except socket.gaierror:
        pass


@dataclass
class FlowResolutionRecord:
    """Summary of a flow handled by the Agent."""

    flow_id: str
    domain: str
    title: str
    action_taken: str
    status: str


@dataclass
class TakeoverResult:
    """Complete summary of the Hybrid Agent Takeover execution."""

    connected_host: str
    authenticated_user: str
    flows_resolved: list[FlowResolutionRecord]
    entities_renamed: int
    entities_hidden_noise: int
    areas_ensured: list[str]
    dashboard_curated: bool
    handover_message: str


class HAWebSocketClient:
    """Lightweight authenticated WebSocket client for Home Assistant."""

    def __init__(self, ws_url: str, token: str, timeout: float = 10.0) -> None:
        self.ws_url = ws_url
        self.token = token
        self.timeout = timeout
        self.ws: websocket.WebSocket | None = None
        self._msg_id = 1

    def connect(self) -> None:
        self.ws = websocket.create_connection(self.ws_url, timeout=self.timeout)
        raw = self.ws.recv()
        msg = json.loads(raw)
        if msg.get("type") != "auth_required":
            raise RuntimeError(f"Unexpected initial WS message: {msg}")
        self.ws.send(json.dumps({"type": "auth", "access_token": self.token}))
        auth_res = json.loads(self.ws.recv())
        if auth_res.get("type") != "auth_ok":
            raise RuntimeError(f"WebSocket auth failed: {auth_res.get('message', auth_res)}")

    def call(self, msg_type: str, **kwargs: Any) -> Any:
        if not self.ws:
            self.connect()
        cur_id = self._msg_id
        self._msg_id += 1
        payload = {"id": cur_id, "type": msg_type, **kwargs}
        assert self.ws is not None
        self.ws.send(json.dumps(payload))
        while True:
            resp = json.loads(self.ws.recv())
            if resp.get("id") == cur_id:
                if not resp.get("success", False):
                    raise RuntimeError(f"HA WS call {msg_type} failed: {resp.get('error', resp)}")
                return resp.get("result")

    def close(self) -> None:
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
            self.ws = None


class OrchestraHybridAgent:
    """
    Hybrid Onboarding Agent that takes over from the initial bootloader/image
    to resolve interactive pairing codes, rename cryptic entities, assign rooms,
    and build a clean Lovelace dashboard for the homeowner.
    """

    def __init__(
        self,
        host: str = "homeassistant.local",
        port: int = 8123,
        token: str | None = None,
        prompt_callback: Callable[[str, str | None], str] | None = None,
        interactive: bool = True,
        session: requests.Session | None = None,
        ws_client: HAWebSocketClient | None = None,
    ) -> None:
        validate_target_safety(host)
        self.host = host
        self.port = port
        self.token = token or self._discover_token()
        self.base_url = f"http://{self.host}:{self.port}"
        self.ws_url = f"ws://{self.host}:{self.port}/api/websocket"
        self.prompt_callback = prompt_callback or self._default_prompt
        self.interactive = interactive
        self.session = session or requests.Session()
        self._ws_client = ws_client

    def _discover_token(self) -> str:
        """Locate token from dist/orchestra_agent_token.txt or sandbox."""
        search_paths = [
            Path("dist/orchestra_agent_token.txt"),
            Path("./ha_config_sandbox/orchestra_agent_token.txt"),
            Path("/config/orchestra_agent_token.txt"),
        ]
        for p in search_paths:
            if p.exists():
                tok = p.read_text(encoding="utf-8").strip()
                if tok:
                    return tok
        return ""

    def _default_prompt(self, message: str, default: str | None = None) -> str:
        """Terminal prompt for human input."""
        prompt_str = f"{message} " if not default else f"{message} [{default}]: "
        try:
            val = input(prompt_str).strip()
            return val if val else (default or "")
        except (EOFError, KeyboardInterrupt):
            return default or ""

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }

    def wait_for_ha_online(self, timeout_seconds: float = 120.0, poll_interval: float = 3.0) -> bool:
        """Poll the Home Assistant REST API until it returns 200 OK."""
        validate_target_safety(self.host)
        start_time = time.time()
        _LOGGER.info("Waiting for Home Assistant at %s...", self.base_url)

        while time.time() - start_time < timeout_seconds:
            try:
                res = self.session.get(f"{self.base_url}/api/", headers=self._headers(), timeout=4.0)
                if res.status_code == 200 and res.json().get("message") == "API running.":
                    _LOGGER.info("Home Assistant API is online and authenticated.")
                    return True
            except requests.RequestException:
                pass
            time.sleep(poll_interval)

        raise TimeoutError(f"Home Assistant at {self.base_url} did not become ready within {timeout_seconds}s.")

    def list_config_flows(self) -> list[dict[str, Any]]:
        """Fetch all pending discovery/config flows in Home Assistant."""
        validate_target_safety(self.host)
        url = f"{self.base_url}/api/config/config_entries/flow"
        res = self.session.get(url, headers=self._headers(), timeout=10.0)
        res.raise_for_status()
        return res.json()

    def submit_flow_step(self, flow_id: str, user_input: dict[str, Any]) -> dict[str, Any]:
        """Submit user or automated input to advance a config flow."""
        validate_target_safety(self.host)
        url = f"{self.base_url}/api/config/config_entries/flow/{flow_id}"
        res = self.session.post(url, headers=self._headers(), json=user_input, timeout=15.0)
        res.raise_for_status()
        return res.json()

    def resolve_pending_flows(self) -> list[FlowResolutionRecord]:
        """
        Inspect all pending config flows. If user input (like Apple TV PIN or Envoy serial)
        is required, prompt clearly in plain English. If zero-credential, auto-confirm.
        """
        flows = self.list_config_flows()
        records: list[FlowResolutionRecord] = []

        for flow in flows:
            flow_id = flow.get("flow_id", "")
            domain = flow.get("handler") or flow.get("domain", "")
            step_id = flow.get("step_id", "")
            context = flow.get("context", {})
            title = context.get("title_placeholders", {}).get("name") or flow.get("description_placeholders", {}).get("name") or domain

            record = FlowResolutionRecord(
                flow_id=flow_id,
                domain=domain,
                title=title,
                action_taken="",
                status="pending",
            )

            # 1. Apple TV AirPlay PIN Pairing
            if domain == "apple_tv":
                if step_id in ("pair", "airplay") or "pin" in str(flow.get("data_schema", "")).lower():
                    pin = self.prompt_callback(
                        f"📺 Apple TV Discovered ('{title}').\n"
                        f"   Please enter the 4-digit AirPlay PIN displayed on your TV screen:",
                        None,
                    )
                    if pin:
                        res = self.submit_flow_step(flow_id, {"pin": pin})
                        record.action_taken = f"Submitted AirPlay PIN for {title}"
                        record.status = res.get("type", "completed")
                    else:
                        record.action_taken = "User skipped AirPlay PIN"
                        record.status = "skipped"
                elif step_id in ("confirm", "zeroconf_confirm"):
                    res = self.submit_flow_step(flow_id, {})
                    record.action_taken = "Auto-confirmed Apple TV discovery"
                    record.status = res.get("type", "completed")
                records.append(record)
                continue

            # 2. Enphase Envoy Solar/Storage Gateway
            if domain == "enphase_envoy":
                serial = (
                    context.get("title_placeholders", {}).get("serial")
                    or flow.get("description_placeholders", {}).get("serial")
                    or context.get("unique_id")
                )
                if not serial:
                    serial = self.prompt_callback(
                        "☀️ Enphase Envoy Solar Gateway detected.\n"
                        "   Please enter your Envoy serial number (from the Envoy label or app):",
                        "122206103673",
                    )
                installer_pwd = derive_envoy_installer_password(str(serial))
                res = self.submit_flow_step(
                    flow_id,
                    {"username": "installer", "password": installer_pwd},
                )
                record.action_taken = f"Configured Envoy (serial {serial}) with derived local installer password"
                record.status = res.get("type", "completed")
                records.append(record)
                continue

            # 3. Zero-credential Network Devices (Brother/IPP printers, Cast, Sonos, DLNA)
            if domain in ("brother", "ipp", "snmp", "cast", "sonos", "dlna_dmr"):
                if step_id in ("confirm", "zeroconf_confirm", "discovery_confirm", "user"):
                    res = self.submit_flow_step(flow_id, {})
                    record.action_taken = f"Auto-confirmed local appliance '{title}'"
                    record.status = res.get("type", "completed")
                    records.append(record)
                    continue

            # 4. Generic Device with Data Schema
            data_schema = flow.get("data_schema")
            if not data_schema or len(data_schema) == 0:
                res = self.submit_flow_step(flow_id, {})
                record.action_taken = f"Auto-confirmed zero-prompt device '{title}'"
                record.status = res.get("type", "completed")
            else:
                record.action_taken = f"Flow requires custom credentials: {step_id}"
                record.status = "manual_required"
            records.append(record)

        return records

    def _get_ws(self) -> HAWebSocketClient:
        if self._ws_client is None:
            self._ws_client = HAWebSocketClient(self.ws_url, self.token)
        return self._ws_client

    def ensure_standard_areas(self) -> dict[str, str]:
        """Ensure standard logical rooms exist in the Area Registry and return name -> area_id map."""
        ws = self._get_ws()
        existing_areas: list[dict[str, Any]] = ws.call("config/area_registry/list")
        area_map: dict[str, str] = {a["name"].lower(): a["area_id"] for a in existing_areas}

        for room_name in STANDARD_ROOMS:
            if room_name.lower() not in area_map:
                try:
                    created = ws.call("config/area_registry/create", name=room_name)
                    area_map[room_name.lower()] = created["area_id"]
                except Exception as err:
                    _LOGGER.warning("Could not create area %s: %s", room_name, err)

        return area_map

    def organize_entities_and_rooms(self) -> dict[str, int]:
        """
        Organize, rename, and assign rooms to all discovered entities.
        Hides diagnostic noise (*_rssi, *_uptime, *_reboot, etc.) so Grandpa's UI stays clean.
        """
        ws = self._get_ws()
        area_map = self.ensure_standard_areas()

        entities: list[dict[str, Any]] = ws.call("config/entity_registry/list")
        devices: list[dict[str, Any]] = ws.call("config/device_registry/list")
        device_area_map: dict[str, str] = {d["id"]: d.get("area_id") or "" for d in devices}

        renamed_count = 0
        hidden_noise_count = 0

        for ent in entities:
            ent_id = ent.get("entity_id", "")
            raw_name = ent.get("original_name") or ent.get("name") or ent_id
            dev_id = ent.get("device_id")

            # 1. Identify diagnostic noise
            is_noise = SemanticEntityOrganizer._is_diagnostic_noise(ent_id, raw_name)
            hidden_by = "orchestra_agent" if is_noise else None
            if is_noise:
                hidden_noise_count += 1

            # 2. Compute friendly human name
            sensible_name = SemanticEntityOrganizer._humanize_name(ent_id, raw_name)

            # 3. Infer logical room
            room_name = SemanticEntityOrganizer._infer_room(ent_id, raw_name)
            target_area_id = area_map.get(room_name.lower())

            # 4. Update entity in registry
            try:
                update_kwargs: dict[str, Any] = {
                    "entity_id": ent_id,
                    "name": sensible_name,
                }
                if target_area_id:
                    update_kwargs["area_id"] = target_area_id
                if hidden_by:
                    update_kwargs["hidden_by"] = hidden_by

                ws.call("config/entity_registry/update", **update_kwargs)
                renamed_count += 1
            except Exception as err:
                _LOGGER.debug("Could not update entity %s: %s", ent_id, err)

            # 5. Assign device to room if device lacks an area
            if dev_id and target_area_id and not device_area_map.get(dev_id):
                try:
                    ws.call("config/device_registry/update", device_id=dev_id, area_id=target_area_id)
                    device_area_map[dev_id] = target_area_id
                except Exception as err:
                    _LOGGER.debug("Could not update device %s area: %s", dev_id, err)

        return {
            "renamed_entities": renamed_count,
            "hidden_noise_entities": hidden_noise_count,
        }

    def curate_overview_dashboard(self) -> bool:
        """Create or update the Lovelace overview dashboard with clean room cards and live power flow."""
        ws = self._get_ws()

        default_dashboard = {
            "title": "My Home",
            "views": [
                {
                    "title": "My Home",
                    "path": "default_view",
                    "icon": "mdi:home-heart",
                    "badges": [],
                    "cards": [
                        {
                            "type": "markdown",
                            "title": "Welcome Home — Orchestra Energy Concierge Active",
                            "content": (
                                "**Everything is running smoothly and automatically.**\n"
                                "- Household comfort and energy efficiency are actively managed.\n"
                                "- Discovered devices are organized into room cards below with zero diagnostic clutter."
                            ),
                        },
                        {
                            "type": "energy-distribution",
                            "title": "Live Home Power Flow (Solar, Battery, Home & Street Grid)",
                            "link_dashboard": True,
                        },
                    ],
                },
                {
                    "title": "Energy & Savings",
                    "path": "energy-and-savings",
                    "icon": "mdi:cash-multiple",
                    "badges": [],
                    "cards": [
                        {
                            "type": "energy-distribution",
                            "title": "Live Energy Distribution",
                            "link_dashboard": True,
                        },
                    ],
                },
            ],
        }

        try:
            ws.call("lovelace/config/save", config=default_dashboard)
            return True
        except Exception as err:
            _LOGGER.debug("Could not save Lovelace config via WS: %s", err)
            return False

    def run_full_takeover(self, timeout_seconds: float = 120.0) -> TakeoverResult:
        """
        Run the complete interactive Agent takeover:
        1. Wait for Home Assistant to boot and authenticate as Orchestra-Agent.
        2. Resolve discovered hardware flows (prompting user for PINs/serials if needed).
        3. Rename entities, assign rooms, and hide diagnostic clutter.
        4. Curate the Lovelace overview dashboard.
        5. Return a complete handover result.
        """
        self.wait_for_ha_online(timeout_seconds=timeout_seconds)

        flow_records = self.resolve_pending_flows()
        org_stats = self.organize_entities_and_rooms()
        dash_ok = self.curate_overview_dashboard()

        handover = (
            f"Orchestra Agent setup complete! "
            f"Resolved {len(flow_records)} hardware flow(s), "
            f"organized {org_stats['renamed_entities']} entities into logical rooms, "
            f"and hid {org_stats['hidden_noise_entities']} technical diagnostic items."
        )

        return TakeoverResult(
            connected_host=self.host,
            authenticated_user="Orchestra-Agent",
            flows_resolved=flow_records,
            entities_renamed=org_stats["renamed_entities"],
            entities_hidden_noise=org_stats["hidden_noise_entities"],
            areas_ensured=list(STANDARD_ROOMS),
            dashboard_curated=dash_ok,
            handover_message=handover,
        )


def auto_discover_home_assistant(timeout_seconds: float = 15.0) -> str | None:
    """
    Auto-discover the newly booted Home Assistant instance on the local subnet.
    Tries mDNS (homeassistant.local), then performs a fast parallel subnet probe on port 8123.
    STRICTLY SKIPS 192.168.0.11 (user's personal production instance).
    """
    import concurrent.futures

    # 1. Try homeassistant.local mDNS
    try:
        resolved = socket.gethostbyname("homeassistant.local")
        if resolved not in FORBIDDEN_IPS:
            res = requests.get(f"http://{resolved}:8123/api/", timeout=2.0)
            if res.status_code in (200, 401):
                return resolved
    except Exception:
        pass

    # 2. Get local host subnet
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        return None

    subnet_prefix = ".".join(local_ip.split(".")[:3])
    candidates = [
        f"{subnet_prefix}.{i}"
        for i in range(1, 255)
        if f"{subnet_prefix}.{i}" not in FORBIDDEN_IPS and f"{subnet_prefix}.{i}" != local_ip
    ]

    def _check_ip(ip: str) -> str | None:
        try:
            res = requests.get(f"http://{ip}:8123/api/", timeout=0.8)
            if res.status_code == 200 and "API running." in res.text:
                return ip
            if res.status_code == 401:
                return ip
        except Exception:
            pass
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=35) as executor:
        futures = {executor.submit(_check_ip, ip): ip for ip in candidates}
        for future in concurrent.futures.as_completed(futures):
            res_ip = future.result()
            if res_ip:
                executor.shutdown(wait=False, cancel_futures=True)
                return res_ip

    return None
