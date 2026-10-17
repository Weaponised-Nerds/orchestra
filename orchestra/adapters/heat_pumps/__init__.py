"""Heat Pump Hot Water (HPWH) Zero-Cloud Adapters."""

from orchestra.adapters.heat_pumps.istore import IStoreHeatPumpAdapter
from orchestra.adapters.heat_pumps.rheem import RheemHeatPumpAdapter
from orchestra.adapters.heat_pumps.rinnai import RinnaiHeatPumpAdapter
from orchestra.adapters.heat_pumps.stiebel_eltron import StiebelEltronHeatPumpAdapter
from orchestra.adapters.heat_pumps.tuya_whitelabel import TuyaWhiteLabelHeatPumpAdapter

__all__ = [
    "IStoreHeatPumpAdapter",
    "RheemHeatPumpAdapter",
    "RinnaiHeatPumpAdapter",
    "StiebelEltronHeatPumpAdapter",
    "TuyaWhiteLabelHeatPumpAdapter",
]
