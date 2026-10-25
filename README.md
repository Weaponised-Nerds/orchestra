# Orchestra (`Z-Node` Cloud-Liberation & HA Process Hijacker)
### Developed by Weaponised Nerds

**Orchestra** is developed by **Weaponised Nerds** as the software core for the **Micro-Node (`Z-Node`)** hardware appliance. It hijacks the standard Home Assistant initialization, discovery, and configuration lifecycle to onboard residential energy, HVAC, heat pump, EV charging, and smart home hardware **100% locally without vendor cloud accounts**.

---

## 1. How Orchestra Hijacks the Home Assistant Process

Standard Home Assistant installations suffer from high user & installer friction: interactive onboarding wizards, cloud OAuth redirects, developer portal registrations (e.g., Tuya IoT Platform), and raw dashboards cluttered with 100+ uncurated entities.

Orchestra eliminates this via two coordinated layers:

1. **Out-of-Process Bootstrap & `.storage` Hijacker ([`orchestra/hijacker/`](orchestra/hijacker/))**:
   - **Onboarding Bypass ([`bootstrap.py`](orchestra/hijacker/bootstrap.py))**: Pre-completes all 4 Home Assistant onboarding steps (`user`, `core_config`, `analytics`, `integration`) in `.storage/onboarding`, seeds Western Australian (`Australia/Perth`, `AUD`, metric) locale settings in `.storage/core.config`, disables cloud telemetry, and provisions a deterministic local system owner and long-lived access token in `.storage/auth`.
   - **Direct Registry Injection ([`storage_injector.py`](orchestra/hijacker/storage_injector.py))**: Writes pre-authenticated local `ConfigEntry` records directly into `.storage/core.config_entries`, registers hardware with MAC/serial identifiers in `.storage/core.device_registry`, assigns logical rooms in `.storage/core.area_registry`, and registers only curated, human-named entities in `.storage/core.entity_registry`.
   - **DNS Cloud Sinkhole & Activation Redirector ([`cloud_blocker.py`](orchestra/hijacker/cloud_blocker.py))**: Generates local `dnsmasq` rules redirecting Tuya activation endpoints (`a.tuya.com`, `m2.tuya.com`) to the local Z-Node IP while sinkholing hostile vendor OTA firmware domains (`ota.tuya.com`, `firmware.sensibo.com`, `ota.myair.com.au`, `rheem.clearblade.com`, `ota.rinnai.com.au`) that could disable local ports.
2. **In-Process Home Assistant Custom Component ([`custom_components/orchestra/`](custom_components/orchestra/))**:
   - Hooks `config_entry_flow_initiated` on the Home Assistant event bus to automatically abort noisy cloud discovery flows (`tuya`, `sensibo`, `tesla_fleet`, `smartthings`, `rheem_econet`, `rinnai`, `stiebel_eltron_cloud`) so Orchestra's local adapters take precedence.
   - Enforces **hard-coded hardware warranty guardrails** ([`safety_guard.py`](custom_components/orchestra/safety_guard.py)):
     - **10-Minute Minimum HVAC Compressor Dwell Timer (`600s`)**: Prevents short-cycling compressor damage during rapid grid price fluctuations (per Conductor v1.2.0 spec).
     - **Setpoint-Offset-Only HVAC Control (`<= 2.0°C`)**: Never cuts raw mains power to inverter-driven air conditioners.
     - **Heat Pump Thermal Storage Boundaries (`50°C–65°C`)**: Uses water thermal mass (`65°C` solar soak boost, `50°C` peak coast) with weekly `>= 60°C` Legionella sterilization protection and zero dry-element cycling.

---

## 2. First-Pass Zero-Cloud Hardware Matrix (22 Targets Across 6 Categories)

| Category | Brand / System | Adapter Module | Zero-Cloud Local Onboarding & Control Mechanism |
| :--- | :--- | :--- | :--- |
| **Solar / Batteries** | **Tesla** (Powerwall 2/3 & Gateway) | [`solar_battery/tesla.py`](orchestra/adapters/solar_battery/tesla.py) | Local Gateway HTTPS (`/api/meters/aggregates`, `/api/system_status/soe`, `/api/operation`) using derived 5-char serial password + Powerwall 3 local TEDAPI (`192.168.91.1/tedapi/v1`). |
| **Solar / Batteries** | **Sigenergy** (SigenStor 5-in-1) | [`solar_battery/sigenergy.py`](orchestra/adapters/solar_battery/sigenergy.py) | Local Modbus TCP (`port 502`, Plant ID `247`, Inverter ID `1`). Controls EMS Work Mode (`40029`) & Charge/Discharge limits (`40031`/`40033`). |
| **Solar / Batteries** | **Fronius** (GEN24 + Smart Meter) | [`solar_battery/fronius.py`](orchestra/adapters/solar_battery/fronius.py) | Unauthenticated Fronius Solar API v1 (`/solar_api/v1/GetPowerFlowRealtimeData.fcgi`) + SunSpec Modbus TCP (`port 502`, Model 124 `StorCtl_Mod`). |
| **Solar / Batteries** | **Enphase** (IQ Gateway / Envoy + IQ Battery) | [`solar_battery/enphase.py`](orchestra/adapters/solar_battery/enphase.py) | mDNS `_enphase-envoy._tcp` discovery + deterministic `installer` MD5 serial digest derivation & cached local JWT for `/ivp/meters/readings` and `/ivp/ensemble/inventory`. |
| **Airconditioning** | **Polyaire AirTouch 5** | [`hvac/airtouch5.py`](orchestra/adapters/hvac/airtouch5.py) | UDP `49005` broadcast discovery + direct binary TCP socket on `port 9005` (`0x555555AA` header, `0xC0` AC/Zone control frames, Modbus CRC-16). |
| **Airconditioning** | **Sensibo** (Air / Sky / PRO) | [`hvac/sensibo.py`](orchestra/adapters/hvac/sensibo.py) | Hijacks onboarding via local Apple HomeKit Accessory Protocol (`_hap._tcp.local.`) into HA's local `homekit_controller` using local Ed25519 LTPK/LTSK pairing keys—zero Sensibo Cloud API key. |
| **Airconditioning** | **Advantage Air MyAir** (MyAir 5 / MyPlace) | [`hvac/advantage_air.py`](orchestra/adapters/hvac/advantage_air.py) | Direct local HTTP JSON API on `port 2025` (`/getSystemData` and `/setAircon?json=...`), bypassing MyAir's paid remote cloud subscription. |
| **Heat Pumps** | **STIEBEL ELTRON** (WWK / WPM + ISG) | [`heat_pumps/stiebel_eltron.py`](orchestra/adapters/heat_pumps/stiebel_eltron.py) | Local ISG Servicewelt Modbus TCP (`port 502`, Slave `1`) driving SG-Ready register `4001` (State 3 Solar Thermal Boost `65°C` / State 1 Peak Coast) and DHW register `522`. |
| **Heat Pumps** | **Rinnai** (Enviroflo GR / AR Series) | [`heat_pumps/rinnai.py`](orchestra/adapters/heat_pumps/rinnai.py) | Local Modbus TCP (`port 502`) & OEM Wi-Fi module local key extraction for tank temperature and `65°C` solar soak boost. |
| **Heat Pumps** | **Rheem** (AmbiPower 280e / EcoNet) | [`heat_pumps/rheem.py`](orchestra/adapters/heat_pumps/rheem.py) | Local Modbus TCP (`port 502`), AS/NZS 4755 DRM1–3 demand response, and local EcoNet MQTT intercept (blocking `rheem.clearblade.com`). |
| **Heat Pumps** | **Tuya (White-Label OEM)** (iStore / Emerald / Midea) | [`heat_pumps/tuya_whitelabel.py`](orchestra/adapters/heat_pumps/tuya_whitelabel.py) | Paired directly via Orchestra's Fake-Cloud AP Activator (`local_key` issued without Tuya account); controls DP `1` (Power), DP `2` (Target `65°C`), DP `3` (Tank Temp), DP `4` (Mode) over TCP `6668`. |
| **EV Chargers** | **Tesla** (Wall Connector Gen 3) | [`ev_chargers/tesla_evse.py`](orchestra/adapters/ev_chargers/tesla_evse.py) | Local HTTP REST (`/api/1/vitals`, `/api/1/lifetime`) + local TWC follower / onboard BLE GATT current modulation (`6A–32A`). |
| **EV Chargers** | **Sigenergy** (Sigen EVAC / DC Charger) | [`ev_chargers/sigenergy_evse.py`](orchestra/adapters/ev_chargers/sigenergy_evse.py) | Local OCPP 1.6J server (`ws://zenith-node.local:9000/ocpp/...`) + SigenStor Modbus TCP register `42003`. |
| **EV Chargers** | **Fronius** (Wattpilot Home / Go) | [`ev_chargers/fronius_evse.py`](orchestra/adapters/ev_chargers/fronius_evse.py) | Local WebSocket (`ws://<ip>/ws`) with PBKDF2/SHA256 challenge-response auth, dynamic `amp` (`6A–32A`), and `psm` (1-phase / 3-phase solar switching) + Local OCPP 1.6J. |
| **EV Chargers** | **EVNex** (E2 / E2 Plus / X7) | [`ev_chargers/evnex.py`](orchestra/adapters/ev_chargers/evnex.py) | Redirected to Z-Node's local OCPP 1.6J server (`ws://zenith-node.local:9000/ocpp/evnex`) for deterministic `SetChargingProfile` & `RemoteStopTransaction`. |
| **Other Integrations** | **Tuya** (Smart Plugs, DIN Contactors, Sub-Meters) | [`integrations/tuya_local.py`](orchestra/adapters/integrations/tuya_local.py) | **Fake-Cloud AP/EZ-Mode Activator** responds to `s.gw.dev.pk.active` locally on `port 8886`, synthesizing a 16-byte `local_key` without a Tuya IoT account, and speaks Tuya Protocol 3.4 over TCP `6668`. |
| **Other Integrations** | **BOM** (Bureau of Meteorology) | [`integrations/bom_weather.py`](orchestra/adapters/integrations/bom_weather.py) | Zero-auth WA station & Open-Meteo BOM ACCESS-G solar irradiance (`ghiWm2`) + heatwave pre-cool trigger. |
| **Other Integrations** | **Conductor** (Demand Response & WEM Engine) | [`integrations/conductor.py`](orchestra/adapters/integrations/conductor.py) | Native Demand Response engine coupled with `https://conductor.deezlepower.com/api/conductor/forecast` and `wss://conductor.deezlepower.com/v1/broadcast`, 10-day rolling counterfactual baseline calculator, and 1-tap homeowner opt-out. |
| **System Ecosystems** | **Samsung SmartThings** | [`ecosystems/smartthings.py`](orchestra/adapters/ecosystems/smartthings.py) | Local SmartThings Edge LAN Driver over SSDP (`port 39500`) + local Matter bridge (no cloud webhook or PAT required). |
| **System Ecosystems** | **Apple HomeKit** | [`ecosystems/apple_homekit.py`](orchestra/adapters/ecosystems/apple_homekit.py) | Native local HAP bridge (`_hap._tcp.local.` on `port 21063`) with curated entity filtering and instant `X-HM://` QR & 8-digit PIN generation. |
| **System Ecosystems** | **Google Home** | [`ecosystems/google_home.py`](orchestra/adapters/ecosystems/google_home.py) | Local Matter Fabric Bridge (`port 5540` via onboard EFR32MG21 Thread/Wi-Fi radio) + Google Local Home SDK mDNS fulfillment. |
| **System Ecosystems** | **Alexa / Ring** | [`ecosystems/alexa_ring.py`](orchestra/adapters/ecosystems/alexa_ring.py) | Local UPnP/SSDP Emulated Hue + Matter bridge for instant `"Alexa, discover devices"` without AWS Lambda skills, plus local Ring RTSP/ONVIF stream proxy. |
| **Heat Pumps** | **iStore (180L / 270L)** | [`heat_pumps/istore.py`](orchestra/adapters/heat_pumps/istore.py) | Probes onboard Hi-Flying UART-Wi-Fi bridge (`HF-A11ASSISTHREAD` on UDP `48899` -> transparent Modbus RTU-over-TCP on `port 8899`) + Tuya OEM local key (`port 6668`). |

---

## 3. Conductor VPP Integration & Custom Component (`custom_components/orchestra`)

The **Orchestra Custom Component** implements the **Conductor $\leftrightarrow$ Home Assistant Integration Specification (v1.2.0)** for Western Australia's South West Interconnected System (SWIS) WEM and local precinct microgrids.

### 3.1 Privacy-First Regional Broadcast Architecture
Conductor acknowledges privacy concerns and does **not** collect private user data or address individual homes:
* **Anonymous Pub-Sub:** Conductor operates as a regional market transmitter (like an energy weather broadcast). Edge gateways connect as **anonymous subscribers** to the regional WEM market broadcast (`wss://conductor.deezlepower.com/v1/broadcast`).
* **Zero Telemetry Exfiltration:** Edge gateways never upload private household load curves, appliance states, battery SoC, or occupancy data to the cloud.
* **Retail TOU Tariff Settlement:** Operating with physical revenue-grade TOU smart meters that settle variable electricity tariffs and solar credits at the boundary. The in-home dividend (`sensor.orchestra_accumulated_dividend_aud`) is purely educational and calculated locally.
* **Closed-Loop Grid Observability:** Conductor measures collective demand response **directly on the physical electrical grid** (via aggregate WEM Operational Demand MW, Western Power substation telemetry, and wholesale clearing price feedback) and dynamically adjusts its broadcast signals.
* **Cloudflare Workers Durable Objects:** The WebSocket broadcaster runs on Cloudflare Workers Durable Objects using the **WebSocket Hibernation API**, consuming **0 GB-seconds** while edge gateways hibernate.

### 3.2 Granular Asset-Class Directives & Operational Scenarios
Conductor broadcasts high-level signals (`Normal/Auto`, `Energy Soak`, `Load Shed`) alongside granular asset recommendations:
1. **Electric Vehicles (EV)**:
   - Solar Soak: `CHARGE_NOW` up to 32A during negative/zero-cost solar windows.
   - Peak Curtailment: `Load Shed - Charging Paused` with `target_amps: 0` (IEC 61851 pilot suspend).
2. **Heat Pump Hot Water (HPWH)**:
   - Midday Solar Boost: `RUN_NOW` / `THERMAL_BOOST_65C` to 65°C using low-cost energy.
   - Peak Coasting: `THERMAL_COAST` (safe thermal envelope $[50^\circ\text{C}, 65^\circ\text{C}]$).
3. **Air Conditioning (HVAC)**:
   - Summer Pre-Cool: `PRE_COOL` with mode `cool` and setpoint offset $\Delta -1.5^\circ\text{C}$.
   - Winter Pre-Heat: `PRE_HEAT` with mode `heat` and setpoint offset $\Delta +1.5^\circ\text{C}$.
   - Peak Load Shed: `SHED_OFFSET` with setpoint drift $\Delta +1.5^\circ\text{C}$.
4. **Home Battery (BESS)**:
   - Storm Watch: `STORM_WATCH_HOLD` (100% capacity reserve hold from grid).
   - Peak Arbitrage: `PEAK_DISCHARGE` to offset household evening peak.

### 3.3 WAN Outage Failover (Section 6)
If the edge gateway loses WAN connectivity or the WebSocket connection drops:
* **Immediate Default to Normal/Auto:** Controllable loads revert to standard local user schedules and default setpoints ($\Delta 0.0^\circ\text{C}$).
* Guarantees homeowner comfort and predictable appliance behavior during internet disruptions without acting on stale market data.

### 3.4 Entity & Alias Catalog (Section 8)
Orchestra registers canonical entities and backward-compatible Conductor aliases:

| Entity ID (Canonical) | Alias ID (Setup Agent) | Domain | Name | Type / Units | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `sensor.orchestra_wem_operational_demand` | — | `sensor` | WEM Operational Demand | `power` (MW) | Real-time SWIS operational grid demand (p50). |
| `sensor.orchestra_wem_wholesale_price` | — | `sensor` | WEM Wholesale Price | `monetary` (AUD/MWh) | Clearing price RTP with `cents_per_kwh` attribute. |
| `sensor.orchestra_wem_renewable_availability` | — | `sensor` | WEM Renewable Availability | `%` | Total VRE share & duck curve severity. |
| `sensor.orchestra_conductor_control_signal` | `sensor.conductor_dr_ngangk_wem_market_signal` | `sensor` | Conductor Control Signal | String | `Normal/Auto`, `Energy Soak`, `Load Shed`, `Opt-Out`. |
| `sensor.orchestra_ac_dispatch_mode` | — | `sensor` | A/C Dispatch Mode | String | `idle`, `precool`, `preheat`, `shed`. |
| `sensor.orchestra_counterfactual_shed_power` | `sensor.conductor_dr_counterfactual_shed_power` | `sensor` | Verified DR Shed | `power` (kW) | Local verified load shed vs 10-day rolling baseline. |
| `sensor.orchestra_accumulated_dividend_aud` | `sensor.conductor_dr_accumulated_dividend_aud` | `sensor` | In-Home Estimated Benefit | `monetary` (AUD) | Educational savings estimate based on TOU rate differentials. |
| `binary_sensor.orchestra_ev_charge_now` | — | `binary_sensor` | EV Charge Now | `battery_charging` | Trigger to initiate immediate EV charging. |
| `binary_sensor.orchestra_heat_pump_run_now` | — | `binary_sensor` | Heat Pump Run Now | `heat` | Trigger to initiate 65°C heat pump boost. |
| `binary_sensor.orchestra_ac_precool` | — | `binary_sensor` | A/C Pre-Cool | `cold` | Pre-cool living area before peak price/heat. |
| `binary_sensor.orchestra_ac_preheat` | — | `binary_sensor` | A/C Pre-Heat | `heat` | Pre-heat living area during excess solar. |
| `binary_sensor.orchestra_home_battery_storm_watch` | — | `binary_sensor` | Battery Storm Watch | `battery_charging` | Grid top-up and 100% capacity reserve hold. |
| `sensor.orchestra_hvac_setpoint_offset` | — | `sensor` | HVAC Setpoint Offset | °C | Drift to add to each A/C's own setpoint (`0` = no override). Attributes: `dispatch_mode`, `mode`, `action`, `targeted`. |
| `sensor.orchestra_ev_target_amps` | — | `sensor` | EV Target Amps | `current` (A) | Charger current; `0` = pause. Attributes: `charge_now`, `paused`, `action`. |
| `sensor.orchestra_heat_pump_target_temp` | — | `sensor` | Heat Pump Target Temperature | `temperature` (°C) | Tank target, clamped to 50–65 °C. Attributes: `run_now`, `action`. |
| `sensor.orchestra_home_battery_min_reserve` | — | `sensor` | Home Battery Minimum Reserve | % | Backup reserve to hold. Attributes: `charge_from_grid`, `storm_watch`, `action`. |
| `select.orchestra_conductor_dispatch_mode` | — | `select` | Conductor Dispatch Mode | Enum | Manual operator override for field testing. |
| `switch.orchestra_conductor_opt_out` | `switch.conductor_dr_event_opt_out` | `switch` | Customer Opt-Out | Toggle | 1-tap customer opt-out switch. |

The four per-asset sensors are already tranche-gated: when a directive's `cohort` range
excludes this home (or the homeowner opts out, or the WAN drops) they report the normal
schedule, so anything built on them follows the gating without extra logic.

### 3.5 Asset Automation Blueprints
The integration installs four blueprints into `blueprints/automation/orchestra/` on setup,
each reading one directive sensor above. Create automations from them in
**Settings → Automations → Blueprints**, or let the setup agent do it (Stage 6).

| Blueprint | Drives | Behaviour |
| :--- | :--- | :--- |
| `orchestra_hvac_setpoint_offset.yaml` | `climate` | Remembers the setpoint in a baseline helper when a drift starts, sets baseline + `setpoint_offset_c` on every frame (never compounding), restores and releases the baseline when the offset returns to 0. A shed offset is applied downwards in heat mode. A manual setpoint change mid-event hands control back to the homeowner. |
| `orchestra_ev_charge_rate.yaml` | `number` (+ optional `switch`) | Follows `target_amps` within the charger's limits; `0` turns the charging switch off (or drops to the minimum). Returns to your normal current on `NORMAL`. |
| `orchestra_heat_pump_target.yaml` | `water_heater` | Follows `target_temp_c`, optionally with a run-now operation mode; returns to your normal temperature. |
| `orchestra_home_battery_reserve.yaml` | `number` (+ optional `switch`) | Holds `min_soc_reserve_pct` and grid charging; returns to your normal reserve on self-consumption. |

The HVAC blueprint needs one `input_number` helper per unit (min `0`, max `40`, step `0.1`;
`0` means no drift is held). The setup agent creates these; by hand, add them under
**Settings → Devices & services → Helpers → Number**.

---

## 4. The "So Easy Grandpa Can Set It Up" Agent Framework ([`orchestra/agent/`](orchestra/agent/))

The user never sets up Home Assistant—the **Orchestra Agent** ([`GrandpaCommissioningAgent`](orchestra/agent/framework.py)) executes a 6-stage autonomous workflow:

1. **Stage 1 — Zero-Cloud Hijack & Standard HA Discovery Ingestion**: Onboards all local solar, battery, HVAC, heat pump, EV charger, and Tuya hardware AND ingests standard Home Assistant auto-discovered devices (Smart TVs, Hue/LIFX lights, garage roller doors, Shelly CT clamps).
2. **Stage 2 — Active Load-Stepping Circuit Identification ([`circuit_identifier.py`](orchestra/agent/circuit_identifier.py))**:
   - Captures a quiet baseline wattage across all unidentified power monitor channels (`Channel A`, `Channel B`, `Channel C`, `Circuit 4`).
   - Steps each controllable heavy load ON briefly (`iStore Hot Water Heat Pump` $\rightarrow +950\text{ W}$, `Main Ducted AC` $\rightarrow +2650\text{ W}$, `EV Charger` $\rightarrow +3680\text{ W}$, `Pool/Utility Contactor` $\rightarrow +1180\text{ W}$), correlates the live wattage jump ($\Delta W$), restores the appliance, and renames the circuit sensor (`"iStore Hot Water Heat Pump Circuit Power"`).
   - Guides the homeowner through a 10-second kitchen kettle test for any remaining non-smart circuit.
3. **Stage 3 — Semantic Device Naming & Entity Hygiene ([`entity_organizer.py`](orchestra/agent/entity_organizer.py))**:
   - Renames cryptic IDs (`media_player.lg_webos_tv_oled65_992a` $\rightarrow$ `"Lounge Room Smart TV"`) and assigns rooms.
   - Automatically hides technical/diagnostic noise (`*_rssi`, `*_uptime`, `*_reboot`, `update.*`) via `hidden_by="orchestra_agent"`.
4. **Stage 4 — Conductor Demand Response Plugin Binding ([`dr_binder.py`](orchestra/agent/dr_binder.py))**:
   - Assigns all named controllable appliances **together with their load-step-verified CT circuit power sensors** into `.storage/conductor.entity_bindings` and the `conductor` ConfigEntry.
5. **Stage 5 — Custom Grandpa-Simple Dashboards ([`dashboard_builder.py`](orchestra/agent/dashboard_builder.py))**:
   - Writes `.storage/lovelace` with two clean views (**`My Home`** with a 1-tap *"Pause Grid Saving Event"* button and organized room cards, and **`Energy & Money Saved`** showing daily/monthly AUD savings, VPP cash credits, and verified switchboard circuits) plus pre-configures `.storage/energy`.
6. **Stage 6 — Guardrailed Automation Commit & Plain-English Handover ([`automation_builder.py`](orchestra/agent/automation_builder.py))**:
   - Fills the asset blueprints (section 3.5) in with every bound A/C, EV charger, heat pump, and battery reserve, creates each A/C's baseline helper in `.storage/input_number`, and commits the automations to `automations.yaml`.

---

## 5. Quick Start & Raspberry Pi CLI Commands

```bash
# 1. Run the full 6-stage Grandpa-Simple Autonomous Agent setup
/opt/homebrew/bin/python3 -m orchestra.cli grandpa-setup --config-dir ./ha_config_sandbox

# 2. Probe your LAN for an iStore 180L / 270L Heat Pump Wi-Fi & Modbus board (on your Raspberry Pi)
/opt/homebrew/bin/python3 -m orchestra.cli probe-istore

# 3. Run the raw Home Assistant onboarding hijack & 23-target local injection
/opt/homebrew/bin/python3 -m orchestra.cli hijack --config-dir ./ha_config_sandbox

# 4. View the complete zero-cloud hardware fingerprint matrix
/opt/homebrew/bin/python3 -m orchestra.cli matrix

# 5. Run the unit and integration test suite
/opt/homebrew/bin/python3 -m unittest discover -s tests -v
```

---

## 6. Flashing the Pre-Loaded Raspberry Pi 5 (`rpi5-64`) HAOS Image

The pre-built Home Assistant OS 15.2 (`rpi5-64`) image with all Orchestra modifications pre-loaded is located in [`dist/haos_rpi5-64_orchestra_preloaded.img.xz`](dist/haos_rpi5-64_orchestra_preloaded.img.xz) (and uncompressed at [`dist/haos_rpi5-64_orchestra_preloaded.img`](dist/haos_rpi5-64_orchestra_preloaded.img)).

### Option A: Raspberry Pi Imager / BalenaEtcher (GUI)
1. Open **Raspberry Pi Imager** (or **BalenaEtcher**).
2. Choose **Raspberry Pi 5** as the device, then choose **Use custom** OS image and select:
   `dist/haos_rpi5-64_orchestra_preloaded.img.xz`
3. Select your MicroSD card or NVMe SSD (16 GB+ recommended) and click **Write**. (Do **not** apply Raspberry Pi Imager OS customisation settings when prompted—Orchestra's `CONFIG` and `.storage` payload are already embedded.)

### Option B: Rebuild with Your Home Wi-Fi Pre-Configured
If your Raspberry Pi 5 will connect via Wi-Fi instead of Ethernet, bake your Wi-Fi credentials into the `CONFIG/network/orchestra-wifi.nmconnection` profile before flashing:

```bash
/opt/homebrew/bin/python3 haos_image/build_rpi_image.py \
  --board rpi5-64 \
  --base-image build/haos_rpi5-64-15.2.img.xz \
  --wifi-ssid "YourHomeWiFi" \
  --wifi-psk "YourWiFiPassword"
```
*(also supports `--board rpi3-64` and `--board rpi4-64`)*

