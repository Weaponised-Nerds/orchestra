"""Platform for Orchestra select entities."""

from __future__ import annotations

from typing import Any

try:
    from .const import (
        DOMAIN,
        SELECT_CONDUCTOR_DISPATCH_MODE,
        VALID_CONTROL_SIGNALS,
    )
    from .coordinator import OrchestraDataUpdateCoordinator
    from .entity import Entity, OrchestraBaseEntity
except ImportError:
    from custom_components.orchestra.const import (
        DOMAIN,
        SELECT_CONDUCTOR_DISPATCH_MODE,
        VALID_CONTROL_SIGNALS,
    )
    from custom_components.orchestra.coordinator import OrchestraDataUpdateCoordinator
    from custom_components.orchestra.entity import Entity, OrchestraBaseEntity

try:
    from homeassistant.components.select import SelectEntity
except ImportError:
    class SelectEntity(Entity):  # type: ignore
        """Select entity fallback."""
        pass


async def async_setup_entry(
    hass: Any,
    entry: Any,
    async_add_entities: Any,
) -> None:
    """Set up Orchestra select entities from a config entry."""
    coordinator: OrchestraDataUpdateCoordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities([OrchestraConductorDispatchModeSelect(coordinator)])


class OrchestraConductorDispatchModeSelect(OrchestraBaseEntity, SelectEntity):
    """Allows manual selection / simulation of Conductor dispatch modes."""

    def __init__(self, coordinator: OrchestraDataUpdateCoordinator) -> None:
        super().__init__(coordinator, SELECT_CONDUCTOR_DISPATCH_MODE)
        self._attr_name = "Conductor Dispatch Mode"
        self._attr_options = list(VALID_CONTROL_SIGNALS)
        self._attr_icon = "mdi:tune-vertical"

    @property
    def current_option(self) -> str:
        return self.coordinator.data.dispatch.control_signal

    async def async_select_option(self, option: str) -> None:
        """Change the selected option."""
        if option in self._attr_options:
            self.coordinator.set_manual_dispatch_mode(option)
