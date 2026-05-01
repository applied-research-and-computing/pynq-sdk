from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from .async_channel import handle_async_connection
from .command_registry import CommandRegistry
from .hardware_backend import HardwareBackend
from .hislip_session import HiSLIPSession

logger = logging.getLogger(__name__)


class HiSLIPServer:
    """
    asyncio TCP server for the HiSLIP protocol.

    Listens on two ports:
    - ``port``       (default 4880): sync channel — one HiSLIPSession per client
    - ``async_port`` (default 4881): async channel — one AsyncChannel per session
    """

    def __init__(
        self,
        registry: CommandRegistry,
        backend: HardwareBackend,
        overlay_manager: Optional[Any],
        port: int = 4880,
        async_port: int = 4881,
    ) -> None:
        self.registry = registry
        self.backend = backend
        self.overlay_manager = overlay_manager
        self.port = port
        self.async_port = async_port

        self._sessions: Dict[int, HiSLIPSession] = {}
        self._session_lock = asyncio.Lock()
        self._next_session_id: int = 1

    async def start(self) -> None:
        """Start both servers and block until cancelled."""
        sync_server = await asyncio.start_server(
            self._handle_sync,
            host="0.0.0.0",
            port=self.port,
        )
        async_server = await asyncio.start_server(
            self._handle_async,
            host="0.0.0.0",
            port=self.async_port,
        )

        addrs = [s.sockets[0].getsockname() for s in (sync_server, async_server) if s.sockets]
        logger.info("HiSLIP sync  listening on %s", addrs[0] if addrs else self.port)
        logger.info("HiSLIP async listening on %s", addrs[1] if len(addrs) > 1 else self.async_port)

        async with sync_server, async_server:
            await asyncio.gather(
                sync_server.serve_forever(),
                async_server.serve_forever(),
            )

    # ------------------------------------------------------------------
    # Connection handlers
    # ------------------------------------------------------------------

    async def _handle_sync(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername", ("?", 0))
        logger.info("Sync channel: connection from %s:%d", peer[0], peer[1])

        async with self._session_lock:
            session_id = self._next_session_id
            self._next_session_id = (session_id % 0xFFFF) + 1

        session = HiSLIPSession(
            reader,
            writer,
            self.registry,
            self.backend,
            session_id,
        )

        async with self._session_lock:
            self._sessions[session_id] = session

        try:
            await session.run()
        finally:
            async with self._session_lock:
                self._sessions.pop(session_id, None)
            logger.info("Sync channel: session %d closed", session_id)

    async def _handle_async(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername", ("?", 0))
        logger.debug("Async channel: connection from %s:%d", peer[0], peer[1])
        # Pass a snapshot of sessions — the async channel only reads from it
        await handle_async_connection(reader, writer, self._sessions)
