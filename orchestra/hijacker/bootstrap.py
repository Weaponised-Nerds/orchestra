"""
Home Assistant Process Hijacker (Bootstrap & Onboarding Bypass).

Eliminates the manual Home Assistant first-run wizard, generates local system
credentials, pre-seeds Western Australian / microgrid locale settings, and
suppresses cloud-tethered discovery flows in favor of Orchestra's local adapters.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import secrets
from typing import Any
import uuid

from orchestra.config import OrchestraConfig


# Cloud integrations that Home Assistant's default zeroconf/dhcp/ssdp scanner
# tries to prompt the user to configure with cloud accounts. Orchestra suppresses
# these and replaces them with direct local config entries.
CLOUD_DOMAINS_TO_SUPPRESS: list[str] = [
    "tuya",               # Replaced by localtuya with Orchestra fake-cloud local_key extraction
    "sensibo",            # Replaced by local homekit_controller (_hap._tcp) or local HTTP bridge
    "tesla_fleet",        # Replaced by local Powerwall HTTPS / TEDAPI + local Wall Connector API
    "smartthings_cloud",  # Replaced by local SmartThings Edge LAN / Matter bridge
    "alexa_cloud",        # Replaced by local Matter / Emulated Hue UPnP + local RTSP
    "google_cloud",       # Replaced by local Matter bridge + Local Home SDK mDNS
    "myair_cloud",        # Replaced by local Advantage Air HTTP port 2025
    "rheem_econet",       # Replaced by local Modbus TCP / EcoNet MQTT intercept
    "rinnai_cloud",       # Replaced by local Modbus TCP / Tuya OEM local key
    "stiebel_cloud",      # Replaced by local ISG Servicewelt Modbus TCP port 502
]


class HAProcessHijacker:
    """
    Hijacks the Home Assistant initialization and onboarding lifecycle so the
    Zenith Micro-Node boots straight into a headless, pre-authenticated,
    cloud-liberated state.
    """

    def __init__(self, config: OrchestraConfig) -> None:
        self.config = config
        self.storage_dir: Path = self.config.ha_config_dir / ".storage"

    def ensure_directories(self) -> None:
        """Create the Home Assistant configuration and `.storage` directories."""
        self.config.ha_config_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def bypass_onboarding(self) -> dict[str, Any]:
        """
        Hijack Home Assistant's onboarding state machine by pre-completing
        all onboarding steps (`user`, `core_config`, `analytics`, `integration`)
        and provisioning a deterministic local admin/daemon credential pair.
        """
        self.ensure_directories()

        # 1. Mark all 4 HA onboarding steps complete in `.storage/onboarding`
        onboarding_payload = {
            "version": 4,
            "minor_version": 1,
            "key": "onboarding",
            "data": {
                "done": [
                    "user",
                    "core_config",
                    "analytics",
                    "integration",
                ]
            },
        }
        self._write_storage_file("onboarding", onboarding_payload)

        # 2. Seed `.storage/core.config` with Western Australian (SWIS) locale & metric units
        core_config_payload = {
            "version": 1,
            "minor_version": 3,
            "key": "core.config",
            "data": {
                "latitude": self.config.latitude,
                "longitude": self.config.longitude,
                "elevation": self.config.elevation_m,
                "unit_system_v2": "metric",
                "location_name": self.config.location_name,
                "time_zone": self.config.time_zone,
                "external_url": None,
                "internal_url": f"http://{self.config.node_hostname}:8123",
                "currency": self.config.currency,
                "country": self.config.country,
                "language": "en-AU",
            },
        }
        self._write_storage_file("core.config", core_config_payload)

        # 3. Disable cloud analytics / telemetry phone-home in `.storage/core.analytics`
        analytics_payload = {
            "version": 1,
            "minor_version": 1,
            "key": "core.analytics",
            "data": {
                "preferences": {
                    "base": False,
                    "diagnostics": False,
                    "usage": False,
                    "statistics": False,
                },
                "onboarded": True,
                "uuid": uuid.uuid4().hex,
            },
        }
        self._write_storage_file("core.analytics", analytics_payload)

        # 4. Provision headless Orchestra system owner & long-lived token in `.storage/auth`
        auth_summary = self._seed_local_auth()

        # 5. Write `configuration.yaml` loading the `orchestra` custom component
        self._write_configuration_yaml()

        return {
            "onboarding_bypassed": True,
            "storage_dir": str(self.storage_dir),
            "admin_user_id": auth_summary["user_id"],
            "daemon_access_token": auth_summary["access_token"],
            "suppressed_cloud_domains": CLOUD_DOMAINS_TO_SUPPRESS,
        }

    def _seed_local_auth(self) -> dict[str, str]:
        """Create the local system owner and refresh token inside `.storage/auth`."""
        user_id = uuid.uuid4().hex
        refresh_token_id = uuid.uuid4().hex
        jwt_key = secrets.token_hex(64)
        raw_token = secrets.token_urlsafe(48)
        access_token = hmac.new(
            jwt_key.encode("utf-8"),
            raw_token.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        auth_payload = {
            "version": 1,
            "minor_version": 1,
            "key": "auth",
            "data": {
                "users": [
                    {
                        "id": user_id,
                        "group_ids": ["system-admin"],
                        "is_owner": True,
                        "is_active": True,
                        "name": "Zenith Orchestra Daemon",
                        "system_generated": False,
                        "local_only": True,
                    }
                ],
                "groups": [
                    {"id": "system-admin", "name": "Administrators"},
                    {"id": "system-users", "name": "Users"},
                    {"id": "system-read-only", "name": "Read Only"},
                ],
                "credentials": [
                    {
                        "id": uuid.uuid4().hex,
                        "user_id": user_id,
                        "auth_provider_type": "homeassistant",
                        "auth_provider_id": None,
                        "data": {"username": "orchestra"},
                    }
                ],
                "refresh_tokens": [
                    {
                        "id": refresh_token_id,
                        "user_id": user_id,
                        "client_id": "http://zenith-node.local/",
                        "client_name": "Orchestra Local Hijack Daemon",
                        "client_icon": None,
                        "token_type": "long_lived_access_token",
                        "created_at": "2026-10-16T00:00:00+00:00",
                        "access_token_expiration": 315360000.0,
                        "token": raw_token,
                        "jwt_key": jwt_key,
                        "last_used_at": None,
                        "last_used_ip": "127.0.0.1",
                        "expire_at": None,
                        "credential_id": None,
                        "version": "2026.10.0",
                    }
                ],
            },
        }
        self._write_storage_file("auth", auth_payload)
        return {"user_id": user_id, "access_token": access_token}

    def _write_configuration_yaml(self) -> None:
        """Write a hardened `configuration.yaml` loading the in-process `orchestra` hook."""
        config_yaml = f"""# Auto-generated by Zenith Orchestra Process Hijacker
# Zero-Cloud Local Appliance Configuration (Z-Node)

homeassistant:
  name: "{self.config.location_name}"
  latitude: {self.config.latitude}
  longitude: {self.config.longitude}
  elevation: {self.config.elevation_m}
  unit_system: metric
  currency: {self.config.currency}
  country: {self.config.country}
  time_zone: "{self.config.time_zone}"

# Load the in-process Orchestra hijack custom component
orchestra:
  cloud_liberation_mode: strict
  ocpp_port: {self.config.ocpp_server_port}
  ngangk_api_url: "{self.config.ngangk_api_base_url}"
  hvac_min_dwell_seconds: {self.config.safety.hvac_min_dwell_seconds}
  hvac_max_setpoint_offset_c: {self.config.safety.hvac_max_setpoint_offset_c}

# Local SQLite recorder tuned for eMMC 5.1 flash longevity (F2FS)
recorder:
  commit_interval: 30
  purge_keep_days: 14
  exclude:
    domains:
      - updater
      - cloud

automation: !include automations.yaml
"""
        yaml_path = self.config.ha_config_dir / "configuration.yaml"
        yaml_path.write_text(config_yaml, encoding="utf-8")

        automations_path = self.config.ha_config_dir / "automations.yaml"
        if not automations_path.exists():
            automations_path.write_text("[]\n", encoding="utf-8")

    def _write_storage_file(self, key: str, payload: dict[str, Any]) -> Path:
        path = self.storage_dir / key
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
