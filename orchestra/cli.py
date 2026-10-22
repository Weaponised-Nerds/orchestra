"""
Command-Line Interface & Benchtop Commissioning Runner for Zenith Orchestra.

Usage:
  /opt/homebrew/bin/python3 -m orchestra.cli grandpa-setup --config-dir ./ha_config_sandbox
  /opt/homebrew/bin/python3 -m orchestra.cli probe-istore
  /opt/homebrew/bin/python3 -m orchestra.cli hijack --config-dir ./ha_config_sandbox
  /opt/homebrew/bin/python3 -m orchestra.cli commission
  /opt/homebrew/bin/python3 -m orchestra.cli matrix
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from orchestra.adapters.heat_pumps.istore import IStoreHeatPumpAdapter
from orchestra.agent.framework import GrandpaCommissioningAgent
from orchestra.config import OrchestraConfig
from orchestra.discovery.fingerprints import HARDWARE_FINGERPRINT_CATALOG
from orchestra.models import UserEnergyPreferences
from orchestra.orchestrator import OrchestraEngine


async def _run_hijack(config_dir: Path, as_json: bool = False) -> int:
    cfg = OrchestraConfig(ha_config_dir=config_dir)
    engine = OrchestraEngine(cfg)
    report = await engine.discover_and_hijack_all(simulate_all_first_pass_brands=True)

    if as_json:
        print(json.dumps(report, indent=2))
        return 0

    print("=" * 88)
    print(" ZENITH ORCHESTRA: ZERO-CLOUD HOME ASSISTANT HIJACK & ONBOARDING ENGINE")
    print("=" * 88)
    print(f"Target HA Config Dir   : {config_dir.resolve()}")
    print(f"HA Onboarding Bypassed : {report['bootstrap']['onboarding_bypassed']}")
    print(f"Suppressed Cloud Flows : {', '.join(report['bootstrap']['suppressed_cloud_domains'])}")
    print(f"DNS Intercept Rules    : {report['dns_intercept_rules_count']} active rules")
    print(f"Total Brands Onboarded : {report['total_devices_onboarded']} devices")
    print(f"Total Curated Entities : {report['total_entities_registered']} entities")
    print("-" * 88)
    print(f"{'CATEGORY':<20} | {'BRAND':<20} | {'LOCAL PROTOCOL':<22} | {'HA DOMAIN':<18} | {'LATENCY'}")
    print("-" * 88)
    for item in report["onboarded_devices"]:
        print(
            f"{item['category']:<20} | "
            f"{item['brand']:<20} | "
            f"{item['protocol']:<22} | "
            f"{item['ha_domain']:<18} | "
            f"{item['diagnostic_latency_ms']:>5.1f} ms"
        )
    print("=" * 88)
    return 0


async def _run_grandpa_setup(config_dir: Path) -> int:
    cfg = OrchestraConfig(ha_config_dir=config_dir)
    engine = OrchestraEngine(cfg)
    agent = GrandpaCommissioningAgent(engine)
    result = await agent.run_autonomous_grandpa_setup()

    print("=" * 92)
    print(" ZENITH AUTONOMOUS AGENT: 'SO EASY GRANDPA CAN SET IT UP' COMMISSIONING FLOW")
    print("=" * 92)
    for turn in result.agent_conversation_log:
        print(f"\n[{turn.stage.value}] Tool: {turn.tool_called}()")
        print(f"  Concierge -> Grandpa: \"{turn.grandpa_message}\"")

    print("\n" + "-" * 92)
    print(" ACTIVE LOAD-STEPPING CIRCUIT IDENTIFICATION RESULTS (Watching Live ΔW)")
    print("-" * 92)
    print(f"{'RAW SENSOR ID':<40} | {'Δ WATTS':<9} | {'ASSIGNED CIRCUIT NAME'}")
    print("-" * 92)
    for m in result.circuit_probe_report.identified_circuits:
        print(
            f"{m.sensor_entity_id:<40} | "
            f"+{m.delta_watts:<7.1f}W | "
            f"{m.assigned_friendly_name} ({m.assigned_area})"
        )

    print("\n" + "-" * 92)
    print(" CONDUCTOR DEMAND RESPONSE PLUGIN BINDINGS (Appliance <-> Verified Circuit Sensor)")
    print("-" * 92)
    for load in result.conductor_manifest.controllable_loads[:6]:
        circuit_str = load.verified_power_sensor_name or "Internal Meter"
        print(f"  • {load.sensible_name:<35} -> {load.control_entity_id:<42} [Circuit: {circuit_str}]")

    print("\n" + "-" * 92)
    print(" GENERATED DASHBOARDS & ENTITY HYGIENE")
    print("-" * 92)
    print(f"  • Visible Curated Entities : {result.total_visible_entities}")
    print(f"  • Hidden Diagnostic Clutter: {result.total_hidden_noise_entities} (RSSI, reboots, MACs hidden)")
    print(f"  • Lovelace Views Created   : {', '.join(result.dashboard_summary['views_created'])}")
    print(f"  • Everyday Rooms Organized : {', '.join(result.dashboard_summary['everyday_rooms_organized'])}")
    print(f"  • Lovelace Storage File    : {result.dashboard_summary['lovelace_path']}")
    print(f"  • HA Energy Storage File   : {result.dashboard_summary['energy_path']}")
    print("=" * 92)
    return 0


async def _run_probe_istore() -> int:
    result = await IStoreHeatPumpAdapter.live_probe_istore_on_lan()
    print("=" * 88)
    print(" iSTORE 180L / 270L HEAT PUMP LOCAL WI-FI & MODBUS PROBER (RASPBERRY PI TOOL)")
    print("=" * 88)
    print(json.dumps(result, indent=2))
    return 0


async def _run_commission(config_dir: Path) -> int:
    cfg = OrchestraConfig(ha_config_dir=config_dir)
    engine = OrchestraEngine(cfg)
    await engine.discover_and_hijack_all(simulate_all_first_pass_brands=True)

    tools = engine.agent_tools
    prefs = UserEnergyPreferences(
        departure_time="07:30",
        return_time="17:30",
        summer_ac_max_comfort_c=24.0,
        precool_target_c=20.5,
        ev_target_soc_pct=80.0,
        bess_evening_reserve_soc_pct=80.0,
    )
    contract = await tools.generate_household_load_contract(prefs)
    committed = await tools.commit_automations_to_node(contract)

    print(f"Generated LoadShedContract : {contract.contract_id}")
    print(f"Enrolled Local Appliances  : {len(contract.enrolled_device_ids)}")
    print(f"Sheddable Peak Capacity    : {contract.sheddable_capacity_kw} kW")
    print(f"Estimated Monthly Savings  : ${contract.estimated_monthly_savings_aud:.2f} AUD")
    print(f"Automations Committed      : {committed} ({config_dir / 'automations.yaml'})")
    return 0


def _print_matrix() -> int:
    print(f"{'ID':<34} | {'CATEGORY':<18} | {'BRAND':<20} | {'PORT':<5} | {'PROTOCOL'}")
    print("-" * 105)
    for fp in HARDWARE_FINGERPRINT_CATALOG:
        print(
            f"{fp.fingerprint_id:<34} | "
            f"{fp.category.value:<18} | "
            f"{fp.brand.value:<20} | "
            f"{fp.default_port:<5} | "
            f"{fp.protocol.value}"
        )
    return 0


def _run_agent_takeover(
    host: str,
    port: int,
    token: str | None,
    timeout: float,
    non_interactive: bool,
) -> int:
    from orchestra.agent.hybrid_runner import OrchestraHybridAgent, validate_target_safety

    validate_target_safety(host)

    print("=" * 88)
    print(" ZENITH ORCHESTRA: HYBRID AGENT TAKEOVER & INTERACTIVE COMMISSIONING")
    print("=" * 88)
    print(f"Target Home Assistant : http://{host}:{port}")

    agent = OrchestraHybridAgent(
        host=host,
        port=port,
        token=token,
        interactive=not non_interactive,
    )
    if not agent.token:
        print("ERROR: No Long-Lived Access Token found. Provide via --token or dist/orchestra_agent_token.txt")
        return 1

    print(f"Token Loaded          : {agent.token[:15]}... (Orchestra-Agent)")
    print(f"Waiting for HA Core   : timeout={timeout:.0f}s...")

    try:
        result = agent.run_full_takeover(timeout_seconds=timeout)
    except Exception as err:
        print(f"\nTakeover Error: {err}")
        return 1

    print("-" * 88)
    print(" TAKEOVER EXECUTION SUMMARY")
    print("-" * 88)
    print(f"Authenticated User    : {result.authenticated_user}")
    print(f"Discovered Flows Handled: {len(result.flows_resolved)}")
    for f in result.flows_resolved:
        print(f"  • [{f.domain}] {f.title}: {f.action_taken} ({f.status})")
    print(f"Entities Curated      : {result.entities_renamed}")
    print(f"Diagnostic Noise Hidden: {result.entities_hidden_noise}")
    print(f"Rooms Ensured         : {', '.join(result.areas_ensured[:5])}...")
    print(f"Overview Dashboard    : {'Curated (/lovelace)' if result.dashboard_curated else 'Existing'}")
    print("-" * 88)
    print(f"Agent Message         : \"{result.handover_message}\"")
    print("=" * 88)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="orchestra",
        description="Zenith Orchestra: Cloud-Free Home Assistant Process Hijacker & Grandpa-Simple Agent Suite",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    takeover_p = sub.add_parser(
        "agent-takeover",
        help="Run the interactive Orchestra Agent takeover (AirPlay PIN, Envoy serial, semantic naming, room assignment, dashboard)",
    )
    takeover_p.add_argument(
        "--host",
        type=str,
        default="homeassistant.local",
        help="Target Home Assistant host/IP (e.g. 192.168.0.44 or homeassistant.local; never 192.168.0.11)",
    )
    takeover_p.add_argument(
        "--port",
        type=int,
        default=8123,
        help="Target Home Assistant HTTP port (default 8123)",
    )
    takeover_p.add_argument(
        "--token",
        type=str,
        default=None,
        help="Orchestra-Agent Long-Lived Access Token (defaults to dist/orchestra_agent_token.txt)",
    )
    takeover_p.add_argument(
        "--timeout",
        type=float,
        default=120.0,
        help="Seconds to wait for Home Assistant to boot before timeout",
    )
    takeover_p.add_argument(
        "--non-interactive",
        action="store_true",
        help="Run without interactive prompts (auto-confirm or skip prompts)",
    )

    gp_p = sub.add_parser(
        "grandpa-setup",
        help="Run the full 6-stage Grandpa-Simple Autonomous Agent setup (naming, load-stepping circuits, DR binding, dashboards)",
    )
    gp_p.add_argument(
        "--config-dir",
        type=Path,
        default=Path("./ha_config_sandbox"),
        help="Path to Home Assistant configuration directory",
    )

    sub.add_parser(
        "probe-istore",
        help="Live LAN UDP 48899 / Modbus 8899 / Tuya 6668 discovery probe for an iStore Heat Pump",
    )

    hijack_p = sub.add_parser("hijack", help="Bypass HA onboarding and inject all discovered local hardware")
    hijack_p.add_argument(
        "--config-dir",
        type=Path,
        default=Path("./ha_config_sandbox"),
        help="Path to Home Assistant configuration directory",
    )
    hijack_p.add_argument("--json", action="store_true", help="Emit JSON output")

    comm_p = sub.add_parser("commission", help="Run AI Concierge load contract compilation & automation commit")
    comm_p.add_argument(
        "--config-dir",
        type=Path,
        default=Path("./ha_config_sandbox"),
        help="Path to Home Assistant configuration directory",
    )

    sub.add_parser("matrix", help="Print the supported first-pass zero-cloud hardware fingerprint matrix")

    app_p = sub.add_parser("app", help="Launch the Orchestra Mobile App & Embedded Concierge local web server")
    app_p.add_argument("--port", type=int, default=5000, help="Local HTTP port (default: 5000)")
    app_p.add_argument("--host", type=str, default="0.0.0.0", help="Binding host (default: 0.0.0.0)")
    app_p.add_argument("--target-ha", type=str, default=None, help="Home Assistant IP/hostname to connect to")

    args = parser.parse_args(argv)
    if args.command == "app":
        from orchestra.app.server import run_app_server
        run_app_server(host=args.host, port=args.port, target_host=args.target_ha)
        return 0
    if args.command == "agent-takeover":
        return _run_agent_takeover(
            host=args.host,
            port=args.port,
            token=args.token,
            timeout=args.timeout,
            non_interactive=args.non_interactive,
        )
    if args.command == "grandpa-setup":
        return asyncio.run(_run_grandpa_setup(args.config_dir))
    if args.command == "probe-istore":
        return asyncio.run(_run_probe_istore())
    if args.command == "hijack":
        return asyncio.run(_run_hijack(args.config_dir, as_json=args.json))
    if args.command == "commission":
        return asyncio.run(_run_commission(args.config_dir))
    if args.command == "matrix":
        return _print_matrix()
    return 1


if __name__ == "__main__":
    sys.exit(main())
