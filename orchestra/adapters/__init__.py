"""
Master Registry of Orchestra Zero-Cloud Hardware & Ecosystem Adapters.

Maps every `(DeviceCategory, HardwareBrand)` combination from the first-pass
hardware specification to its concrete `HardwareAdapter` implementation.
"""

from __future__ import annotations

from orchestra.adapters.base import HardwareAdapter
from orchestra.adapters.ecosystems import (
    AlexaRingLocalBridgeAdapter,
    AppleHomeKitBridgeAdapter,
    GoogleHomeLocalBridgeAdapter,
    SamsungSmartThingsLocalBridgeAdapter,
)
from orchestra.adapters.ev_chargers import (
    EVNexChargerAdapter,
    FroniusWattpilotAdapter,
    LocalOCPP16JServer,
    SigenergyEVChargerAdapter,
    TeslaWallConnectorAdapter,
)
from orchestra.adapters.heat_pumps import (
    IStoreHeatPumpAdapter,
    RheemHeatPumpAdapter,
    RinnaiHeatPumpAdapter,
    StiebelEltronHeatPumpAdapter,
    TuyaWhiteLabelHeatPumpAdapter,
)
from orchestra.adapters.hvac import (
    AdvantageAirLocalAdapter,
    PolyaireAirTouch5Adapter,
    SensiboLocalHVACAdapter,
)
from orchestra.adapters.integrations import (
    BOMWeatherAdapter,
    ConductorDemandResponseAdapter,
    TuyaCloudFreeActivator,
    TuyaLocalAdapter,
)
from orchestra.adapters.solar_battery import (
    EnphaseSolarBatteryAdapter,
    FroniusSolarBatteryAdapter,
    SigenergySolarBatteryAdapter,
    TeslaSolarBatteryAdapter,
)
from orchestra.config import OrchestraConfig
from orchestra.models import DeviceCategory, HardwareBrand


ADAPTER_CLASS_REGISTRY: dict[tuple[DeviceCategory, HardwareBrand], type[HardwareAdapter]] = {
    # Solar / Batteries
    (DeviceCategory.SOLAR_BATTERY, HardwareBrand.TESLA): TeslaSolarBatteryAdapter,
    (DeviceCategory.SOLAR_BATTERY, HardwareBrand.SIGENERGY): SigenergySolarBatteryAdapter,
    (DeviceCategory.SOLAR_BATTERY, HardwareBrand.FRONIUS): FroniusSolarBatteryAdapter,
    (DeviceCategory.SOLAR_BATTERY, HardwareBrand.ENPHASE): EnphaseSolarBatteryAdapter,
    # Airconditioning
    (DeviceCategory.AIRCONDITIONING, HardwareBrand.POLYAIRE_AIRTOUCH5): PolyaireAirTouch5Adapter,
    (DeviceCategory.AIRCONDITIONING, HardwareBrand.SENSIBO): SensiboLocalHVACAdapter,
    (DeviceCategory.AIRCONDITIONING, HardwareBrand.ADVANTAGE_AIR): AdvantageAirLocalAdapter,
    # Heat Pump Hot Water
    (DeviceCategory.HEAT_PUMP, HardwareBrand.STIEBEL_ELTRON): StiebelEltronHeatPumpAdapter,
    (DeviceCategory.HEAT_PUMP, HardwareBrand.RINNAI): RinnaiHeatPumpAdapter,
    (DeviceCategory.HEAT_PUMP, HardwareBrand.RHEEM): RheemHeatPumpAdapter,
    (DeviceCategory.HEAT_PUMP, HardwareBrand.TUYA_WHITELABEL_HP): TuyaWhiteLabelHeatPumpAdapter,
    (DeviceCategory.HEAT_PUMP, HardwareBrand.ISTORE): IStoreHeatPumpAdapter,
    # EV Chargers
    (DeviceCategory.EV_CHARGER, HardwareBrand.TESLA): TeslaWallConnectorAdapter,
    (DeviceCategory.EV_CHARGER, HardwareBrand.SIGENERGY): SigenergyEVChargerAdapter,
    (DeviceCategory.EV_CHARGER, HardwareBrand.FRONIUS): FroniusWattpilotAdapter,
    (DeviceCategory.EV_CHARGER, HardwareBrand.EVNEX): EVNexChargerAdapter,
    # Other Integrations
    (DeviceCategory.OTHER_INTEGRATION, HardwareBrand.TUYA): TuyaLocalAdapter,
    (DeviceCategory.OTHER_INTEGRATION, HardwareBrand.BOM): BOMWeatherAdapter,
    (DeviceCategory.OTHER_INTEGRATION, HardwareBrand.CONDUCTOR): ConductorDemandResponseAdapter,
    # System Ecosystem Bridges
    (DeviceCategory.SYSTEM_ECOSYSTEM, HardwareBrand.SMARTTHINGS): SamsungSmartThingsLocalBridgeAdapter,
    (DeviceCategory.SYSTEM_ECOSYSTEM, HardwareBrand.APPLE_HOMEKIT): AppleHomeKitBridgeAdapter,
    (DeviceCategory.SYSTEM_ECOSYSTEM, HardwareBrand.GOOGLE_HOME): GoogleHomeLocalBridgeAdapter,
    (DeviceCategory.SYSTEM_ECOSYSTEM, HardwareBrand.ALEXA_RING): AlexaRingLocalBridgeAdapter,
}


def get_adapter_for_hardware(
    category: DeviceCategory,
    brand: HardwareBrand,
    config: OrchestraConfig,
) -> HardwareAdapter:
    """Instantiate the appropriate zero-cloud adapter for a `(category, brand)` pair."""
    key = (category, brand)
    if key not in ADAPTER_CLASS_REGISTRY:
        raise KeyError(f"No Orchestra adapter registered for category={category} brand={brand}")
    adapter_cls = ADAPTER_CLASS_REGISTRY[key]
    return adapter_cls(config)


__all__ = [
    "ADAPTER_CLASS_REGISTRY",
    "AdvantageAirLocalAdapter",
    "AlexaRingLocalBridgeAdapter",
    "AppleHomeKitBridgeAdapter",
    "BOMWeatherAdapter",
    "ConductorDemandResponseAdapter",
    "EVNexChargerAdapter",
    "EnphaseSolarBatteryAdapter",
    "FroniusSolarBatteryAdapter",
    "FroniusWattpilotAdapter",
    "GoogleHomeLocalBridgeAdapter",
    "HardwareAdapter",
    "LocalOCPP16JServer",
    "PolyaireAirTouch5Adapter",
    "RheemHeatPumpAdapter",
    "RinnaiHeatPumpAdapter",
    "SamsungSmartThingsLocalBridgeAdapter",
    "SensiboLocalHVACAdapter",
    "SigenergyEVChargerAdapter",
    "SigenergySolarBatteryAdapter",
    "StiebelEltronHeatPumpAdapter",
    "TeslaSolarBatteryAdapter",
    "TeslaWallConnectorAdapter",
    "TuyaCloudFreeActivator",
    "TuyaLocalAdapter",
    "TuyaWhiteLabelHeatPumpAdapter",
    "get_adapter_for_hardware",
]
