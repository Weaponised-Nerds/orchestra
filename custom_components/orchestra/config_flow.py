"""Zero-click and Form-based Config Flow for the Orchestra Custom Component."""

from __future__ import annotations

import logging
from typing import Any

try:
    from .const import (
        CONF_CONDUCTOR_WS_URL,
        CONF_NGANGK_API_URL,
        CONF_NODE_ID,
        DEFAULT_CONDUCTOR_WS_URL,
        DEFAULT_NGANGK_API_URL,
        DEFAULT_NODE_ID,
        DOMAIN,
    )
except ImportError:
    from custom_components.orchestra.const import (
        CONF_CONDUCTOR_WS_URL,
        CONF_NGANGK_API_URL,
        CONF_NODE_ID,
        DEFAULT_CONDUCTOR_WS_URL,
        DEFAULT_NGANGK_API_URL,
        DEFAULT_NODE_ID,
        DOMAIN,
    )

_LOGGER = logging.getLogger(__name__)

try:
    from homeassistant import config_entries
    from homeassistant.data_entry_flow import FlowResult
    from homeassistant.helpers import config_validation as cv
    from .schema import vol
    _string_validator = cv.string
except ImportError:
    _string_validator = str
    # Lightweight fallback shim when running unit tests outside Home Assistant core
    class _MockConfigFlow:
        """Fallback mock for ConfigFlow when running outside Home Assistant."""

        def __init_subclass__(cls, *, domain: str | None = None, **kwargs: Any) -> None:
            super().__init_subclass__(**kwargs)
            cls.domain = domain

        def __init__(self) -> None:
            self.hass: Any = None
            self._unique_id: str | None = None

        def _async_current_entries(self) -> list[Any]:
            return []

        async def async_set_unique_id(self, unique_id: str) -> None:
            self._unique_id = unique_id

        def _abort_if_unique_id_configured(self) -> None:
            pass

        def async_show_form(
            self,
            *,
            step_id: str,
            data_schema: Any = None,
            errors: dict[str, str] | None = None,
            description_placeholders: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
            }

        def async_create_entry(
            self,
            *,
            title: str,
            data: dict[str, Any],
            description: str | None = None,
            description_placeholders: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            return {
                "type": "create_entry",
                "title": title,
                "data": data,
            }

        def async_abort(
            self,
            *,
            reason: str,
            description_placeholders: dict[str, str] | None = None,
        ) -> dict[str, Any]:
            return {
                "type": "abort",
                "reason": reason,
            }

    class config_entries:  # type: ignore
        ConfigFlow = _MockConfigFlow

    FlowResult = dict[str, Any]  # type: ignore

    class _VolSchema:
        def __init__(self, schema: dict) -> None:
            self.schema = schema

    class vol:  # type: ignore
        Schema = _VolSchema

        @staticmethod
        def Optional(key: Any, default: Any = None) -> Any:
            return key

        @staticmethod
        def Required(key: Any, default: Any = None) -> Any:
            return key


class OrchestraConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handles automated and UI config flow creation for the Orchestra integration."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user step."""
        current_entries = []
        if hasattr(self, "_async_current_entries"):
            try:
                current_entries = self._async_current_entries()
            except Exception:
                current_entries = []
        elif hasattr(self, "_current_entries"):
            try:
                current_entries = self._current_entries()
            except Exception:
                current_entries = []

        if current_entries:
            return self.async_abort(reason="already_configured")

        if user_input is not None:
            return self.async_create_entry(
                title="Orchestra",
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Optional(CONF_NODE_ID, default=DEFAULT_NODE_ID): _string_validator,
                vol.Optional(CONF_NGANGK_API_URL, default=DEFAULT_NGANGK_API_URL): _string_validator,
                vol.Optional(CONF_CONDUCTOR_WS_URL, default=DEFAULT_CONDUCTOR_WS_URL): _string_validator,
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=schema,
        )

    async def async_step_import(
        self, import_data: dict[str, Any]
    ) -> FlowResult:
        """Handle import from configuration.yaml or automated onboarding."""
        current_entries = []
        if hasattr(self, "_async_current_entries"):
            try:
                current_entries = self._async_current_entries()
            except Exception:
                current_entries = []
        elif hasattr(self, "_current_entries"):
            try:
                current_entries = self._current_entries()
            except Exception:
                current_entries = []

        if current_entries:
            return self.async_abort(reason="already_configured")
        return self.async_create_entry(title="Orchestra", data=import_data or {})


# Direct reference for backward compatibility / HA reflection
ConfigFlow = OrchestraConfigFlow

if hasattr(config_entries, "HANDLERS"):
    try:
        config_entries.HANDLERS.register(DOMAIN)(OrchestraConfigFlow)
    except Exception:
        try:
            config_entries.HANDLERS[DOMAIN] = OrchestraConfigFlow
        except Exception:
            pass
