"""
Direct Home Assistant `.storage` Injector & Config Flow Hijacker.

Allows Orchestra to onboard discovered local hardware directly into Home Assistant's
`.storage/core.config_entries`, `.storage/core.device_registry`,
`.storage/core.area_registry`, and `.storage/core.entity_registry` without requiring
the user to click through any UI dialogs or cloud OAuth screens.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any
import uuid

from orchestra.config import OrchestraConfig
from orchestra.hijacker.bootstrap import CLOUD_DOMAINS_TO_SUPPRESS
from orchestra.models import DiscoveredHardware, HAConfigEntrySpec, HAEntitySpec


DEFAULT_HOME_AREAS: list[tuple[str, str]] = [
    ("garage_utility", "Garage / Utility"),
    ("roof_solar", "Roof / Solar Array"),
    ("living_bedrooms", "Living & Bedrooms"),
    ("driveway_ev", "Driveway / EV Bay"),
    ("weather_grid", "Grid & Weather Intelligence"),
    ("ecosystem_bridges", "System Ecosystem Bridges"),
]


class HAStorageInjector:
    """
    Injects pre-authenticated local config entries, devices, areas, entities,
    and Conductor demand response automations directly into Home Assistant.
    """

    def __init__(self, config: OrchestraConfig) -> None:
        self.config = config
        self.storage_dir: Path = self.config.ha_config_dir / ".storage"
        self.storage_dir.mkdir(parents=True, exist_ok=True)

    def initialize_registries(self) -> None:
        """Ensure core.area_registry, core.config_entries, core.device_registry, and core.entity_registry exist."""
        self._ensure_area_registry()
        self._ensure_config_entries_With_cloud_suppression()
        self._ensure_device_registry()
        self._ensure_entity_registry()

    def inject_config_entry(
        self,
        entry_spec: HAConfigEntrySpec,
        hardware: DiscoveredHardware,
    ) -> dict[str, Any]:
        """
        Atomically register a local hardware integration into:
        1. `.storage/core.config_entries`
        2. `.storage/core.device_registry`
        3. `.storage/core.entity_registry`
        """
        self.initialize_registries()

        # 1. Inject Config Entry (idempotent by unique_id + domain)
        entries_doc = self._read_storage("core.config_entries")
        entries_list: list[dict[str, Any]] = entries_doc["data"]["entries"]

        existing_idx = next(
            (
                i
                for i, item in enumerate(entries_list)
                if item.get("domain") == entry_spec.domain
                and item.get("unique_id") == (entry_spec.unique_id or entry_spec.entry_id)
            ),
            None,
        )

        serialized_entry = entry_spec.to_ha_storage_dict()
        if existing_idx is not None:
            serialized_entry["entry_id"] = entries_list[existing_idx]["entry_id"]
            entry_spec.entry_id = serialized_entry["entry_id"]
            entries_list[existing_idx] = serialized_entry
        else:
            entries_list.append(serialized_entry)

        # Also suppress any cloud domains specified by the adapter
        for cloud_domain in entry_spec.suppressed_cloud_domains:
            self._add_ignored_cloud_entry(entries_list, cloud_domain)

        self._write_storage("core.config_entries", entries_doc)

        # 2. Inject Device into `core.device_registry`
        area_id = self._resolve_area_id(entry_spec.suggested_area)
        device_reg_id = self._upsert_device(entry_spec, hardware, area_id)

        # 3. Inject Entities into `core.entity_registry`
        registered_entities = self._upsert_entities(
            entry_spec.entry_id,
            device_reg_id,
            area_id,
            entry_spec.entities,
        )

        return {
            "entry_id": entry_spec.entry_id,
            "domain": entry_spec.domain,
            "title": entry_spec.title,
            "device_registry_id": device_reg_id,
            "area_id": area_id,
            "entities_registered": len(registered_entities),
            "entity_ids": [e.entity_id for e in entry_spec.entities],
        }

    def write_automations(self, automations: list[dict[str, Any]]) -> Path:
        """Write compiled Conductor & Solar Soak automations to `automations.yaml`."""
        automations_path = self.config.ha_config_dir / "automations.yaml"
        # JSON is valid YAML 1.2, making it zero-dependency and deterministic
        automations_path.write_text(json.dumps(automations, indent=2), encoding="utf-8")
        return automations_path

    def get_injected_entries(self, include_ignored: bool = False) -> list[dict[str, Any]]:
        """Return all active (and optionally ignored) config entries."""
        if not (self.storage_dir / "core.config_entries").exists():
            return []
        doc = self._read_storage("core.config_entries")
        entries: list[dict[str, Any]] = doc["data"]["entries"]
        if include_ignored:
            return entries
        return [e for e in entries if e.get("source") != "ignore"]

    def get_registered_devices(self) -> list[dict[str, Any]]:
        """Return all registered devices from `core.device_registry`."""
        if not (self.storage_dir / "core.device_registry").exists():
            return []
        return self._read_storage("core.device_registry")["data"]["devices"]

    def get_registered_entities(self) -> list[dict[str, Any]]:
        """Return all registered entities from `core.entity_registry`."""
        if not (self.storage_dir / "core.entity_registry").exists():
            return []
        return self._read_storage("core.entity_registry")["data"]["entities"]

    def update_device_metadata(
        self,
        device_id: str,
        friendly_name: str,
        room_name: str,
    ) -> bool:
        """Used by the Zenith AI Commissioning Agent to rename a device and assign its room."""
        self.initialize_registries()
        area_id = self._resolve_area_id(room_name)
        dev_doc = self._read_storage("core.device_registry")
        updated = False
        for dev in dev_doc["data"]["devices"]:
            if dev.get("id") == device_id or any(
                ident[1] == device_id for ident in dev.get("identifiers", [])
            ):
                dev["name_by_user"] = friendly_name
                dev["area_id"] = area_id
                updated = True
                break
        if updated:
            self._write_storage("core.device_registry", dev_doc)
        return updated

    # -------------------------------------------------------------------------
    # Internal Registry Helpers
    # -------------------------------------------------------------------------

    def _ensure_area_registry(self) -> None:
        if (self.storage_dir / "core.area_registry").exists():
            return
        areas = [
            {
                "aliases": [],
                "floor_id": None,
                "icon": None,
                "id": area_id,
                "labels": [],
                "name": name,
                "picture": None,
            }
            for area_id, name in DEFAULT_HOME_AREAS
        ]
        self._write_storage(
            "core.area_registry",
            {
                "version": 1,
                "minor_version": 6,
                "key": "core.area_registry",
                "data": {"areas": areas},
            },
        )

    def _resolve_area_id(self, area_name: str) -> str:
        area_doc = self._read_storage("core.area_registry")
        areas: list[dict[str, Any]] = area_doc["data"]["areas"]
        slug = area_name.lower().replace(" / ", "_").replace(" & ", "_").replace(" ", "_")
        for area in areas:
            if area["id"] == slug or area["name"].lower() == area_name.lower():
                return str(area["id"])
        # Create new area on the fly if not present
        areas.append(
            {
                "aliases": [],
                "floor_id": None,
                "icon": None,
                "id": slug,
                "labels": [],
                "name": area_name,
                "picture": None,
            }
        )
        self._write_storage("core.area_registry", area_doc)
        return slug

    def _ensure_config_entries_With_cloud_suppression(self) -> None:
        if (self.storage_dir / "core.config_entries").exists():
            return
        entries: list[dict[str, Any]] = []
        for cloud_domain in CLOUD_DOMAINS_TO_SUPPRESS:
            self._add_ignored_cloud_entry(entries, cloud_domain)
        self._write_storage(
            "core.config_entries",
            {
                "version": 1,
                "minor_version": 4,
                "key": "core.config_entries",
                "data": {"entries": entries},
            },
        )

    def _add_ignored_cloud_entry(
        self,
        entries: list[dict[str, Any]],
        cloud_domain: str,
    ) -> None:
        if any(e.get("domain") == cloud_domain and e.get("source") == "ignore" for e in entries):
            return
        now_iso = datetime.now(timezone.utc).isoformat()
        entries.append(
            {
                "entry_id": uuid.uuid4().hex,
                "version": 1,
                "minor_version": 1,
                "domain": cloud_domain,
                "title": f"Suppressed Cloud Integration ({cloud_domain})",
                "data": {},
                "options": {},
                "pref_disable_new_entities": True,
                "pref_disable_polling": True,
                "source": "ignore",
                "unique_id": f"orchestra_suppressed_{cloud_domain}",
                "disabled_by": "user",
                "created_at": now_iso,
                "modified_at": now_iso,
                "discovery_keys": {},
            }
        )

    def _ensure_device_registry(self) -> None:
        if (self.storage_dir / "core.device_registry").exists():
            return
        self._write_storage(
            "core.device_registry",
            {
                "version": 1,
                "minor_version": 7,
                "key": "core.device_registry",
                "data": {"devices": [], "deleted_devices": []},
            },
        )

    def _ensure_entity_registry(self) -> None:
        if (self.storage_dir / "core.entity_registry").exists():
            return
        self._write_storage(
            "core.entity_registry",
            {
                "version": 1,
                "minor_version": 15,
                "key": "core.entity_registry",
                "data": {"entities": [], "deleted_entities": []},
            },
        )

    def _upsert_device(
        self,
        entry_spec: HAConfigEntrySpec,
        hardware: DiscoveredHardware,
        area_id: str,
    ) -> str:
        dev_doc = self._read_storage("core.device_registry")
        devices: list[dict[str, Any]] = dev_doc["data"]["devices"]

        identifier_pair = [entry_spec.domain, hardware.device_id]
        for dev in devices:
            if identifier_pair in dev.get("identifiers", []):
                if entry_spec.entry_id not in dev["config_entries"]:
                    dev["config_entries"].append(entry_spec.entry_id)
                dev["area_id"] = area_id
                self._write_storage("core.device_registry", dev_doc)
                return str(dev["id"])

        device_reg_id = uuid.uuid4().hex
        devices.append(
            {
                "area_id": area_id,
                "config_entries": [entry_spec.entry_id],
                "configuration_url": f"http://{hardware.ip_address}:{hardware.port}",
                "connections": [["mac", hardware.mac_address.lower()]],
                "disabled_by": None,
                "entry_type": None,
                "hw_version": hardware.protocol.value,
                "id": device_reg_id,
                "identifiers": [identifier_pair],
                "labels": ["cloud_free", hardware.category.value],
                "manufacturer": hardware.brand.value.replace("_", " ").title(),
                "model": hardware.model_name,
                "name": entry_spec.title,
                "name_by_user": None,
                "serial_number": hardware.serial_number,
                "sw_version": hardware.firmware_version,
                "via_device_id": None,
            }
        )
        self._write_storage("core.device_registry", dev_doc)
        return device_reg_id

    def _upsert_entities(
        self,
        config_entry_id: str,
        device_reg_id: str,
        area_id: str,
        entities: list[HAEntitySpec],
    ) -> list[dict[str, Any]]:
        ent_doc = self._read_storage("core.entity_registry")
        ent_list: list[dict[str, Any]] = ent_doc["data"]["entities"]
        registered: list[dict[str, Any]] = []

        for spec in entities:
            existing = next(
                (e for e in ent_list if e.get("entity_id") == spec.entity_id),
                None,
            )
            record = {
                "aliases": [],
                "area_id": area_id,
                "capabilities": spec.attributes,
                "config_entry_id": config_entry_id,
                "device_class": spec.device_class,
                "device_id": device_reg_id,
                "disabled_by": None,
                "entity_category": None,
                "entity_id": spec.entity_id,
                "hidden_by": None,
                "icon": None,
                "id": existing["id"] if existing else uuid.uuid4().hex,
                "has_entity_name": True,
                "labels": ["orchestra_managed"],
                "name": spec.friendly_name,
                "options": {},
                "original_device_class": spec.device_class,
                "original_icon": None,
                "original_name": spec.friendly_name,
                "platform": config_entry_id[:12],
                "supported_features": 0,
                "translation_key": None,
                "unique_id": f"{device_reg_id}_{spec.entity_id}",
                "previous_unique_id": None,
                "unit_of_measurement": spec.unit_of_measurement,
            }
            if existing is not None:
                idx = ent_list.index(existing)
                ent_list[idx] = record
            else:
                ent_list.append(record)
            registered.append(record)

        self._write_storage("core.entity_registry", ent_doc)
        return registered

    def _read_storage(self, key: str) -> dict[str, Any]:
        path = self.storage_dir / key
        return json.loads(path.read_text(encoding="utf-8"))

    def _write_storage(self, key: str, payload: dict[str, Any]) -> None:
        path = self.storage_dir / key
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
