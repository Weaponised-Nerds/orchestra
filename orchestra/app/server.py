"""
Orchestra Mobile App Local Web & API Server.

Serves the zero-friction mobile web application for the homeowner
and hosts the embedded Orchestra Concierge Agent.
"""

from __future__ import annotations

from http.server import HTTPServer, SimpleHTTPRequestHandler
import json
import logging
from pathlib import Path
import sys
from typing import Any
from urllib.parse import urlparse

from orchestra.app.concierge_brain import OrchestraConciergeAgent

_LOGGER = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"


class OrchestraAppRequestHandler(SimpleHTTPRequestHandler):
    """Handles static web asset delivery and embedded Concierge API endpoints."""

    concierge: OrchestraConciergeAgent = OrchestraConciergeAgent()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            index_path = STATIC_DIR / "index.html"
            self.wfile.write(index_path.read_bytes())
            return

        if parsed.path.startswith("/static/"):
            rel_path = parsed.path.replace("/static/", "")
            target_file = STATIC_DIR / rel_path
            if target_file.exists() and target_file.is_file():
                content_type = self._guess_mime(target_file.suffix)
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.end_headers()
                self.wfile.write(target_file.read_bytes())
                return

        if parsed.path == "/api/status":
            prompt = self.concierge.check_for_interactive_prompts()
            snapshot = self.concierge.live_snapshot
            payload = {
                "connected": self.concierge.target_host is not None,
                "host": self.concierge.target_host or "homeassistant.local",
                "energy": {
                    "solar_kw": snapshot.solar_kw,
                    "battery_soc_pct": snapshot.battery_soc_pct,
                    "battery_kw": snapshot.battery_kw,
                    "home_load_kw": snapshot.home_load_kw,
                    "grid_kw": snapshot.grid_kw,
                    "money_saved_today_aud": snapshot.money_saved_today_aud,
                    "grid_tariff_mode": snapshot.grid_tariff_mode,
                },
                "pending_prompt": prompt,
            }
            self._send_json(payload)
            return

        if parsed.path == "/api/discover":
            res = self.concierge.discover_and_connect()
            self._send_json(res)
            return

        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)

        length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(length) if length > 0 else b"{}"
        try:
            body = json.loads(raw_body.decode("utf-8"))
        except Exception:
            body = {}

        if parsed.path == "/api/flows/submit":
            flow_id = body.get("flow_id", "")
            pin = body.get("pin", "")
            res = self.concierge.submit_pin(flow_id, pin)
            self._send_json(res)
            return

        if parsed.path == "/api/concierge/chat":
            user_text = body.get("text", "")
            msg = self.concierge.handle_grandpa_message(user_text)
            self._send_json({
                "sender": msg.sender,
                "reply": msg.text,
                "action_type": msg.action_type,
            })
            return

        self.send_error(404, "Endpoint not found")

    def _send_json(self, data: Any, status: int = 200) -> None:
        raw = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(raw)

    def _guess_mime(self, ext: str) -> str:
        ext_map = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".jpeg": "image/jpeg",
            ".jpg": "image/jpeg",
            ".png": "image/png",
            ".svg": "image/svg+xml",
        }
        return ext_map.get(ext.lower(), "application/octet-stream")


import threading

def run_app_server(host: str = "0.0.0.0", port: int = 5000, target_host: str | None = None) -> None:
    """Run the Orchestra mobile app HTTP server."""
    OrchestraAppRequestHandler.concierge = OrchestraConciergeAgent(target_host=target_host)
    threading.Thread(target=OrchestraAppRequestHandler.concierge.discover_and_connect, daemon=True).start()

    server = HTTPServer((host, port), OrchestraAppRequestHandler)
    print("=" * 80)
    print(f" ORCHESTRA MOBILE APP RUNNING AT http://localhost:{port}")
    print(f" Access on your phone/tablet at http://<this-machine-ip>:{port}")
    print(" Press Ctrl+C to stop.")
    print("=" * 80)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down Orchestra App server.")
        server.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    run_app_server(port=port)
