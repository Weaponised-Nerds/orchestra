"""Airconditioning (HVAC) Zero-Cloud Adapters."""

from orchestra.adapters.hvac.advantage_air import AdvantageAirLocalAdapter
from orchestra.adapters.hvac.airtouch5 import PolyaireAirTouch5Adapter
from orchestra.adapters.hvac.sensibo import SensiboLocalHVACAdapter

__all__ = [
    "AdvantageAirLocalAdapter",
    "PolyaireAirTouch5Adapter",
    "SensiboLocalHVACAdapter",
]
