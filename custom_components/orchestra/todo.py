"""The Orchestra setup punch list: things the setup agent could not do without a person."""

from __future__ import annotations

from typing import Any

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .setup_agent import OrchestraSetupAgent


async def async_setup_entry(hass: HomeAssistant, entry: Any, async_add_entities: Any) -> None:
    """Add the punch list on installs the Orchestra image set up."""
    agent: OrchestraSetupAgent | None = hass.data.get(DOMAIN, {}).get("setup_agent")
    if agent is not None:
        async_add_entities([OrchestraPunchList(agent)])


class OrchestraPunchList(TodoListEntity):
    """Each open item is one thing the homeowner needs to do, such as entering a pairing code."""

    _attr_has_entity_name = True
    _attr_unique_id = "orchestra_setup_punch_list"
    _attr_icon = "mdi:clipboard-check-outline"
    _attr_supported_features = (
        TodoListEntityFeature.UPDATE_TODO_ITEM | TodoListEntityFeature.DELETE_TODO_ITEM
    )

    def __init__(self, agent: OrchestraSetupAgent) -> None:
        self._agent = agent
        self._attr_name = "Orchestra setup"
        self.entity_id = "todo.orchestra_setup"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self._agent.async_add_listener(self.async_write_ha_state))

    @property
    def todo_items(self) -> list[TodoItem]:
        return [
            TodoItem(
                uid=item["key"],
                summary=item["summary"],
                description=item.get("description"),
                status=TodoItemStatus.COMPLETED if item.get("done") else TodoItemStatus.NEEDS_ACTION,
            )
            for item in self._agent.items
        ]

    async def async_update_todo_item(self, item: TodoItem) -> None:
        await self._agent.async_set_item_done(str(item.uid), item.status == TodoItemStatus.COMPLETED)

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        await self._agent.async_remove_items(uids)
