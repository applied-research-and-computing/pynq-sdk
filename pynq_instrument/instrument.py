from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, List, Optional

from .command_registry import CommandDescriptor, CommandRegistry, CommandType, ParamDescriptor
from .hardware_backend import HardwareBackend, MockBackend
from .response_helpers import respond_enum

logger = logging.getLogger(__name__)


class Instrument:
    """
    Top-level entry point for a Carbon PYNQ instrument.

    Usage::

        inst = Instrument("Acme", "ADC-Logger", "SN-001", "1.0.0")

        @inst.command("ADC:READ?", type=CommandType.QUERY, requires=["adc_0"])
        async def adc_read(adc_0):
            ...

        await inst.start_async()
    """

    def __init__(
        self,
        manufacturer: str,
        model: str,
        serial: str,
        firmware_version: str,
        backend: Optional[HardwareBackend] = None,
    ) -> None:
        self.manufacturer = manufacturer
        self.model = model
        self.serial = serial
        self.firmware_version = firmware_version

        self.registry = CommandRegistry()
        self.backend: HardwareBackend = backend if backend is not None else MockBackend()
        self.overlay_manager: Optional[Any] = None

        self._port: int = 4880
        self._async_port: int = 4881

    # ------------------------------------------------------------------
    # @inst.command decorator
    # ------------------------------------------------------------------

    def command(
        self,
        scpi_command: str,
        *,
        type: CommandType = CommandType.QUERY,
        requires: Optional[List[str]] = None,
        requires_overlay: Optional[str] = None,
        params: Optional[List[ParamDescriptor]] = None,
        timeout_ms: int = 5000,
        group: str = "",
        description: str = "",
    ) -> Callable:
        """Register a handler function as a SCPI command."""

        def decorator(fn: Callable) -> Callable:
            desc = CommandDescriptor(
                scpi_command=scpi_command.upper(),
                type=type,
                handler=fn,
                requires_ips=requires or [],
                requires_overlay=requires_overlay,
                params=params or [],
                timeout_ms=timeout_ms,
                group=group,
                description=description,
            )
            self.registry.register(desc)
            return fn

        return decorator

    def trigger(self, fn: Callable[[], None]) -> Callable[[], None]:
        """Register a callback invoked on HiSLIP TRIGGER messages (GET)."""
        self.registry.set_trigger_callback(fn)
        return fn

    # ------------------------------------------------------------------
    # Overlay management
    # ------------------------------------------------------------------

    def load_overlay(self, bitfile: str) -> None:
        """Load a PYNQ overlay synchronously. Call before start_async()."""
        self._ensure_overlay_manager()
        self.overlay_manager.load(bitfile)  # type: ignore[union-attr]

    def use_pynq_backend(self) -> None:
        """Switch from MockBackend to PYNQBackend. Call before load_overlay()."""
        from .hardware_backend import PYNQBackend
        from .overlay_manager import OverlayManager

        om = OverlayManager()
        self.overlay_manager = om
        self.backend = PYNQBackend(om)

    # ------------------------------------------------------------------
    # Startup
    # ------------------------------------------------------------------

    async def start_async(
        self,
        port: int = 4880,
        async_port: int = 4881,
        advertise: bool = True,
    ) -> None:
        """
        Register built-in commands, start mDNS, and begin serving HiSLIP.

        Blocks until interrupted (KeyboardInterrupt or task cancellation).
        """
        self._port = port
        self._async_port = async_port

        self._register_builtins()

        if advertise:
            from .discovery import MDNSAdvertiser
            advertiser = MDNSAdvertiser(self.manufacturer, self.model, port)
            await advertiser.start()

        from .hislip_server import HiSLIPServer
        server = HiSLIPServer(
            self.registry,
            self.backend,
            self.overlay_manager,
            port,
            async_port,
        )
        logger.info(
            "Instrument %s %s starting on port %d",
            self.manufacturer,
            self.model,
            port,
        )
        await server.start()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _register_builtins(self) -> None:
        from .scpi_standard import register_standard_commands
        from .scpi_system import register_system_commands

        register_standard_commands(
            self.registry,
            get_idn=self._idn_string,
            get_backend=lambda: self.backend,
        )
        register_system_commands(
            self.registry,
            get_identity=self._identity_dict,
        )

        if self.overlay_manager is not None:
            from .scpi_overlay import register_overlay_commands
            register_overlay_commands(self.registry, self.overlay_manager)

    def _idn_string(self) -> str:
        return f"{self.manufacturer},{self.model},{self.serial},{self.firmware_version}"

    def _identity_dict(self) -> dict:
        return {
            "manufacturer": self.manufacturer,
            "model": self.model,
            "serial": self.serial,
            "firmware": self.firmware_version,
        }

    def _ensure_overlay_manager(self) -> None:
        if self.overlay_manager is not None:
            return
        if isinstance(self.backend, MockBackend):
            from .overlay_manager import MockOverlayManager
            self.overlay_manager = MockOverlayManager()
        else:
            from .overlay_manager import OverlayManager
            from .hardware_backend import PYNQBackend
            om = OverlayManager()
            self.overlay_manager = om
            if isinstance(self.backend, PYNQBackend):
                self.backend._om = om
