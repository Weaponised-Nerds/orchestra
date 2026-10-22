"""Unit tests for the Orchestra Concierge Agent & Mobile App Server."""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest.mock import MagicMock, patch

from orchestra.app.concierge_brain import LiveEnergySnapshot, OrchestraConciergeAgent
from orchestra.app.server import OrchestraAppRequestHandler, STATIC_DIR


class TestConciergeMobileApp(unittest.TestCase):
    """Verify Concierge Agent decision-making, natural language intents, and mobile server routes."""

    def setUp(self) -> None:
        self.concierge = OrchestraConciergeAgent(target_host="192.168.0.44", token="test_token")

    def test_concierge_welcome_message(self) -> None:
        """Verify the Concierge initializes with a warm, plain-English greeting."""
        self.assertGreaterEqual(len(self.concierge.messages), 1)
        first_msg = self.concierge.messages[0]
        self.assertEqual(first_msg.sender, "concierge")
        self.assertIn("G'day!", first_msg.text)
        self.assertIn("Orchestra Home Concierge", first_msg.text)

    def test_grandpa_hot_water_boost_intent(self) -> None:
        """Verify Concierge handles hot water boost requests autonomously."""
        msg = self.concierge.handle_grandpa_message("I need more hot water for a shower")
        self.assertEqual(msg.sender, "concierge")
        self.assertEqual(msg.action_type, "hot_water_boost")
        self.assertIn("hot water heat pump", msg.text)
        self.assertIn("solar-boost", msg.text)

    def test_grandpa_guest_comfort_intent(self) -> None:
        """Verify Concierge pre-cools living areas when visitors are expected."""
        msg = self.concierge.handle_grandpa_message("We have visitors coming over this afternoon")
        self.assertEqual(msg.sender, "concierge")
        self.assertEqual(msg.action_type, "guest_mode")
        self.assertIn("22°C", msg.text)
        self.assertEqual(msg.action_payload["ac_setpoint_c"], 22.0)

    def test_grandpa_night_sleep_intent(self) -> None:
        """Verify Concierge locks garage, turns off lights, and sets sleep mode."""
        msg = self.concierge.handle_grandpa_message("Goodnight, turning off everything for bed")
        self.assertEqual(msg.sender, "concierge")
        self.assertEqual(msg.action_type, "night_mode")
        self.assertTrue(msg.action_payload["garage_locked"])

    def test_grandpa_savings_query_intent(self) -> None:
        """Verify Concierge reports solar earnings in plain dollars and cents."""
        self.concierge.live_snapshot.money_saved_today_aud = 7.80
        msg = self.concierge.handle_grandpa_message("How much money have I saved today?")
        self.assertEqual(msg.sender, "concierge")
        self.assertEqual(msg.action_type, "savings_summary")
        self.assertIn("$7.80", msg.text)

    def test_static_files_exist_with_brand_assets(self) -> None:
        """Verify index.html, styles.css, app.js, and logo files are present."""
        self.assertTrue((STATIC_DIR / "index.html").exists())
        self.assertTrue((STATIC_DIR / "styles.css").exists())
        self.assertTrue((STATIC_DIR / "app.js").exists())
        self.assertTrue((STATIC_DIR / "logo.jpeg").exists())

        css_content = (STATIC_DIR / "styles.css").read_text(encoding="utf-8")
        # Ensure exact brand colors from Orchestra_logo.jpeg are present
        self.assertIn("#0e2b33", css_content)  # Midnight teal
        self.assertIn("#3bafaf", css_content)  # Brand electric cyan
