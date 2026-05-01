"""Carbon PYNQ Instrument SDK — public API."""

from .command_registry import CommandDescriptor, CommandRegistry, CommandType, ParamDescriptor
from .hardware_backend import HardwareBackend, MockBackend, PYNQBackend
from .instrument import Instrument
from .response_helpers import (
    respond_bool,
    respond_enum,
    respond_error,
    respond_float,
    respond_float_array,
    respond_int,
)

__version__ = "0.1.0"
__all__ = [
    "Instrument",
    "CommandType",
    "CommandDescriptor",
    "CommandRegistry",
    "ParamDescriptor",
    "HardwareBackend",
    "PYNQBackend",
    "MockBackend",
    "respond_float",
    "respond_int",
    "respond_bool",
    "respond_enum",
    "respond_float_array",
    "respond_error",
]
