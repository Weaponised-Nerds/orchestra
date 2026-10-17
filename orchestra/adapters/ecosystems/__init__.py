"""System Ecosystem Bridges (Apple HomeKit, Google Home, Alexa / Ring, Samsung SmartThings)."""

from orchestra.adapters.ecosystems.alexa_ring import AlexaRingLocalBridgeAdapter
from orchestra.adapters.ecosystems.apple_homekit import AppleHomeKitBridgeAdapter
from orchestra.adapters.ecosystems.google_home import GoogleHomeLocalBridgeAdapter
from orchestra.adapters.ecosystems.smartthings import SamsungSmartThingsLocalBridgeAdapter

__all__ = [
    "AlexaRingLocalBridgeAdapter",
    "AppleHomeKitBridgeAdapter",
    "GoogleHomeLocalBridgeAdapter",
    "SamsungSmartThingsLocalBridgeAdapter",
]
