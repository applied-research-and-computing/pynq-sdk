from __future__ import annotations

import logging
from typing import Callable, List, Optional

from .command_registry import CommandDescriptor, CommandRegistry, CommandType
from .errors import clear_errors, push_error
from .response_helpers import respond_bool, respond_enum, respond_error, respond_int

logger = logging.getLogger(__name__)

# IEEE 488.2 status registers (module-level; one active session at a time)
_esr: int = 0   # Event Status Register
_ese: int = 0   # Event Status Enable mask
_sre: int = 0   # Service Request Enable mask

# ESR bit definitions
ESR_OPC = 0x01   # Operation Complete
ESR_QYE = 0x04   # Query Error
ESR_DDE = 0x08   # Device-Dependent Error
ESR_EAV = 0x10   # Error Available
ESR_PON = 0x80   # Power On

# Status byte bits
STB_MAV = 0x10   # Message Available
STB_ESB = 0x20   # Event Status Bit


def get_status_byte(mav: bool = False) -> int:
    esb = 1 if (_esr & _ese) else 0
    stb = (STB_MAV if mav else 0) | (STB_ESB if esb else 0)
    # RQS/MSS in bit 6 when SRE enables it
    if stb & _sre:
        stb |= 0x40
    return stb


def set_esr_bit(bit: int) -> None:
    global _esr
    _esr |= bit


def reset_registers() -> None:
    global _esr, _ese, _sre
    _esr = 0
    _ese = 0
    _sre = 0


def register_standard_commands(
    registry: CommandRegistry,
    get_idn: Callable[[], str],
    get_backend: Optional[Callable] = None,
) -> None:
    """Register IEEE 488.2 mandatory commands. Call after user commands."""
    global _esr, _ese, _sre

    def _idn() -> str:
        return get_idn()

    def _rst() -> str:
        return respond_enum("OK")

    def _cls() -> str:
        global _esr
        _esr = 0
        clear_errors()
        return respond_enum("OK")

    def _tst() -> str:
        if get_backend is None:
            return respond_int(0)
        backend = get_backend()
        # Check overlay-dependent commands
        for desc in registry.all_commands():
            if desc.requires_ips:
                if not backend.is_overlay_loaded():
                    push_error(-300, "Self-test: overlay not loaded")
                    set_esr_bit(ESR_DDE)
                    return respond_int(1)
                # Verify all IPs are present
                from .overlay_manager import OverlayManager
                om = getattr(backend, "_om", None)
                if isinstance(om, OverlayManager):
                    missing = om.missing_ips(desc.requires_ips)
                    if missing:
                        push_error(-300, f"Self-test: missing IPs {missing}")
                        set_esr_bit(ESR_DDE)
                        return respond_int(1)
        return respond_int(0)

    def _opc_query() -> str:
        return respond_int(1)

    def _opc() -> str:
        # Sets OPC bit in ESR; since all commands are sync, set immediately
        global _esr
        _esr |= ESR_OPC
        return ""

    def _wai() -> str:
        return respond_enum("OK")

    def _esr_query() -> str:
        global _esr
        val = _esr
        _esr = 0  # reading ESR clears it (IEEE 488.2)
        return respond_int(val)

    def _ese_query() -> str:
        return respond_int(_ese)

    def _ese_write(mask: int) -> str:
        global _ese
        _ese = mask & 0xFF
        return respond_enum("OK")

    def _sre_query() -> str:
        return respond_int(_sre)

    def _sre_write(mask: int) -> str:
        global _sre
        _sre = mask & 0xFF
        return respond_enum("OK")

    def _stb_query() -> str:
        # *STB? on sync channel: return current status byte (no MAV context)
        logger.warning("*STB? on sync channel; clients should use async channel (port 4881)")
        return respond_int(get_status_byte())

    builtin_cmds: List[CommandDescriptor] = [
        CommandDescriptor("*IDN?",  CommandType.QUERY, _idn,        group="IEEE488", description="Identify instrument",           timeout_ms=500),
        CommandDescriptor("*RST",   CommandType.WRITE, _rst,        group="IEEE488", description="Reset to defaults",             timeout_ms=1000),
        CommandDescriptor("*CLS",   CommandType.WRITE, _cls,        group="IEEE488", description="Clear status registers",        timeout_ms=500),
        CommandDescriptor("*TST?",  CommandType.QUERY, _tst,        group="IEEE488", description="Self-test query",               timeout_ms=2000),
        CommandDescriptor("*OPC?",  CommandType.QUERY, _opc_query,  group="IEEE488", description="Operation complete query",      timeout_ms=500),
        CommandDescriptor("*OPC",   CommandType.WRITE, _opc,        group="IEEE488", description="Set operation complete bit",    timeout_ms=500),
        CommandDescriptor("*WAI",   CommandType.WRITE, _wai,        group="IEEE488", description="Wait to continue",             timeout_ms=500),
        CommandDescriptor("*ESR?",  CommandType.QUERY, _esr_query,  group="IEEE488", description="Read event status register",   timeout_ms=500),
        CommandDescriptor("*ESE?",  CommandType.QUERY, _ese_query,  group="IEEE488", description="Read event status enable",     timeout_ms=500),
        CommandDescriptor("*ESE",   CommandType.WRITE, _ese_write,  group="IEEE488", description="Set event status enable mask", timeout_ms=500),
        CommandDescriptor("*SRE?",  CommandType.QUERY, _sre_query,  group="IEEE488", description="Read service request enable",  timeout_ms=500),
        CommandDescriptor("*SRE",   CommandType.WRITE, _sre_write,  group="IEEE488", description="Set service request enable",   timeout_ms=500),
        CommandDescriptor("*STB?",  CommandType.QUERY, _stb_query,  group="IEEE488", description="Read status byte",             timeout_ms=500),
    ]

    # Register after user commands so user can override builtins
    for desc in builtin_cmds:
        registry.register(desc)
