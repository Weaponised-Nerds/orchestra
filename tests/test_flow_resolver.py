"""Unit tests for AutonomousHardwareHijacker & Flow Resolver."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.orchestra.flow_resolver import (
    AutonomousHardwareHijacker,
    derive_envoy_installer_password,
)


def test_derive_envoy_installer_password() -> None:
    # Deterministic test of Enphase installer digest calculation
    pwd = derive_envoy_installer_password("122206103673")
    assert len(pwd) == 6
    assert pwd.isalnum()

    # Different serial should yield different password
    pwd2 = derive_envoy_installer_password("122206109999")
    assert pwd2 != pwd


def test_auto_resolve_enphase_flow() -> None:
    async def _run() -> None:
        mock_hass = MagicMock()
        mock_hass.config_entries.flow.async_progress.return_value = [
            {
                "flow_id": "test_enphase_flow_123",
                "handler": "enphase_envoy",
                "step_id": "user",
                "context": {"title_placeholders": {"serial": "122206103673"}},
            }
        ]
        mock_hass.config_entries.flow.async_configure = AsyncMock(return_value={"type": "create_entry"})

        hijacker = AutonomousHardwareHijacker(mock_hass)
        resolved = await hijacker.sweep_and_resolve_flows()

        assert resolved == 1
        mock_hass.config_entries.flow.async_configure.assert_awaited_once()
        call_args = mock_hass.config_entries.flow.async_configure.await_args
        assert call_args[0][0] == "test_enphase_flow_123"
        assert call_args[1]["user_input"]["username"] == "installer"
        assert len(call_args[1]["user_input"]["password"]) == 6

    asyncio.run(_run())


def test_auto_abort_cloud_flow() -> None:
    async def _run() -> None:
        mock_hass = MagicMock()
        mock_hass.config_entries.flow.async_progress.return_value = [
            {
                "flow_id": "test_tuya_flow_456",
                "handler": "tuya",
                "step_id": "user",
                "context": {},
            }
        ]
        mock_hass.config_entries.flow.async_abort = MagicMock(return_value={"type": "abort"})

        hijacker = AutonomousHardwareHijacker(mock_hass)
        resolved = await hijacker.sweep_and_resolve_flows()

        assert resolved == 1
        mock_hass.config_entries.flow.async_abort.assert_called_once_with("test_tuya_flow_456")

    asyncio.run(_run())


def test_auto_confirm_printer_flow() -> None:
    async def _run() -> None:
        mock_hass = MagicMock()
        mock_hass.config_entries.flow.async_progress.return_value = [
            {
                "flow_id": "test_printer_flow_789",
                "handler": "brother",
                "step_id": "zeroconf_confirm",
                "context": {},
            }
        ]
        mock_hass.config_entries.flow.async_configure = AsyncMock(return_value={"type": "create_entry"})

        hijacker = AutonomousHardwareHijacker(mock_hass)
        resolved = await hijacker.sweep_and_resolve_flows()

        assert resolved == 1
        mock_hass.config_entries.flow.async_configure.assert_awaited_once_with(
            "test_printer_flow_789", user_input={}
        )

    asyncio.run(_run())
