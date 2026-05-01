from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


class OverlayManager:
    """Owns the pynq.Overlay lifecycle: load, unload, IP inventory, version."""

    def __init__(self) -> None:
        self._overlay: Optional[Any] = None
        self._loaded_name: Optional[str] = None
        self._ip_inventory: List[str] = []
        self._version: str = ""

    # ------------------------------------------------------------------
    # Synchronous API (called during startup or from run_in_executor)
    # ------------------------------------------------------------------

    def load(self, bitfile: str) -> None:
        """Load bitfile + HWH using pynq.Overlay. Raises RuntimeError on failure."""
        try:
            import pynq  # type: ignore[import]
        except ImportError as exc:
            raise RuntimeError("pynq module not available") from exc

        try:
            overlay = pynq.Overlay(bitfile)
        except Exception as exc:
            raise RuntimeError(f"Failed to load overlay {bitfile!r}: {exc}") from exc

        self._overlay = overlay
        self._loaded_name = os.path.basename(bitfile)
        self._ip_inventory = list(getattr(overlay, "ip_dict", {}).keys())
        # Version from HWH metadata; fall back to stem of filename
        self._version = (
            getattr(overlay, "version", None)
            or os.path.splitext(self._loaded_name)[0]
        )
        logger.info("Overlay loaded: %s  IPs: %s", self._loaded_name, self._ip_inventory)

    def unload(self) -> None:
        self._overlay = None
        self._loaded_name = None
        self._ip_inventory = []
        self._version = ""
        logger.info("Overlay unloaded")

    # ------------------------------------------------------------------
    # Async wrappers
    # ------------------------------------------------------------------

    async def load_async(self, bitfile: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self.load, bitfile)

    # ------------------------------------------------------------------
    # State queries
    # ------------------------------------------------------------------

    def is_loaded(self) -> bool:
        return self._loaded_name is not None

    def get_ip(self, name: str) -> Any:
        if self._overlay is None:
            raise RuntimeError("No overlay loaded")
        try:
            return getattr(self._overlay, name)
        except AttributeError as exc:
            raise KeyError(f"IP {name!r} not found in overlay") from exc

    def inventory(self) -> List[str]:
        return list(self._ip_inventory)

    def version(self) -> str:
        return self._version

    def status(self) -> str:
        if self._loaded_name:
            return f"LOADED:{self._loaded_name}"
        return "NONE"

    def missing_ips(self, required: List[str]) -> List[str]:
        """Return required IPs absent from the current inventory."""
        return [ip for ip in required if ip not in self._ip_inventory]


class MockOverlayManager(OverlayManager):
    """Test stand-in — never calls pynq.Overlay."""

    def __init__(self, ip_names: Optional[List[str]] = None) -> None:
        super().__init__()
        if ip_names is not None:
            self._loaded_name = "mock.bit"
            self._ip_inventory = list(ip_names)
            self._version = "mock-1.0"
            self._overlay = object()  # non-None sentinel

    def load(self, bitfile: str) -> None:
        self._loaded_name = os.path.basename(bitfile)
        self._version = os.path.splitext(self._loaded_name)[0]
        logger.info("MockOverlayManager: loaded %s", self._loaded_name)

    def get_ip(self, name: str) -> Any:
        if not self.is_loaded():
            raise RuntimeError("No overlay loaded")
        # Return a simple namespace so tests can set attributes freely
        class _FakeIP:
            pass
        return _FakeIP()
