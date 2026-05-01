from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional


class CommandType(Enum):
    WRITE = "write"
    QUERY = "query"


@dataclass
class ParamDescriptor:
    name: str = ""
    type: str = "string"  # "int" | "float" | "string" | "bool" | "enum"
    description: str = ""
    min: float = 0.0
    max: float = 0.0
    enum_values: List[str] = field(default_factory=list)
    default_value: Optional[str] = None


@dataclass
class CommandDescriptor:
    scpi_command: str
    type: CommandType
    handler: Callable
    params: List[ParamDescriptor] = field(default_factory=list)
    requires_ips: List[str] = field(default_factory=list)
    requires_overlay: Optional[str] = None
    timeout_ms: int = 5000
    group: str = ""
    description: str = ""


class CommandRegistry:
    def __init__(self) -> None:
        self._commands: Dict[str, CommandDescriptor] = {}
        self._ordered: List[CommandDescriptor] = []
        self.trigger_callback: Optional[Callable[[], None]] = None

    def register(self, desc: CommandDescriptor) -> None:
        """Register a command. If the mnemonic already exists, it is overwritten."""
        if desc.scpi_command not in self._commands:
            self._ordered.append(desc)
        else:
            # Replace in ordered list so JSON output stays consistent
            self._ordered = [
                desc if d.scpi_command == desc.scpi_command else d
                for d in self._ordered
            ]
        self._commands[desc.scpi_command] = desc

    def lookup(self, mnemonic: str) -> Optional[CommandDescriptor]:
        return self._commands.get(mnemonic)

    def all_commands(self) -> List[CommandDescriptor]:
        return list(self._ordered)

    def set_trigger_callback(self, callback: Optional[Callable[[], None]]) -> None:
        self.trigger_callback = callback
