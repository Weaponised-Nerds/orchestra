"""Platform for Orchestra switch entities."""

from __future__ import annotations

from typing import Any

try:
    from .const import (
        DOMAIN,
        SWITCH_CONDUCTOR_OPT_OUT,
    )
    from .coordinator import OrchestraDataUpdateCoordinator
    from .entity import Entity, OrchestraBaseEntity
except ImportError:
    from custom_components.orchestra.const import (
        DOMAIN,
        SWITCH_CONDUCTOR_OPT_OUT,
    )
    from custom_components.orchestra.coordinator import OrchestraDataUpdateCoordinator
    from custom_components.orchestra.entity import Entity, OrchestraBaseEntity

try:
    from homeassistant.components.switch import SwitchEntity
except ImportError:
    class SwitchEntity(Entity):  # type: ignore
        """Switch entity fallback."""
        pass


async def async_setup_entry(
    hass: Any,
    entry: Any,
    async_add_entities: Any,
) -> None:
    """Set up Orchestra switches from a config entry."""
    coordinator: OrchestraDataUpdateCoordinator = hass.data[DOMAIN]["coordinator"]
    async_add_entities(
        [
            # 1. Canonical Switch
            OrchestraConductorOptOutSwitch(coordinator),
            # 2. Backward-Compatible Conductor Alias
            OrchestraConductorOptOutSwitch(
                coordinator,
                key="event_opt_out",
                prefix="conductor_dr",
                entity_id="switch.conductor_dr_event_opt_out",
            ),
        ]
    )


class OrchestraConductorOptOutSwitch(OrchestraBaseEntity, SwitchEntity):
    """1-Tap Homeowner Opt-Out switch. Disables all demand response overrides with 0 penalty."""

    def __init__(
        self,
        coordinator: OrchestraDataUpdateCoordinator,
        key: str = SWITCH_CONDUCTOR_OPT_OUT,
        prefix: str = DOMAIN,
        entity_id: str | None = None,
    ) -> None:
        super().__init__(coordinator, key, prefix, entity_id)
        self._attr_name = "Conductor Customer Opt-Out" if prefix == DOMAIN else "Conductor 1-Tap Peak Event Opt-Out"
        self._attr_icon = "mdi:account-check"

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.dispatch.opted_out)

    @property
    def icon(self) -> str:
        return "mdi:account-cancel" if self.is_on else "mdi:account-check"

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Opt out of Conductor demand response events."""
        self.coordinator.set_opt_out(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Opt back in to Conductor demand response events."""
        self.coordinator.set_opt_out(False)
