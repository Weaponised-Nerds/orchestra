# Ngangk WEM Predictive Energy Engine
## REST API Reference & Developer Documentation

**Version:** 1.0.0  
**Base URL:** `https://ngangk.deeben.workers.dev`  
**Protocol:** HTTPS / JSON  
**CORS:** Enabled (`*`)  
**Data Sources:** AEMO WA WEM Historical Dispatch + Open-Meteo BOM ACCESS Numerical Weather Prediction  

---

## 1. Quick Start

Ngangk delivers 30-minute trading interval forecasts for the Western Australian South West Interconnected System (SWIS), conditioning machine learning models on live Bureau of Meteorology (BOM) weather forecasts and simulating maintenance outages.

```bash
# Query the live 24-hour (48 interval) forecast
curl -X GET "https://ngangk.deeben.workers.dev/api/ngangk/forecast?horizon=24"

# Query the forecast assuming Collie G1 (340 MW) is offline for maintenance
curl -X GET "https://ngangk.deeben.workers.dev/api/ngangk/forecast?horizon=24&offline_mw=340"
```

---

## 2. Authentication & Rate Limiting

* **Current Environment:** Public access enabled with open CORS headers.
* **Headers:**
  ```http
  Content-Type: application/json
  Accept: application/json
  ```
* **Rate Limits:** Cloudflare Worker edge execution allows up to 100,000 requests/day on the free tier, with sub-15ms cached response latency.

---

## 3. Endpoints Overview

| Endpoint | Method | Persona / Category | Description |
| :--- | :--- | :--- | :--- |
| [`/api/ngangk/health`](#endpoint-1-get-apingangkhealth) | `GET` | System | Health check, model version, and validation metrics (WAPE, RMSE). |
| [`/api/ngangk/forecast`](#endpoint-2-get-apingangkforecast) | `GET` | Core ML | Multi-quantile P10/P50/P90 demand, wholesale price, and solar forecast. |
| [`/api/ngangk/optimize/utility`](#endpoint-3-post-apingangkoptimizeutility) | `POST` | Utility | Generating fleet co-optimization with competitor outage simulator. |
| [`/api/ngangk/optimize/industry`](#endpoint-4-post-apingangkoptimizeindustry) | `POST` | C&I | Behind-the-meter solar & BESS peak demand charge shaving (RT4/RT7). |
| [`/api/ngangk/optimize/residential`](#endpoint-5-post-apingangkoptimizeresidential) | `POST` | Residential | Dynamic tariff signals, EV solar soaking, and HomeAssistant appliance sinks. |

---

## Endpoint 1: `GET /api/ngangk/health`

Returns engine status and model training metrics.

### Response `200 OK`
```json
{
  "status": "healthy",
  "model": {
    "model_name": "Ngangk WEM Multi-Horizon Predictive Engine",
    "version": "1.0.0",
    "trained_at": "2026-10-15T20:23:35.767265",
    "records_trained": 52592,
    "records_tested": 31295,
    "metrics": {
      "demand_wape_pct": 8.14,
      "demand_rmse_mw": 239.3,
      "price_mae_aud_mwh": 30.42
    }
  },
  "engine": "Ngangk WEM Multi-Horizon Predictive Engine"
}
```

---

## Endpoint 2: `GET /api/ngangk/forecast`

Runs the multi-quantile predictive model against the latest 7-day BOM ACCESS weather forecast.

### Query Parameters
| Parameter | Type | Required | Default | Description |
| :--- | :--- | :--- | :--- | :--- |
| `horizon` | `integer` | No | `24` | Forecast horizon in hours (`24` = 48 intervals, up to `168` = 7 days). |
| `offline_mw` | `number` | No | `0` | Total competitor generator capacity (MW) offline for maintenance. Dynamically shifts the wholesale clearing price curve upwards. |

### Sample Request
```http
GET /api/ngangk/forecast?horizon=24&offline_mw=340 HTTP/1.1
Host: ngangk.deeben.workers.dev
```

### Response `200 OK` (Truncated to 1 Interval)
```json
{
  "status": "success",
  "generatedAt": "2026-10-15T20:45:00.000Z",
  "offlineSimulatedMW": 340,
  "horizonHours": 24,
  "intervalsCount": 48,
  "forecast": [
    {
      "intervalIdx": 1,
      "tradingIntervalIdx": 17,
      "timestamp": "2026-10-16T08:00:00.000Z",
      "timeFormatted": "08:00 AWST",
      "weather": {
        "tempC": 21.4,
        "ghiWm2": 420.0,
        "windSpeedKmh": 18.2,
        "cloudCoverPct": 5.0,
        "heatwaveAlert": 0
      },
      "demandMW": {
        "p10": 1943,
        "p50": 2073,
        "p90": 2203
      },
      "renewablesMW": {
        "estimatedDPV": 655.2,
        "utilityWind": 285.0,
        "utilitySolar": 82.5,
        "totalVRE": 1022.7,
        "duckCurveSeverityPct": 37.5
      },
      "wholesalePriceRTP": {
        "p10": 62.40,
        "p50": 88.50,
        "p90": 128.32,
        "outageImpactAdder": 54.40,
        "isNegative": false
      },
      "fcessPrices": {
        "contingencyRaise": 14.50,
        "contingencyLower": 19.20,
        "rocof": 6.80
      }
    }
  ]
}
```

---

## Endpoint 3: `POST /api/ngangk/optimize/utility`

Optimizes a grid-scale asset portfolio (coal, gas, solar, wind, BESS) against predicted wholesale market prices and simulated competitor maintenance outages.

### Request Body
```json
{
  "offlineAssets": [
    { "name": "Collie G1", "capacityMW": 340 },
    { "name": "Muja G7", "capacityMW": 200 }
  ],
  "portfolio": [
    {
      "id": "muja_g7",
      "name": "Muja Coal Unit 7",
      "type": "coal",
      "capacityMW": 200,
      "marginalCostAUD": 38.0
    },
    {
      "id": "pinjar_gt",
      "name": "Pinjar Gas Peaker GT1",
      "type": "gas",
      "capacityMW": 120,
      "marginalCostAUD": 85.0
    },
    {
      "id": "merredin_solar",
      "name": "Merredin Solar Farm",
      "type": "solar",
      "capacityMW": 100,
      "marginalCostAUD": 0.0
    },
    {
      "id": "kwinana_bess",
      "name": "Kwinana BESS 2",
      "type": "bess",
      "capacityMW": 100,
      "storageMWh": 400,
      "currentSoCPct": 50.0
    }
  ]
}
```

### Response `200 OK`
```json
{
  "status": "success",
  "portfolioSummary": {
    "totalRevenueAUD": 1044447,
    "intervalsCount": 48,
    "offlineCapacitySimulatedMW": 540
  },
  "schedule": [
    {
      "intervalIdx": 1,
      "timeFormatted": "08:00 AWST",
      "clearingPriceAUD": 142.90,
      "totalDispatchedMW": 420.0,
      "intervalRevenueAUD": 30009.00,
      "assets": [
        { "assetId": "muja_g7", "name": "Muja Coal Unit 7", "type": "coal", "dispatchedMW": 200, "bidPrice": 38.0, "revenueAUD": 14290.00 },
        { "assetId": "pinjar_gt", "name": "Pinjar Gas Peaker GT1", "type": "gas", "dispatchedMW": 120, "bidPrice": 85.0, "revenueAUD": 8574.00 },
        { "assetId": "merredin_solar", "name": "Merredin Solar Farm", "type": "solar", "dispatchedMW": 0.0, "bidPrice": 0.0, "revenueAUD": 0.00 },
        { "assetId": "kwinana_bess", "name": "Kwinana BESS 2", "type": "bess", "dispatchedMW": 100, "bidPrice": 45.0, "revenueAUD": 7145.00, "socPct": 37.5 }
      ]
    }
  ]
}
```

---

## Endpoint 4: `POST /api/ngangk/optimize/industry`

Computes the optimal behind-the-meter (BTM) solar and battery schedule to shave coincident peak network demand charges under Western Power RT4/RT7 tariffs.

### Request Body
```json
{
  "loadMW": 15.0,
  "operatingHours": "24_7", // Options: "24_7" | "day_shift" | "two_shifts"
  "solarMWp": 6.0,
  "bessMWh": 16.0,
  "bessMW": 4.0,
  "initialSoCPct": 40.0
}
```

### Response `200 OK`
```json
{
  "status": "success",
  "summary": {
    "baselinePeakMW": 15.0,
    "newPeakGridMW": 11.0,
    "peakDemandReductionMW": 4.0,
    "dailyEnergySavingsAUD": 2206,
    "monthlyDemandChargeSavingsAUD": 74000,
    "totalEstimatedMonthlySavingsAUD": 140180,
    "solarSelfConsumptionPct": 92.5
  },
  "schedule": [
    {
      "intervalIdx": 25,
      "timeFormatted": "12:00 AWST",
      "priceAUD": -14.50,
      "baselineLoadMW": 15.0,
      "solarGenMW": 4.92,
      "bessPowerMW": -4.0,
      "bessSoCPct": 62.5,
      "netGridImportMW": 14.08,
      "isPeakShaving": false
    },
    {
      "intervalIdx": 37,
      "timeFormatted": "18:00 AWST",
      "priceAUD": 165.20,
      "baselineLoadMW": 15.0,
      "solarGenMW": 0.0,
      "bessPowerMW": 4.0,
      "bessSoCPct": 37.5,
      "netGridImportMW": 11.0,
      "isPeakShaving": true
    }
  ]
}
```

---

## Endpoint 5: `POST /api/ngangk/optimize/residential`

Calculates dynamic household energy optimization, identifying solar soak windows (charging EVs and water heaters during negative prices) and battery discharge during the evening peak.

### Request Body
```json
{
  "solarKW": 6.6,
  "batteryKWH": 10.0,
  "hasEV": true,
  "evCapacityKWH": 60.0,
  "initialBatterySoCPct": 35.0,
  "initialEVSoCPct": 40.0
}
```

### Response `200 OK`
```json
{
  "status": "success",
  "summary": {
    "dailySavingsAUD": 10.82,
    "monthlyProjectedSavingsAUD": 325,
    "solarSelfConsumptionPct": 94.2,
    "totalSolarSoakedKWH": 14.0,
    "totalSolarGenKWH": 30.7
  },
  "actionableSuggestions": [
    {
      "appliance": "Electric Vehicle (EV)",
      "recommendation": "Plug in vehicle between 11:30 AM and 2:30 PM",
      "reason": "Market clearing price drops to -$14.50/MWh with 82% renewable grid saturation. Free solar soaking saves $8.50 per charge cycle.",
      "icon": "zap"
    },
    {
      "appliance": "Home Battery (10 kWh)",
      "recommendation": "Reserve 80%+ state-of-charge for the 5:30 PM - 8:30 PM peak window",
      "reason": "Discharging during the evening peak avoids peak grid imports and supports local network frequency stability.",
      "icon": "battery-charging"
    },
    {
      "appliance": "Heat Pump Hot Water",
      "recommendation": "Shift daily heating timer to 12:00 PM - 2:00 PM",
      "reason": "Consumes 3.6 kWh of rooftop solar that would otherwise be curtailed by Western Power emergency back-off signals.",
      "icon": "flame"
    },
    {
      "appliance": "Air Conditioning",
      "recommendation": "Pre-cool living areas to 20°C starting at 1:00 PM",
      "reason": "Thermal mass of the home acts as an energy battery, allowing you to turn off heavy compressors during high-tariff 6:00 PM heat spikes.",
      "icon": "wind"
    }
  ],
  "schedule": [
    {
      "intervalIdx": 25,
      "timeFormatted": "12:00 AWST",
      "marketSignal": "SUPER_OFF_PEAK_SOLAR_SOAK",
      "signalColor": "green",
      "rtpPrice": -14.50,
      "solarKW": 5.15,
      "homeLoadKW": 0.50,
      "batteryActionKW": -3.00,
      "batterySoCPct": 71.0,
      "applianceSinks": [
        { "id": "ev_charger", "name": "Electric Vehicle Smart Diverter", "action": "CHARGING_SOLAR_SOAK", "loadKW": 7.2 },
        { "id": "heat_pump_water", "name": "Heat Pump Hot Water Heater", "action": "RUNNING_BOOST", "loadKW": 1.8 }
      ]
    }
  ]
}
```

---

## 4. Code Examples

### Python (`requests`)
```python
import requests

BASE_URL = "https://ngangk.deeben.workers.dev"

# 1. Fetch 24h Forecast
forecast_resp = requests.get(f"{BASE_URL}/api/ngangk/forecast", params={"horizon": 24, "offline_mw": 340})
forecast = forecast_resp.json()

print(f"Generated at: {forecast['generatedAt']}")
print(f"Interval 1 Demand (P50): {forecast['forecast'][0]['demandMW']['p50']} MW")

# 2. Run C&I Facility Optimization
ci_payload = {
    "loadMW": 10.0,
    "operatingHours": "24_7",
    "solarMWp": 5.0,
    "bessMWh": 12.0
}
opt_resp = requests.post(f"{BASE_URL}/api/ngangk/optimize/industry", json=ci_payload)
savings = opt_resp.json()["summary"]
print(f"Estimated Monthly Savings: ${savings['totalEstimatedMonthlySavingsAUD']:,} AUD")
```

### JavaScript / Node.js (`fetch`)
```javascript
const BASE_URL = "https://ngangk.deeben.workers.dev";

// Fetch Residential Recommendations
async function getResidentialSchedules() {
  const res = await fetch(`${BASE_URL}/api/ngangk/optimize/residential`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      solarKW: 6.6,
      batteryKWH: 13.5,
      hasEV: true
    })
  });
  const data = await res.json();
  console.log("Actionable suggestions:", data.actionableSuggestions);
}

getResidentialSchedules();
```

---

## 5. HomeAssistant Integration Guide

HomeAssistant can consume the Ngangk API as a REST sensor to drive local smart plugs, EV diverters, and heat pumps automatically.

Add this block to your HomeAssistant `configuration.yaml`:

```yaml
sensor:
  - platform: rest
    name: "Ngangk WEM Market Signal"
    resource: "https://ngangk.deeben.workers.dev/api/ngangk/forecast?horizon=1"
    scan_interval: 900 # Poll every 15 minutes
    value_template: >-
      {% if value_json.forecast[0].wholesalePriceRTP.p50 < 0 %}
        SOLAR_SOAK
      {% elif value_json.forecast[0].wholesalePriceRTP.p50 > 120 %}
        PEAK_DISCHARGE
      {% else %}
        NORMAL
      {% endif %}
    json_attributes_path: "$.forecast[0]"
    json_attributes:
      - wholesalePriceRTP
      - renewablesMW
      - demandMW

automation:
  - alias: "EV Solar Soak: Start Charging during Negative WEM Price"
    trigger:
      - platform: state
        entity_id: sensor.ngangk_wem_market_signal
        to: "SOLAR_SOAK"
    action:
      - service: switch.turn_on
        target:
          entity_id: switch.ev_charger_diverter
      - service: notify.mobile_app
        data:
          message: "WEM price is negative. Commencing EV solar soaking."
```
