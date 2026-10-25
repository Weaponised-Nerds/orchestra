"""
Orchestra DataUpdateCoordinator & Conductor Dispatch Engine.

Fetches 30-minute SWIS trading interval forecasts from the Ngangk API
(https://ngangk.deeben.workers.dev/api/ngangk/forecast), tracks WEM operational demand,
wholesale price, renewable availability, and manages Conductor dispatch control
signals with granular asset-class responses (EV, Heat Pump, HVAC Pre-cool/Pre-heat,
Home Battery Storm Watch).
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Callable
import urllib.request

try:
    from .const import (
        ACTION_BATTERY_STORM_WATCH_HOLD,
        ACTION_EV_CHARGE_NOW,
        ACTION_EV_LOAD_SHED_PAUSED,
        ACTION_HPWH_RUN_NOW,
        ACTION_HVAC_PRE_COOL,
        ACTION_HVAC_PRE_HEAT,
        ASSET_EV,
        ASSET_HEAT_PUMP,
        ASSET_HOME_BATTERY,
        ASSET_HVAC,
        DEFAULT_HVAC_MAX_SETPOINT_OFFSET_C,
        DEFAULT_HVAC_MIN_DWELL_SECONDS,
        DEFAULT_NGANGK_API_URL,
        DEFAULT_POLL_INTERVAL_SECONDS,
        DOMAIN,
        EVENT_ORCHESTRA_ASSET_DIRECTIVE,
        EVENT_ORCHESTRA_CONDUCTOR_DISPATCH,
        FALLBACK_NGANGK_API_URL,
        SIGNAL_ENERGY_SOAK,
        SIGNAL_LOAD_SHED,
        SIGNAL_NORMAL_AUTO,
        SIGNAL_OPT_OUT,
        VALID_CONTROL_SIGNALS,
    )
    from .safety_guard import InProcessSafetyGuard
except ImportError:
    from custom_components.orchestra.const import (
        ACTION_BATTERY_STORM_WATCH_HOLD,
        ACTION_EV_CHARGE_NOW,
        ACTION_EV_LOAD_SHED_PAUSED,
        ACTION_HPWH_RUN_NOW,
        ACTION_HVAC_PRE_COOL,
        ACTION_HVAC_PRE_HEAT,
        ASSET_EV,
        ASSET_HEAT_PUMP,
        ASSET_HOME_BATTERY,
        ASSET_HVAC,
        DEFAULT_HVAC_MAX_SETPOINT_OFFSET_C,
        DEFAULT_HVAC_MIN_DWELL_SECONDS,
        DEFAULT_NGANGK_API_URL,
        DEFAULT_POLL_INTERVAL_SECONDS,
        DOMAIN,
        EVENT_ORCHESTRA_ASSET_DIRECTIVE,
        EVENT_ORCHESTRA_CONDUCTOR_DISPATCH,
        FALLBACK_NGANGK_API_URL,
        SIGNAL_ENERGY_SOAK,
        SIGNAL_LOAD_SHED,
        SIGNAL_NORMAL_AUTO,
        SIGNAL_OPT_OUT,
        VALID_CONTROL_SIGNALS,
    )
    from custom_components.orchestra.safety_guard import InProcessSafetyGuard

_LOGGER = logging.getLogger(__name__)


@dataclass
class OrchestraMarketData:
    """Current WEM operational demand and wholesale market metrics."""

    operational_demand_mw: float = 2000.0
    demand_p10_mw: float = 1850.0
    demand_p90_mw: float = 2150.0
    wholesale_price_aud_mwh: float = 75.0
    price_p10_aud_mwh: float = 50.0
    price_p90_aud_mwh: float = 110.0
    wholesale_price_cents_kwh: float = 7.5
    is_negative_price: bool = False
    renewable_availability_pct: float = 35.0
    total_vre_mw: float = 700.0
    estimated_dpv_mw: float = 450.0
    utility_solar_mw: float = 100.0
    utility_wind_mw: float = 150.0
    duck_curve_severity_pct: float = 25.0
    trading_interval_idx: int = 1
    interval_time_formatted: str = "00:00 AWST"
    forecast_timestamp: str = ""
    last_updated: str = ""
    source: str = "ngangk_api"


@dataclass
class OrchestraDispatchState:
    """Active Conductor dispatch directives and granular asset responses."""

    control_signal: str = SIGNAL_NORMAL_AUTO
    signal_source: str = "ngangk_autonomous"  # "conductor_push" | "ngangk_autonomous" | "manual"
    dispatch_id: str = "baseline"
    reason: str = "Autonomous Ngangk baseline"
    valid_until: str = ""
    opted_out: bool = False

    # Granular Asset Class 1: EV
    ev_charge_now: bool = False
    ev_target_amps: int = 16
    ev_action: str = ""
    ev_reason: str = ""

    # Granular Asset Class 2: Heat Pump Water Heater
    heat_pump_run_now: bool = False
    heat_pump_target_temp_c: float = 65.0
    heat_pump_action: str = ""
    heat_pump_reason: str = ""

    # Granular Asset Class 3: A/C Pre-Cool & Pre-Heat
    ac_precool: bool = False
    ac_preheat: bool = False
    ac_setpoint_offset_c: float = 0.0
    ac_dispatch_mode: str = "idle"  # "idle" | "precool" | "preheat" | "shed"
    ac_mode: str = "auto"  # "cool" | "heat" | "auto" | "off"
    ac_reason: str = ""

    # Granular Asset Class 4: Home Battery (Storm Watch / Soak)
    home_battery_storm_watch: bool = False
    battery_min_reserve_soc_pct: float = 100.0
    battery_charge_from_grid: bool = False
    battery_action: str = ""
    battery_reason: str = ""

    # Performance & Verified Impact
    counterfactual_shed_kw: float = 0.0
    accumulated_dividend_aud: float = 0.0

    # Tranche targeting: asset classes whose directive cohort range excluded this home
    excluded_assets: list[str] = field(default_factory=list)


@dataclass
class OrchestraCoordinatorData:
    """Combined payload held by the Orchestra coordinator."""

    market: OrchestraMarketData = field(default_factory=OrchestraMarketData)
    dispatch: OrchestraDispatchState = field(default_factory=OrchestraDispatchState)


class OrchestraDataUpdateCoordinator:
    """
    Coordinator responsible for polling the Ngangk WEM API and handling
    inbound Conductor push dispatches. Works in standard Home Assistant and
    in test environments.
    """

    def __init__(
        self,
        hass: Any,
        ngangk_api_url: str = DEFAULT_NGANGK_API_URL,
        poll_interval_seconds: int = DEFAULT_POLL_INTERVAL_SECONDS,
        safety_guard: InProcessSafetyGuard | None = None,
        node_id: str = "orchestra_node_wa_home",
    ) -> None:
        self.hass = hass
        self.ngangk_api_url = ngangk_api_url
        self.poll_interval_seconds = poll_interval_seconds
        self.node_id = node_id
        self.safety_guard = safety_guard or InProcessSafetyGuard(
            hvac_min_dwell_seconds=DEFAULT_HVAC_MIN_DWELL_SECONDS,
            hvac_max_setpoint_offset_c=DEFAULT_HVAC_MAX_SETPOINT_OFFSET_C,
        )

        self.data = OrchestraCoordinatorData()
        # Anonymous 0-99 cohort used to decide which tranche directives apply to this home
        self.cohort: int | None = None
        self.last_update_success: bool = True
        self.last_exception: Exception | None = None
        self.name: str = "orchestra"
        self.always_update: bool = True
        self._listeners: list[Callable[[], None]] = []
        self._poll_task: asyncio.Task | None = None
        self._is_stopped = False

    def async_add_listener(
        self,
        update_callback: Callable[..., None],
        context: Any = None,
        *args: Any,
        **kwargs: Any,
    ) -> Callable[[], None]:
        """Subscribe to updates."""
        self._listeners.append(update_callback)

        def _remove() -> None:
            if update_callback in self._listeners:
                self._listeners.remove(update_callback)

        return _remove

    def async_update_listeners(self) -> None:
        """Notify all registered entities of updated data."""
        for callback in list(self._listeners):
            try:
                callback()
            except Exception as err:
                _LOGGER.error("Error invoking Orchestra coordinator listener: %s", err)

    async def async_start(self) -> None:
        """Start the background Ngangk forecast polling loop."""
        self._is_stopped = False
        # Do initial fetch
        await self.async_refresh()
        if self.poll_interval_seconds > 0 and self._poll_task is None:
            self._poll_task = asyncio.create_task(self._background_poll_loop())

    async def async_stop(self) -> None:
        """Stop background polling."""
        self._is_stopped = True
        if self._poll_task and not self._poll_task.done():
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    async def _background_poll_loop(self) -> None:
        """Periodic background refresh loop."""
        while not self._is_stopped:
            try:
                await asyncio.sleep(self.poll_interval_seconds)
                if not self._is_stopped:
                    await self.async_refresh()
            except asyncio.CancelledError:
                break
            except Exception as err:
                _LOGGER.warning("Error in Orchestra polling loop: %s", err)
                await asyncio.sleep(30)

    async def async_refresh(self) -> None:
        """Fetch latest Ngangk forecast and evaluate autonomous state."""
        try:
            market_data = await self._fetch_ngangk_forecast()
            if market_data:
                self.data.market = market_data
            self.last_update_success = True
            self.last_exception = None
        except Exception as err:
            _LOGGER.warning("Ngangk forecast fetch failed: %s", err)
            self.last_exception = err
            # Keep last_update_success True to ensure gateway entities stay visible
            self.last_update_success = True

        # Update autonomous dispatch if not currently locked under active conductor push
        self._evaluate_autonomous_dispatch()
        self.async_update_listeners()

    async def _fetch_ngangk_forecast(self) -> OrchestraMarketData | None:
        """Fetch and parse live 30-min trading forecast from Ngangk API."""
        loop = asyncio.get_running_loop()

        def _sync_fetch() -> dict[str, Any]:
            query = "?horizon=24&start=now"
            urls = [f"{self.ngangk_api_url}{query}"]
            if FALLBACK_NGANGK_API_URL and self.ngangk_api_url != FALLBACK_NGANGK_API_URL:
                urls.append(f"{FALLBACK_NGANGK_API_URL}{query}")

            last_err = None
            for url in urls:
                try:
                    req = urllib.request.Request(
                        url,
                        headers={"User-Agent": "Orchestra-HA-Plugin/1.0", "Accept": "application/json"},
                    )
                    with urllib.request.urlopen(req, timeout=5.0) as resp:
                        return json.loads(resp.read().decode("utf-8"))
                except Exception as err:
                    last_err = err
                    continue
            raise last_err or RuntimeError("No forecast URL accessible")

        try:
            payload = await loop.run_in_executor(None, _sync_fetch)
        except Exception as exc:
            _LOGGER.debug("Network call to Ngangk failed: %s; keeping existing forecast", exc)
            return None

        forecast_list = payload.get("forecast", [])
        if not forecast_list:
            return None

        # Resolve current 30-min trading interval in Western Australia (AWST = UTC+8)
        # Each trading day in the SWIS WEM has 48 intervals (1: 00:00, 16: 07:30, etc.)
        awst_tz = timezone(timedelta(hours=8))
        now_awst = datetime.now(awst_tz)
        target_interval_idx = (now_awst.hour * 60 + now_awst.minute) // 30 + 1

        current = None

        # Priority 1: Match by timestamp window if ISO timestamp is present
        now_ts = now_awst.timestamp()
        for item in forecast_list:
            ts_str = item.get("timestamp")
            if ts_str:
                try:
                    item_dt = datetime.fromisoformat(ts_str)
                    item_ts = item_dt.timestamp()
                    if item_ts <= now_ts < item_ts + 1800:
                        current = item
                        break
                except Exception:
                    pass

        # Priority 2: Match by tradingIntervalIdx (AWST trading interval 1-48).
        # intervalIdx is a position within the returned horizon, not a trading
        # interval, so it is deliberately not used for matching.
        if current is None:
            for item in forecast_list:
                if item.get("tradingIntervalIdx") == target_interval_idx:
                    current = item
                    break

        # No interval covers "now": keep the existing market state rather than
        # silently presenting a stale interval (e.g. 00:00) as the live price.
        if current is None:
            _LOGGER.warning(
                "Conductor forecast has no interval covering %s (trading interval %s); "
                "keeping previous market data",
                now_awst.isoformat(),
                target_interval_idx,
            )
            return None

        demand_info = current.get("demandMW", {})
        price_info = current.get("wholesalePriceRTP", {})
        renewables_info = current.get("renewablesMW", {})

        # Note: Conductor already applies activeBiasCorrections to the p50 values it
        # returns; the field is informational only and must not be added again here.

        p50_demand = float(demand_info.get("p50", 2000.0))
        p10_demand = float(demand_info.get("p10", p50_demand * 0.9))
        p90_demand = float(demand_info.get("p90", p50_demand * 1.1))

        p50_price = float(price_info.get("p50", 75.0))
        p10_price = float(price_info.get("p10", p50_price * 0.8))
        p90_price = float(price_info.get("p90", p50_price * 1.3))
        is_neg = bool(price_info.get("isNegative", p50_price < 0))

        total_vre = float(renewables_info.get("totalVRE", 0.0))
        dpv = float(renewables_info.get("estimatedDPV", 0.0))
        sol = float(renewables_info.get("utilitySolar", 0.0))
        wnd = float(renewables_info.get("utilityWind", 0.0))
        duck_pct = float(renewables_info.get("duckCurveSeverityPct", 0.0))

        # Calculate renewable availability ratio
        if p50_demand > 0 and total_vre > 0:
            ren_avail = min(100.0, round((total_vre / p50_demand) * 100.0, 1))
        else:
            ren_avail = duck_pct

        now_iso = datetime.now(timezone.utc).isoformat()

        return OrchestraMarketData(
            operational_demand_mw=p50_demand,
            demand_p10_mw=p10_demand,
            demand_p90_mw=p90_demand,
            wholesale_price_aud_mwh=p50_price,
            price_p10_aud_mwh=p10_price,
            price_p90_aud_mwh=p90_price,
            wholesale_price_cents_kwh=round(p50_price / 10.0, 3),
            is_negative_price=is_neg,
            renewable_availability_pct=ren_avail,
            total_vre_mw=total_vre,
            estimated_dpv_mw=dpv,
            utility_solar_mw=sol,
            utility_wind_mw=wnd,
            duck_curve_severity_pct=duck_pct,
            trading_interval_idx=int(current.get("tradingIntervalIdx", 1)),
            interval_time_formatted=str(current.get("timeFormatted", "00:00 AWST")),
            forecast_timestamp=str(current.get("timestamp", now_iso)),
            last_updated=now_iso,
            source="ngangk_api",
        )

    def _evaluate_autonomous_dispatch(self) -> None:
        """
        Derives control signal and granular asset responses when Conductor
        push is not active, has expired, or is autonomous.
        """
        disp = self.data.dispatch
        if disp.opted_out:
            self._apply_opt_out_state()
            return

        # If locked to a manual or unexpired conductor push, don't overwrite
        if disp.signal_source == "conductor_push" and disp.valid_until:
            try:
                valid_dt = datetime.fromisoformat(disp.valid_until.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) < valid_dt:
                    return  # Still active Conductor push directive
            except Exception:
                pass

        # Evaluate Ngangk autonomous signals
        mkt = self.data.market
        disp.signal_source = "ngangk_autonomous"
        disp.dispatch_id = f"auto_{mkt.trading_interval_idx}"

        if mkt.is_negative_price or mkt.wholesale_price_aud_mwh < 0.0:
            disp.control_signal = SIGNAL_ENERGY_SOAK
            disp.reason = (
                f"Negative wholesale price (${mkt.wholesale_price_aud_mwh:.2f}/MWh). "
                f"Soaking excess solar with EV, heat pump, and A/C."
            )
            disp.ev_charge_now = True
            disp.ev_target_amps = 16
            disp.ev_action = ACTION_EV_CHARGE_NOW
            disp.ev_reason = "Negative price window; soaking grid solar"

            disp.heat_pump_run_now = True
            disp.heat_pump_target_temp_c = 65.0
            disp.heat_pump_action = ACTION_HPWH_RUN_NOW
            disp.heat_pump_reason = "Thermal boost to 65°C using low-cost energy"

            disp.ac_precool = True
            disp.ac_preheat = False
            disp.ac_setpoint_offset_c = -1.5
            disp.ac_dispatch_mode = "precool"
            disp.ac_reason = "Pre-cooling living space with zero-cost energy"

            disp.home_battery_storm_watch = False
            disp.battery_action = "SOLAR_SOAK"
            disp.battery_reason = "Normal solar soak"

        elif mkt.wholesale_price_aud_mwh > 120.0 or mkt.duck_curve_severity_pct > 80.0:
            disp.control_signal = SIGNAL_LOAD_SHED
            disp.reason = (
                f"High wholesale price (${mkt.wholesale_price_aud_mwh:.2f}/MWh). "
                f"Shedding non-critical load and pausing EV charging."
            )
            disp.ev_charge_now = False
            disp.ev_target_amps = 6
            disp.ev_action = "PAUSE_OR_MIN_6A"
            disp.ev_reason = "Pausing EV charging during peak price interval"

            disp.heat_pump_run_now = False
            disp.heat_pump_target_temp_c = 50.0
            disp.heat_pump_action = "THERMAL_COAST"
            disp.heat_pump_reason = "Coasting heat pump during peak price window"

            disp.ac_precool = False
            disp.ac_preheat = False
            disp.ac_setpoint_offset_c = 1.5
            disp.ac_dispatch_mode = "shed"
            disp.ac_reason = "Shedding HVAC compressor load (+1.5°C setpoint drift)"

            disp.home_battery_storm_watch = False
            disp.battery_action = "PEAK_DISCHARGE"
            disp.battery_reason = "Battery discharging during peak price window"
        else:
            disp.control_signal = SIGNAL_NORMAL_AUTO
            disp.reason = "Market prices nominal. Normal automated operation."
            disp.ev_charge_now = False
            disp.ev_target_amps = 16
            disp.ev_action = "NORMAL"
            disp.ev_reason = "Nominal market conditions"

            disp.heat_pump_run_now = False
            disp.heat_pump_target_temp_c = 60.0
            disp.heat_pump_action = "NORMAL"
            disp.heat_pump_reason = "Nominal schedule"

            disp.ac_precool = False
            disp.ac_preheat = False
            disp.ac_setpoint_offset_c = 0.0
            disp.ac_dispatch_mode = "idle"
            disp.ac_reason = "Nominal comfort schedule"

            disp.home_battery_storm_watch = False
            disp.battery_action = "SELF_CONSUME"
            disp.battery_reason = "Standard self-consumption"

    def apply_conductor_dispatch(self, payload: dict[str, Any]) -> dict[str, Any]:
        """
        Processes an incoming dispatch push frame from the Conductor platform
        (via WebSocket or Webhook). Enforces safety guardrails and applies
        granular asset directives.
        """
        disp = self.data.dispatch

        # If homeowner has opted out, reject automated load control overrides
        if disp.opted_out:
            _LOGGER.info("Conductor dispatch ignored because customer opt-out is active")
            return {
                "status": "rejected",
                "reason": "customer_opt_out_active",
                "dispatch_id": payload.get("dispatch_id", "unknown"),
            }

        dispatch_id = payload.get("dispatch_id", f"disp_{int(datetime.now(timezone.utc).timestamp())}")
        control_signal = payload.get("control_signal", SIGNAL_NORMAL_AUTO)
        reason = payload.get("reason", "Conductor central dispatch")
        valid_until = payload.get("valid_until", "")

        if control_signal not in VALID_CONTROL_SIGNALS:
            control_signal = SIGNAL_NORMAL_AUTO

        disp.dispatch_id = dispatch_id
        disp.control_signal = control_signal
        disp.signal_source = "conductor_push"
        disp.reason = reason
        disp.valid_until = valid_until

        # Update real-time market price or demand if broadcast in dispatch payload
        if "wholesale_price" in payload or "wholesalePriceRTP" in payload:
            raw_p = payload.get("wholesale_price") or payload.get("wholesalePriceRTP")
            if isinstance(raw_p, dict):
                p_val = float(raw_p.get("p50", self.data.market.wholesale_price_aud_mwh))
            else:
                try:
                    p_val = float(raw_p)
                except (ValueError, TypeError):
                    p_val = self.data.market.wholesale_price_aud_mwh
            self.data.market.wholesale_price_aud_mwh = p_val
            self.data.market.wholesale_price_cents_kwh = round(p_val / 10.0, 3)
            self.data.market.is_negative_price = p_val < 0

        if "operational_demand_mw" in payload or "demandMW" in payload:
            raw_d = payload.get("operational_demand_mw") or payload.get("demandMW")
            if isinstance(raw_d, dict):
                d_val = float(raw_d.get("p50", self.data.market.operational_demand_mw))
            else:
                try:
                    d_val = float(raw_d)
                except (ValueError, TypeError):
                    d_val = self.data.market.operational_demand_mw
            self.data.market.operational_demand_mw = d_val

        granular = payload.get("granular_assets", {})
        safety_adjustments: list[str] = []

        # Tranche targeting: a directive with `cohort: {from, to}` only applies to homes
        # whose anonymous cohort falls in [from, to); the rest run their normal schedule.
        excluded: list[str] = []
        for asset in (ASSET_EV, ASSET_HEAT_PUMP, ASSET_HVAC, ASSET_HOME_BATTERY):
            if granular.get(asset) and not self._cohort_targeted(granular[asset].get("cohort")):
                excluded.append(asset)
                self._apply_asset_normal(asset, "Not in this tranche; standard schedule")
        disp.excluded_assets = excluded
        granular = {k: v for k, v in granular.items() if k not in excluded}

        # 1. Granular EV directive
        ev_dir = granular.get(ASSET_EV, {})
        if ev_dir:
            ev_action = str(ev_dir.get("action", ""))
            target_amps = int(ev_dir.get("target_amps", 16))
            paused = target_amps == 0 or "PAUSE" in ev_action.upper() or "LOAD SHED" in ev_action.upper()
            # Without an action, `enabled` alone means charge now (pre-v1.2 frames)
            charge_now = not paused and bool(
                ev_dir.get("charge_now", False)
                or ev_action.upper() == ACTION_EV_CHARGE_NOW
                or (not ev_action and ev_dir.get("enabled", False))
            )
            if not ev_action and not ev_dir.get("enabled", True):
                paused, charge_now = True, False

            # Item 3.2: target_amps == 0 or load shed pause indicates pilot pause (IEC 61851 0A)
            if paused:
                target_amps = 0
                ev_action_resolved = (
                    ACTION_EV_LOAD_SHED_PAUSED if ("LOAD SHED" in ev_action.upper() or control_signal == SIGNAL_LOAD_SHED)
                    else "PAUSE"
                )
            else:
                if target_amps < 6:
                    target_amps = 6
                    safety_adjustments.append("ev_pilot_clamped_to_6a_minimum")
                elif target_amps > 32:
                    target_amps = 32
                    safety_adjustments.append("ev_pilot_clamped_to_32a_maximum")
                ev_action_resolved = ev_action or (ACTION_EV_CHARGE_NOW if charge_now else "NORMAL")

            disp.ev_charge_now = charge_now
            disp.ev_target_amps = target_amps
            disp.ev_action = ev_action_resolved
            disp.ev_reason = ev_dir.get("reason", reason)

        # 2. Granular Heat Pump Water Heater directive
        hp_dir = granular.get(ASSET_HEAT_PUMP, {})
        if hp_dir:
            hp_action = str(hp_dir.get("action", "")).upper()
            hp_enabled = bool(
                hp_dir.get("run_now", False)
                or hp_action in (ACTION_HPWH_RUN_NOW, "THERMAL_BOOST_65C")
                or (not hp_action and hp_dir.get("enabled", False))
            )
            requested_temp = float(hp_dir.get("target_temp_c", 65.0))
            # Enforce safety guardrail [50.0, 65.0]
            clamped_temp = max(
                self.safety_guard.hpwh_min_temp_c,
                min(self.safety_guard.hpwh_max_temp_c, requested_temp),
            )
            if clamped_temp != requested_temp:
                safety_adjustments.append(
                    f"hpwh_target_clamped_from_{requested_temp}C_to_{clamped_temp}C"
                )

            disp.heat_pump_run_now = hp_enabled
            disp.heat_pump_target_temp_c = clamped_temp
            disp.heat_pump_action = hp_dir.get("action", ACTION_HPWH_RUN_NOW if hp_enabled else "STANDBY")
            disp.heat_pump_reason = hp_dir.get("reason", reason)

        # 3. Granular A/C (HVAC) directive
        hvac_dir = granular.get(ASSET_HVAC, {})
        if hvac_dir:
            hvac_action = str(hvac_dir.get("action", "")).upper()
            mode = str(hvac_dir.get("mode", "")).lower()
            precool = bool(hvac_dir.get("precool", False) or "PRE_COOL" in hvac_action or mode == "cool")
            preheat = bool(hvac_dir.get("preheat", False) or "PRE_HEAT" in hvac_action or mode == "heat")
            is_shed = "SHED" in hvac_action or (not hvac_action and control_signal == SIGNAL_LOAD_SHED)

            offset = float(hvac_dir.get("setpoint_offset_c", -1.5 if precool else (1.5 if (preheat or is_shed) else 0.0)))

            # Clamp offset to safety limit
            max_offset = self.safety_guard.hvac_max_setpoint_offset_c
            if abs(offset) > max_offset:
                clamped_offset = max_offset if offset > 0 else -max_offset
                safety_adjustments.append(
                    f"hvac_offset_clamped_from_{offset}C_to_{clamped_offset}C"
                )
                offset = clamped_offset

            disp.ac_precool = precool
            disp.ac_preheat = preheat
            disp.ac_setpoint_offset_c = offset
            if precool:
                disp.ac_dispatch_mode = "precool"
            elif preheat:
                disp.ac_dispatch_mode = "preheat"
            elif is_shed:
                disp.ac_dispatch_mode = "shed"
            else:
                disp.ac_dispatch_mode = "idle"
            disp.ac_mode = mode if mode else ("cool" if precool else ("heat" if preheat else "auto"))
            disp.ac_reason = hvac_dir.get("reason", reason)

        # 4. Granular Home Battery directive (Storm Watch)
        batt_dir = granular.get(ASSET_HOME_BATTERY, {})
        if batt_dir:
            storm_watch = bool(
                batt_dir.get("storm_watch", False)
                or batt_dir.get("charge_now", False)
                or "STORM_WATCH" in str(batt_dir.get("action", "")).upper()
            )
            disp.home_battery_storm_watch = storm_watch
            disp.battery_min_reserve_soc_pct = float(batt_dir.get("min_soc_reserve_pct", 100.0 if storm_watch else 20.0))
            disp.battery_charge_from_grid = bool(batt_dir.get("charge_from_grid", storm_watch))
            disp.battery_action = batt_dir.get("action", ACTION_BATTERY_STORM_WATCH_HOLD if storm_watch else "SELF_CONSUME")
            disp.battery_reason = batt_dir.get("reason", reason)

        # Fire HA Bus Event if bus available
        if hasattr(self.hass, "bus") and hasattr(self.hass.bus, "async_fire"):
            event_payload = {
                "dispatch_id": dispatch_id,
                "control_signal": control_signal,
                "granular_assets": asdict(disp),
                "safety_adjustments": safety_adjustments,
            }
            if asyncio.iscoroutinefunction(self.hass.bus.async_fire):
                asyncio.create_task(
                    self.hass.bus.async_fire(EVENT_ORCHESTRA_CONDUCTOR_DISPATCH, event_payload)
                )
            else:
                self.hass.bus.async_fire(EVENT_ORCHESTRA_CONDUCTOR_DISPATCH, event_payload)

        self.async_update_listeners()

        return {
            "status": "applied",
            "dispatch_id": dispatch_id,
            "control_signal": control_signal,
            "safety_adjustments": safety_adjustments,
            "applied_at": datetime.now(timezone.utc).isoformat(),
        }

    def _cohort_targeted(self, cohort: Any) -> bool:
        """True when this home's cohort falls inside a directive's [from, to) range."""
        if not cohort:
            return True
        if self.cohort is None:
            # Without a cohort we cannot place ourselves in a tranche; stay out of it.
            return False
        try:
            lo, hi = float(cohort.get("from", 0)), float(cohort.get("to", 100))
        except (AttributeError, TypeError, ValueError):
            return True
        return lo <= self.cohort < hi

    def _apply_asset_normal(self, asset: str, reason: str) -> None:
        """Return one asset class to its standard local schedule."""
        disp = self.data.dispatch
        if asset == ASSET_EV:
            disp.ev_charge_now = False
            disp.ev_target_amps = 16
            disp.ev_action = "NORMAL"
            disp.ev_reason = reason
        elif asset == ASSET_HEAT_PUMP:
            disp.heat_pump_run_now = False
            disp.heat_pump_target_temp_c = 60.0
            disp.heat_pump_action = "NORMAL"
            disp.heat_pump_reason = reason
        elif asset == ASSET_HVAC:
            disp.ac_precool = False
            disp.ac_preheat = False
            disp.ac_setpoint_offset_c = 0.0
            disp.ac_dispatch_mode = "idle"
            disp.ac_mode = "auto"
            disp.ac_reason = reason
        elif asset == ASSET_HOME_BATTERY:
            disp.home_battery_storm_watch = False
            disp.battery_min_reserve_soc_pct = 20.0
            disp.battery_charge_from_grid = False
            disp.battery_action = "SELF_CONSUME"
            disp.battery_reason = reason

    def apply_wan_fallback_normal(self, reason: str = "WAN connection offline. Reverted to standard schedule.") -> None:
        """
        Item 3.4 of Conductor spec v1.2.0:
        During a WAN outage or WebSocket disconnect, edge node defaults to Normal/Auto
        and returns all controllable appliances to standard local user schedules.
        """
        disp = self.data.dispatch
        if disp.opted_out:
            return

        disp.signal_source = "wan_fallback"
        disp.control_signal = SIGNAL_NORMAL_AUTO
        disp.dispatch_id = "wan_offline_normal"
        disp.reason = reason
        disp.valid_until = ""

        # EV: return to normal schedule
        disp.ev_charge_now = False
        disp.ev_target_amps = 16
        disp.ev_action = "NORMAL"
        disp.ev_reason = "WAN offline; standard charging schedule"

        # HPWH: standard schedule
        disp.heat_pump_run_now = False
        disp.heat_pump_target_temp_c = 60.0
        disp.heat_pump_action = "NORMAL"
        disp.heat_pump_reason = "WAN offline; standard heating schedule"

        # HVAC: clear setpoint offsets (Δ 0.0°C) and idle mode
        disp.ac_precool = False
        disp.ac_preheat = False
        disp.ac_setpoint_offset_c = 0.0
        disp.ac_dispatch_mode = "idle"
        disp.ac_mode = "auto"
        disp.ac_reason = "WAN offline; standard comfort setpoint"

        # Battery: self-consume
        disp.home_battery_storm_watch = False
        disp.battery_min_reserve_soc_pct = 20.0
        disp.battery_charge_from_grid = False
        disp.battery_action = "SELF_CONSUME"
        disp.battery_reason = "WAN offline; standard self-consumption"
        disp.excluded_assets = []

        self.async_update_listeners()

    def set_opt_out(self, opt_out: bool) -> None:
        """1-tap customer opt-out toggle. Zeroes active DR actions immediately."""
        disp = self.data.dispatch
        disp.opted_out = opt_out
        if opt_out:
            self._apply_opt_out_state()
        else:
            disp.control_signal = SIGNAL_NORMAL_AUTO
            disp.reason = "Customer opt-out cleared. Resuming automated orchestration."
            self._evaluate_autonomous_dispatch()
        self.async_update_listeners()

    def _apply_opt_out_state(self) -> None:
        """Enforces safe default behavior when customer opts out."""
        disp = self.data.dispatch
        disp.control_signal = SIGNAL_OPT_OUT
        disp.signal_source = "opt_out"
        disp.reason = "Customer 1-tap opt-out active. All demand-response overrides disabled."
        # Every asset returns to its standard schedule, so asset automations restore baselines
        for asset in (ASSET_EV, ASSET_HEAT_PUMP, ASSET_HVAC, ASSET_HOME_BATTERY):
            self._apply_asset_normal(asset, "Customer opt-out active; standard schedule")
        disp.excluded_assets = []

    def set_manual_dispatch_mode(self, mode: str) -> None:
        """Manual override from UI select entity for field testing."""
        if mode == SIGNAL_OPT_OUT:
            self.set_opt_out(True)
            return

        self.set_opt_out(False)
        disp = self.data.dispatch
        disp.control_signal = mode
        disp.signal_source = "manual"
        disp.dispatch_id = f"manual_{int(datetime.now(timezone.utc).timestamp())}"

        if mode == SIGNAL_ENERGY_SOAK:
            disp.ev_charge_now = True
            disp.ev_target_amps = 16
            disp.heat_pump_run_now = True
            disp.ac_precool = True
            disp.ac_setpoint_offset_c = -1.5
            disp.ac_dispatch_mode = "precool"
            disp.home_battery_storm_watch = False
        elif mode == SIGNAL_LOAD_SHED:
            disp.ev_charge_now = False
            disp.ev_target_amps = 0
            disp.heat_pump_run_now = False
            disp.ac_precool = False
            disp.ac_setpoint_offset_c = 1.5
            disp.ac_dispatch_mode = "shed"
            disp.home_battery_storm_watch = False
        else:
            disp.ev_charge_now = False
            disp.ev_target_amps = 16
            disp.heat_pump_run_now = False
            disp.ac_precool = False
            disp.ac_setpoint_offset_c = 0.0
            disp.ac_dispatch_mode = "idle"

        self.async_update_listeners()
