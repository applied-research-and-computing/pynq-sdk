from __future__ import annotations

import asyncio
import logging
from typing import List

from .command_registry import CommandDescriptor, CommandRegistry, CommandType
from .errors import push_error
from .response_helpers import respond_enum, respond_error

logger = logging.getLogger(__name__)


def register_overlay_commands(registry: CommandRegistry, overlay_manager: Any) -> None:
    """Register OVERLAY:* remote overlay-management commands."""

    async def _overlay_load(bitfile: str) -> str:
        bitfile = bitfile.strip().strip('"').strip("'")
        try:
            await overlay_manager.load_async(bitfile)
            return respond_enum("OK")
        except Exception as exc:
            push_error(-300, str(exc))
            return respond_error(-300, str(exc))

    def _overlay_status() -> str:
        return overlay_manager.status()

    def _overlay_version() -> str:
        return overlay_manager.version() or "UNKNOWN"

    async def _overlay_unload() -> str:
        try:
            # run_in_executor for any blocking cleanup
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, overlay_manager.unload)
            return respond_enum("OK")
        except Exception as exc:
            push_error(-300, str(exc))
            return respond_error(-300, str(exc))

    overlay_cmds: List[CommandDescriptor] = [
        CommandDescriptor(
            "OVERLAY:LOAD",
            CommandType.WRITE,
            _overlay_load,
            group="Overlay",
            description='Load bitfile: OVERLAY:LOAD "filename.bit"',
            timeout_ms=30000,
        ),
        CommandDescriptor(
            "OVERLAY:STATUS?",
            CommandType.QUERY,
            _overlay_status,
            group="Overlay",
            description="Return LOADED:<name> or NONE",
            timeout_ms=500,
        ),
        CommandDescriptor(
            "OVERLAY:VERSION?",
            CommandType.QUERY,
            _overlay_version,
            group="Overlay",
            description="Return overlay version string from HWH metadata",
            timeout_ms=500,
        ),
        CommandDescriptor(
            "OVERLAY:UNLOAD",
            CommandType.WRITE,
            _overlay_unload,
            group="Overlay",
            description="Gracefully unload current overlay",
            timeout_ms=5000,
        ),
    ]

    for desc in overlay_cmds:
        registry.register(desc)


# Resolve forward reference used in function signature
from typing import Any  # noqa: E402 — keep at bottom to avoid circular imports
register_overlay_commands.__annotations__["overlay_manager"] = Any
