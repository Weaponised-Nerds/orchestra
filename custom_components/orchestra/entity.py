"""Base Entity definitions for the Orchestra Home Assistant component."""

from __future__ import annotations

from typing import Any

try:
    from .const import DOMAIN
    from .coordinator import OrchestraDataUpdateCoordinator
except ImportError:
    from custom_components.orchestra.const import DOMAIN
    from custom_components.orchestra.coordinator import OrchestraDataUpdateCoordinator

try:
    from homeassistant.helpers.entity import DeviceInfo, Entity
    from homeassistant.helpers.update_coordinator import CoordinatorEntity
except ImportError:
    # Lightweight fallback shim when running outside standard HA core environment
    class Entity:  # type: ignore
        """Mock HA Entity base class."""

        def __init__(self) -> None:
            self.hass: Any = None
            self._attr_extra_state_attributes: dict[str, Any] = {}
            self._attr_name: str | None = None
            self._attr_unique_id: str | None = None
            self._attr_native_value: Any = None
            self._attr_device_class: str | None = None
            self._attr_native_unit_of_measurement: str | None = None
            self._attr_icon: str | None = None
            self._attr_is_on: bool = False
            self.entity_id: str | None = None

        @property
        def name(self) -> str | None:
            return self._attr_name

        @property
        def unique_id(self) -> str | None:
            return self._attr_unique_id

        @property
        def extra_state_attributes(self) -> dict[str, Any]:
            return self._attr_extra_state_attributes

        @property
        def native_value(self) -> Any:
            return self._attr_native_value

        @property
        def native_unit_of_measurement(self) -> str | None:
            return self._attr_native_unit_of_measurement

        @property
        def device_class(self) -> str | None:
            return self._attr_device_class

        @property
        def icon(self) -> str | None:
            return self._attr_icon

        @property
        def is_on(self) -> bool:
            return self._attr_is_on

        async def async_added_to_hass(self) -> None:
            pass

        async def async_will_remove_from_hass(self) -> None:
            pass

        def async_write_ha_state(self) -> None:
            pass

    class CoordinatorEntity(Entity):  # type: ignore
        """Mock CoordinatorEntity."""

        def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
            super().__init__()
            self.coordinator = coordinator

        async def async_added_to_hass(self) -> None:
            self.coordinator.async_add_listener(self._handle_coordinator_update)

        def _handle_coordinator_update(self) -> None:
            self.async_write_ha_state()

        @property
        def available(self) -> bool:
            return True

    DeviceInfo = dict  # type: ignore


class OrchestraBaseEntity(CoordinatorEntity):
    """Common base for all Orchestra entities."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: OrchestraDataUpdateCoordinator,
        key: str,
        prefix: str = DOMAIN,
        entity_id: str | None = None,
    ) -> None:
        super().__init__(coordinator)
        self._key = key
        self._prefix = prefix
        self._attr_unique_id = f"{prefix}_{key}"
        if prefix == DOMAIN:
            self._attr_has_entity_name = True
            self._attr_translation_key = key
        else:
            self._attr_has_entity_name = False
        if entity_id:
            self.entity_id = entity_id

    @property
    def name(self) -> str | None:
        """Return the explicit name of the entity."""
        return getattr(self, "_attr_name", None)

    @property
    def available(self) -> bool:
        """Return True if entity is available."""
        return getattr(self.coordinator, "last_update_success", True)

    @property
    def device_info(self) -> Any:
        return {
            "identifiers": {(DOMAIN, "orchestra_gateway")},
            "name": "Orchestra Gateway",
            "manufacturer": "Weaponised Nerds",
            "model": "WEM Predictive Micro-Node",
            "sw_version": "0.3.1",
        }
