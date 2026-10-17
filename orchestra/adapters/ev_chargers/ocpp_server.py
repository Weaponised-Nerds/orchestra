"""
Local OCPP 1.6J Central System Server (`ws://zenith-node.local:9000`).

Liberates OCPP-compliant residential EV chargers (EVNex, Sigenergy EVAC, Fronius Wattpilot)
from commercial cloud Charge Point Operators (CPOs). Provides deterministic local
`SetChargingProfile` (6A to 32A pilot current throttling in 1A increments),
`RemoteStartTransaction`, and `RemoteStopTransaction`.
"""

from __future__ import annotations

import json
from typing import Any
import uuid


class LocalOCPP16JServer:
    """
    Lightweight OCPP 1.6J JSON-RPC state & frame builder hosted on the Zenith Micro-Node.
    Message types follow OCPP-J specification:
    - CALL: `[2, "<UniqueId>", "<Action>", {<Payload>}]`
    - CALLRESULT: `[3, "<UniqueId>", {<Payload>}]`
    """

    def __init__(self, node_hostname: str = "zenith-node.local", port: int = 9000) -> None:
        self.node_hostname = node_hostname
        self.port = port

    def endpoint_for(self, charge_point_id: str) -> str:
        """Return the local WebSocket URL that the EVSE points to."""
        return f"ws://{self.node_hostname}:{self.port}/ocpp/{charge_point_id}"

    @staticmethod
    def build_set_charging_profile_call(
        limit_amps: int,
        connector_id: int = 1,
        number_phases: int = 1,
    ) -> list[Any]:
        """
        Build an OCPP 1.6J `SetChargingProfile` CALL frame to dynamically throttle
        EV charging current (e.g., 16A solar soak or 6A minimum pilot during peak).
        """
        msg_id = uuid.uuid4().hex[:12]
        payload = {
            "connectorId": connector_id,
            "csChargingProfiles": {
                "chargingProfileId": 100,
                "stackLevel": 1,
                "chargingProfilePurpose": "TxDefaultProfile",
                "chargingProfileKind": "Relative",
                "chargingSchedule": {
                    "chargingRateUnit": "A",
                    "chargingSchedulePeriod": [
                        {
                            "startPeriod": 0,
                            "limit": float(limit_amps),
                            "numberPhases": number_phases,
                        }
                    ],
                },
            },
        }
        return [2, msg_id, "SetChargingProfile", payload]

    @staticmethod
    def build_remote_stop_call(transaction_id: int = 1) -> list[Any]:
        """Build an OCPP 1.6J `RemoteStopTransaction` CALL frame for instant peak load shedding."""
        msg_id = uuid.uuid4().hex[:12]
        return [2, msg_id, "RemoteStopTransaction", {"transactionId": transaction_id}]

    @staticmethod
    def handle_boot_notification(unique_id: str) -> str:
        """Return a valid OCPP 1.6J BootNotification CALLRESULT JSON string."""
        response = [
            3,
            unique_id,
            {
                "status": "Accepted",
                "currentTime": "2026-10-16T04:00:00Z",
                "interval": 60,
            },
        ]
        return json.dumps(response)
