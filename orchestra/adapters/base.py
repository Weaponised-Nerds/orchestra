"""Abstract base class for Orchestra Cloud-Liberation Hardware Adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod

from orchestra.config import OrchestraConfig
from orchestra.models import (
    DeviceCategory,
    DiagnosticResult,
    DiscoveredHardware,
    HAConfigEntrySpec,
    HardwareBrand,
    LocalCredentials,
)


class HardwareAdapter(ABC):
    """
    Base interface for all brand-specific zero-cloud hardware adapters.
    Each adapter knows how to:
    1. Extract or synthesize local credentials without cloud accounts (`extract_local_credentials`)
    2. Build a ready-to-inject Home Assistant ConfigEntry & curated entity set (`build_ha_config_entry`)
    3. Execute a safe local controllability pulse check (`verify_local_control`)
    """

    brand: HardwareBrand
    category: DeviceCategory

    def __init__(self, config: OrchestraConfig) -> None:
        self.config = config

    @abstractmethod
    async def extract_local_credentials(
        self,
        hardware: DiscoveredHardware,
    ) -> LocalCredentials:
        """Obtain local keys, tokens, or socket bindings without vendor cloud login."""

    @abstractmethod
    def build_ha_config_entry(
        self,
        hardware: DiscoveredHardware,
        credentials: LocalCredentials,
    ) -> HAConfigEntrySpec:
        """Build the Home Assistant ConfigEntry and curated entities for direct injection."""

    @abstractmethod
    async def verify_local_control(
        self,
        hardware: DiscoveredHardware,
        credentials: LocalCredentials,
        action: str = "default_diagnostic",
    ) -> DiagnosticResult:
        """Verify local read/write control and enforce hardware safety guardrails."""
