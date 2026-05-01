from __future__ import annotations

import asyncio
import logging
import struct
from typing import Optional

from .hislip import (
    ERR_ATTEMPT_TO_USE_CONN,
    ERR_UNIDENTIFIED,
    MAX_PAYLOAD,
    MSG_ASYNC_DEV_CLEAR_ACK,
    MSG_ASYNC_DEVICE_CLEAR,
    MSG_ASYNC_INITIALIZE,
    MSG_ASYNC_INITIALIZE_RESP,
    MSG_ASYNC_LOCK,
    MSG_ASYNC_LOCK_RESPONSE,
    MSG_ASYNC_MAX_MSG_SIZE,
    MSG_ASYNC_MAX_MSG_SIZE_RESP,
    MSG_ASYNC_REMOTE_LOCAL_CTRL,
    MSG_ASYNC_REMOTE_LOCAL_RESP,
    MSG_ASYNC_STATUS_QUERY,
    MSG_ASYNC_STATUS_RESPONSE,
    MSG_ERROR,
    recv_message,
    send_message,
)

logger = logging.getLogger(__name__)

# Vendor-defined identification token used in ASYNC_INITIALIZE_RESP msg_param
_VENDOR_TOKEN = 0x00004342  # "CB\x00\x00" — Carbon Board


class AsyncChannel:
    """
    Handles the HiSLIP 2.0 async channel (port 4881) for one session.

    The async channel carries:
    - ASYNC_INITIALIZE / ASYNC_INITIALIZE_RESP  (session correlation)
    - ASYNC_MAX_MSG_SIZE negotiation
    - ASYNC_LOCK (always granted)
    - ASYNC_STATUS_QUERY / ASYNC_STATUS_RESPONSE  (*STB? without blocking sync channel)
    - ASYNC_DEVICE_CLEAR  (trigger device clear; coordinate with sync channel)
    - ASYNC_REMOTE_LOCAL_CTRL  (remote/local mode flag)
    - ASYNC_SERVICE_REQUEST emissions are driven by the session dispatch loop.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        session: Any,  # HiSLIPSession — avoid circular import
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.session = session
        self._send_lock = asyncio.Lock()

    async def run(self) -> None:
        # Install our writer on the session so it can emit SRQs
        self.session._async_writer = self.writer
        self.session._async_send_lock = self._send_lock
        self.session.async_open = True
        try:
            await self._loop()
        finally:
            self.session.async_open = False
            self.session._async_writer = None
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass
            logger.info("Async channel closed for session %d", self.session.session_id)

    async def _loop(self) -> None:
        while True:
            try:
                msg_type, control_code, msg_param, payload = await recv_message(self.reader)
            except asyncio.IncompleteReadError:
                logger.info(
                    "Async channel session %d: client disconnected",
                    self.session.session_id,
                )
                break
            except Exception as exc:
                logger.warning(
                    "Async channel session %d: recv error: %s",
                    self.session.session_id,
                    exc,
                )
                break

            await self._handle(msg_type, control_code, msg_param, payload)

    async def _handle(
        self, msg_type: int, control_code: int, msg_param: int, payload: bytes
    ) -> None:
        if msg_type == MSG_ASYNC_MAX_MSG_SIZE:
            proposed = (
                struct.unpack(">Q", payload[:8])[0] if len(payload) >= 8 else MAX_PAYLOAD
            )
            negotiated = min(proposed, MAX_PAYLOAD)
            await self._send(MSG_ASYNC_MAX_MSG_SIZE_RESP, 0, 0, struct.pack(">Q", negotiated))

        elif msg_type == MSG_ASYNC_LOCK:
            # Always grant the lock
            await self._send(MSG_ASYNC_LOCK_RESPONSE, 1, 0)

        elif msg_type == MSG_ASYNC_STATUS_QUERY:
            stb = self.session.status_byte
            if control_code & 0x01:
                # Client requests MAV clear
                self.session.status_byte &= ~0x10
            await self._send(MSG_ASYNC_STATUS_RESPONSE, stb, 0)

        elif msg_type == MSG_ASYNC_DEVICE_CLEAR:
            await self._handle_device_clear()

        elif msg_type == MSG_ASYNC_REMOTE_LOCAL_CTRL:
            self.session.remote_mode = control_code > 0
            logger.info(
                "Session %d: remote mode %s",
                self.session.session_id,
                "enabled" if self.session.remote_mode else "disabled",
            )
            await self._send(MSG_ASYNC_REMOTE_LOCAL_RESP, control_code, 0)

        else:
            sync_only = msg_type in (6, 7, 8, 9, 12, 13)  # DATA/DATA_END/CLEAR/TRIGGER/INTERRUPTED
            if sync_only:
                logger.warning(
                    "Async channel: sync-only message type %d received", msg_type
                )
                await self._send(MSG_ERROR, ERR_ATTEMPT_TO_USE_CONN, 0)
            else:
                logger.warning("Async channel: unhandled message type %d", msg_type)
                await self._send(MSG_ERROR, ERR_UNIDENTIFIED, 0)

    async def _handle_device_clear(self) -> None:
        logger.info("Session %d: device clear requested", self.session.session_id)
        await self.session.start_device_clear()

        if self.session.sync_open:
            await self.session.send_device_clear_complete()
            success = await self.session.wait_device_clear_acked(timeout=5.0)
            if not success:
                logger.warning(
                    "Session %d: device clear ACK timed out", self.session.session_id
                )

        await self._send(MSG_ASYNC_DEV_CLEAR_ACK, 0, 0)

    async def _send(
        self, msg_type: int, control_code: int, msg_param: int, payload: bytes = b""
    ) -> None:
        async with self._send_lock:
            try:
                await send_message(self.writer, msg_type, control_code, msg_param, payload)
            except Exception as exc:
                logger.debug("Async channel send error: %s", exc)


async def handle_async_connection(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    sessions: dict,
) -> None:
    """
    Entry point for a new connection on the async channel port.

    Reads the ASYNC_INITIALIZE message, correlates with an active sync session,
    and runs the AsyncChannel loop.
    """
    peer = writer.get_extra_info("peername", ("?", 0))
    logger.debug("Async channel: connection from %s:%d", peer[0], peer[1])

    try:
        msg_type, control_code, msg_param, payload = await asyncio.wait_for(
            recv_message(reader), timeout=10.0
        )
    except Exception as exc:
        logger.warning("Async channel: failed to read ASYNC_INITIALIZE: %s", exc)
        writer.close()
        return

    if msg_type != MSG_ASYNC_INITIALIZE:
        logger.warning(
            "Async channel: expected ASYNC_INITIALIZE (17), got %d", msg_type
        )
        try:
            from .hislip import ERR_INVALID_INIT_SEQ
            await send_message(writer, MSG_ERROR, ERR_INVALID_INIT_SEQ, 0)
        except Exception:
            pass
        writer.close()
        return

    session_id = msg_param & 0xFFFF
    session = sessions.get(session_id)

    if session is None or not session.sync_open or session.async_open:
        logger.warning(
            "Async channel: invalid session %d (exists=%s, sync_open=%s, async_open=%s)",
            session_id,
            session is not None,
            session.sync_open if session else False,
            session.async_open if session else False,
        )
        try:
            from .hislip import ERR_INVALID_INIT_SEQ
            await send_message(writer, MSG_ERROR, ERR_INVALID_INIT_SEQ, 0)
        except Exception:
            pass
        writer.close()
        return

    # Acknowledge with vendor token
    await send_message(writer, MSG_ASYNC_INITIALIZE_RESP, 0, _VENDOR_TOKEN)

    channel = AsyncChannel(reader, writer, session)
    await channel.run()


# Resolve forward reference
from typing import Any  # noqa: E402
