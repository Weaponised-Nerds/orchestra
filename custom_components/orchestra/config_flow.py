"""Zero-click Config Flow for the Orchestra Custom Component."""

from __future__ import annotations

from typing import Any

from custom_components.orchestra.const import DOMAIN


class OrchestraConfigFlow:
    """Handles automated config flow creation for the Orchestra integration."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> dict[str, Any]:
        """Immediately create the Orchestra entry without prompting for cloud credentials."""
        return {
            "type": "create_entry",
            "title": "Orchestra",
            "domain": DOMAIN,
            "data": user_input or {"cloud_liberation_mode": "strict"},
        }
