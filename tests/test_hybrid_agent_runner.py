"""Unit tests for Orchestra Hybrid Onboarding and Interactive Commissioning Agent Runner."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from orchestra.agent.hybrid_runner import (
    HAWebSocketClient,
    OrchestraHybridAgent,
    derive_envoy_installer_password,
    validate_target_safety,
)
from orchestra.config import OrchestraConfig
from orchestra.hijacker.bootstrap import HAProcessHijacker


class TestHybridAgentRunner(unittest.TestCase):
    """Verify safety guards, flow resolution, and entity organization."""

    def test_strict_safety_guard_blocks_user_production_ha(self) -> None:
        """Ensure Orchestra NEVER connects to 192.168.0.11 under any circumstances."""
        with self.assertRaises(ValueError) as ctx:
            validate_target_safety("192.168.0.11")
        self.assertIn("CRITICAL SAFETY VIOLATION", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            validate_target_safety("http://192.168.0.11:8123")
        self.assertIn("CRITICAL SAFETY VIOLATION", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            OrchestraHybridAgent(host="192.168.0.11", token="dummy")
        self.assertIn("CRITICAL SAFETY VIOLATION", str(ctx.exception))

    def test_envoy_installer_password_derivation(self) -> None:
        """Verify deterministic Envoy password generation from serial."""
        pwd1 = derive_envoy_installer_password("122206103673")
        pwd2 = derive_envoy_installer_password("122206103673")
        self.assertEqual(pwd1, pwd2)
        self.assertEqual(len(pwd1), 6)

    def test_bootstrap_generates_orchestra_agent_token_and_auth_provider(self) -> None:
        """Verify HAProcessHijacker generates Orchestra-Agent user and HS256 JWT token."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            ha_dir = Path(tmp_dir)
            cfg = OrchestraConfig(ha_config_dir=ha_dir)
            hijacker = HAProcessHijacker(cfg)
            res = hijacker.bypass_onboarding()

            self.assertEqual(res["admin_user_name"], "Orchestra-Agent")
            self.assertEqual(res["admin_username"], "orchestra-agent")
            self.assertTrue(res["daemon_access_token"].startswith("ey"))

            # Verify .storage/auth
            auth_file = ha_dir / ".storage" / "auth"
            self.assertTrue(auth_file.exists())
            auth_data = json.loads(auth_file.read_text())
            user = auth_data["data"]["users"][0]
            self.assertEqual(user["name"], "Orchestra-Agent")
            self.assertFalse(user["local_only"])

            # Verify .storage/auth_provider.homeassistant
            auth_provider_file = ha_dir / ".storage" / "auth_provider.homeassistant"
            self.assertTrue(auth_provider_file.exists())
            provider_data = json.loads(auth_provider_file.read_text())
            self.assertEqual(provider_data["data"]["users"][0]["username"], "orchestra-agent")

            # Verify token file
            token_file = ha_dir / "orchestra_agent_token.txt"
            self.assertTrue(token_file.exists())
            self.assertEqual(token_file.read_text().strip(), res["daemon_access_token"])

    def test_flow_resolution_apple_tv_pin_prompt(self) -> None:
        """Verify the Agent prompts for Apple TV AirPlay 4-digit PIN."""
        mock_session = MagicMock()
        mock_get_flows = MagicMock()
        mock_get_flows.json.return_value = [
            {
                "flow_id": "appletv_flow_123",
                "handler": "apple_tv",
                "step_id": "pair",
                "context": {"title_placeholders": {"name": "Lounge Room Apple TV 4K"}},
                "data_schema": [{"name": "pin", "type": "string"}],
            }
        ]
        mock_post_step = MagicMock()
        mock_post_step.json.return_value = {"type": "create_entry", "version": 1}

        mock_session.get.return_value = mock_get_flows
        mock_session.post.return_value = mock_post_step

        prompt_answers = {"airplay": "9676"}

        def mock_prompt(msg: str, default: str | None = None) -> str:
            return "9676"

        agent = OrchestraHybridAgent(
            host="192.168.0.44",
            token="dummy_token",
            prompt_callback=mock_prompt,
            session=mock_session,
        )

        records = agent.resolve_pending_flows()
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].domain, "apple_tv")
        self.assertEqual(records[0].status, "create_entry")

        # Verify POST payload contained the entered PIN
        mock_session.post.assert_called_once()
        call_args = mock_session.post.call_args
        self.assertEqual(call_args[1]["json"], {"pin": "9676"})

    def test_flow_resolution_enphase_envoy_and_printers(self) -> None:
        """Verify Enphase Envoy installer password submission and Brother printer confirmation."""
        mock_session = MagicMock()
        mock_get_flows = MagicMock()
        mock_get_flows.json.return_value = [
            {
                "flow_id": "envoy_flow_456",
                "handler": "enphase_envoy",
                "step_id": "user",
                "context": {"title_placeholders": {"serial": "122206103673"}},
            },
            {
                "flow_id": "brother_flow_789",
                "handler": "brother",
                "step_id": "zeroconf_confirm",
                "context": {"title_placeholders": {"name": "Brother HL-L2350DW"}},
                "data_schema": [],
            },
        ]
        mock_session.get.return_value = mock_get_flows
        mock_session.post.return_value.json.return_value = {"type": "create_entry"}

        agent = OrchestraHybridAgent(
            host="192.168.0.44",
            token="dummy_token",
            session=mock_session,
        )

        records = agent.resolve_pending_flows()
        self.assertEqual(len(records), 2)
        self.assertEqual(records[0].domain, "enphase_envoy")
        self.assertEqual(records[1].domain, "brother")

        # Verify 2 POST calls
        self.assertEqual(mock_session.post.call_count, 2)
        envoy_call = mock_session.post.call_args_list[0]
        self.assertEqual(envoy_call[1]["json"]["username"], "installer")
        self.assertTrue(len(envoy_call[1]["json"]["password"]) == 6)

        brother_call = mock_session.post.call_args_list[1]
        self.assertEqual(brother_call[1]["json"], {})

    def test_organize_entities_and_rooms_via_mock_ws(self) -> None:
        """Verify entities are semantically renamed, assigned to rooms, and diagnostic noise is hidden."""
        mock_ws = MagicMock()

        areas = [{"area_id": "living_bedrooms", "name": "Living & Bedrooms"}]
        entities = [
            {
                "entity_id": "media_player.lg_webos_tv_oled65_992a",
                "original_name": "LG webOS TV OLED65C3PSA (992A)",
                "device_id": "dev_tv_1",
            },
            {
                "entity_id": "sensor.shelly_pro_3em_channel_a_power",
                "original_name": "shelly_pro_3em_08f9e0_channel_a_power",
                "device_id": "dev_shelly_1",
            },
            {
                "entity_id": "sensor.shelly_pro_3em_wifi_rssi",
                "original_name": "shelly_pro_3em_08f9e0_wifi_rssi",
                "device_id": "dev_shelly_1",
            },
        ]
        devices = [
            {"id": "dev_tv_1", "area_id": None},
            {"id": "dev_shelly_1", "area_id": None},
        ]

        def mock_call(msg_type: str, **kwargs: str) -> list | dict:
            if msg_type == "config/area_registry/list":
                return areas
            if msg_type == "config/area_registry/create":
                new_id = kwargs["name"].lower().replace(" ", "_").replace("&", "").replace("/", "")
                created = {"area_id": new_id, "name": kwargs["name"]}
                areas.append(created)
                return created
            if msg_type == "config/entity_registry/list":
                return entities
            if msg_type == "config/device_registry/list":
                return devices
            if msg_type == "config/entity_registry/update":
                return {"entity_id": kwargs["entity_id"]}
            if msg_type == "config/device_registry/update":
                return {"device_id": kwargs["device_id"]}
            return {}

        mock_ws.call.side_effect = mock_call

        agent = OrchestraHybridAgent(
            host="192.168.0.44",
            token="dummy_token",
            ws_client=mock_ws,
        )

        stats = agent.organize_entities_and_rooms()
        self.assertEqual(stats["renamed_entities"], 3)
        self.assertEqual(stats["hidden_noise_entities"], 1)  # wifi_rssi hidden

    def test_auto_discover_home_assistant_never_probes_forbidden_ip(self) -> None:
        """Ensure auto_discover_home_assistant filters out 192.168.0.11 from candidate IPs."""
        from orchestra.agent.hybrid_runner import FORBIDDEN_IPS, auto_discover_home_assistant

        self.assertIn("192.168.0.11", FORBIDDEN_IPS)

        with patch("socket.gethostbyname", return_value="192.168.0.11"):
            # When homeassistant.local resolves to forbidden IP, it must be ignored
            with patch("socket.socket") as mock_sock_cls:
                mock_sock = MagicMock()
                mock_sock.getsockname.return_value = ["192.168.0.91"]
                mock_sock_cls.return_value = mock_sock

                probed_ips: list[str] = []

                def mock_get(url: str, **kwargs) -> MagicMock:
                    ip = url.split("://")[1].split(":")[0]
                    probed_ips.append(ip)
                    res = MagicMock()
                    if ip == "192.168.0.44":
                        res.status_code = 200
                        res.text = '{"message": "API running."}'
                    else:
                        res.status_code = 500
                        res.text = "offline"
                    return res

                with patch("requests.get", side_effect=mock_get):
                    found = auto_discover_home_assistant(timeout_seconds=2.0)
                    self.assertNotIn("192.168.0.11", probed_ips)
                    self.assertEqual(found, "192.168.0.44")
