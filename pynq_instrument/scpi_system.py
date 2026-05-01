from __future__ import annotations

import json
import logging
from typing import Callable, List

from .command_registry import CommandDescriptor, CommandRegistry, CommandType
from .errors import error_count, pop_error
from .response_helpers import respond_int

logger = logging.getLogger(__name__)


def register_system_commands(
    registry: CommandRegistry,
    get_identity: Callable[[], dict],
) -> None:
    """Register SYST:ERR?, SYST:ERR:COUN?, and SYSTEM:COMMANDS?."""

    def _syst_err() -> str:
        return pop_error()

    def _syst_err_count() -> str:
        return respond_int(error_count())

    def _commands() -> str:
        identity = get_identity()
        commands: List[dict] = []

        for desc in registry.all_commands():
            # Exclude SYSTEM: commands from the listing (mirrors ESP32 behavior)
            if desc.scpi_command.startswith("SYSTEM:"):
                continue
            entry: dict = {
                "scpi": desc.scpi_command,
                "type": desc.type.value,
                "timeout_ms": desc.timeout_ms,
                "params": [],
            }
            if desc.group:
                entry["group"] = desc.group
            if desc.description:
                entry["description"] = desc.description

            for p in desc.params:
                param_entry: dict = {
                    "name": p.name,
                    "type": p.type,
                }
                if p.description:
                    param_entry["description"] = p.description
                if p.type in ("int", "float") and p.max > p.min:
                    param_entry["min"] = p.min
                    param_entry["max"] = p.max
                if p.type == "enum" and p.enum_values:
                    param_entry["values"] = p.enum_values
                if p.default_value is not None:
                    param_entry["default"] = p.default_value
                entry["params"].append(param_entry)

            commands.append(entry)

        payload = {"identity": identity, "commands": commands}
        return json.dumps(payload, separators=(",", ":"))

    system_cmds: List[CommandDescriptor] = [
        CommandDescriptor(
            "SYST:ERR?",
            CommandType.QUERY,
            _syst_err,
            group="System",
            description="Pop oldest error from queue",
            timeout_ms=500,
        ),
        CommandDescriptor(
            "SYST:ERR:COUN?",
            CommandType.QUERY,
            _syst_err_count,
            group="System",
            description="Return error queue depth",
            timeout_ms=500,
        ),
        CommandDescriptor(
            "SYSTEM:COMMANDS?",
            CommandType.QUERY,
            _commands,
            group="System",
            description="Return all registered commands and device identity as JSON",
            timeout_ms=2000,
        ),
    ]

    for desc in system_cmds:
        registry.register(desc)
