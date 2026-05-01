from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional, Tuple

from .command_registry import CommandRegistry
from .errors import clear_errors, push_error
from .hardware_backend import HardwareBackend
from .hislip import (
    ERR_INVALID_INIT_SEQ,
    ERR_POORLY_FORMED,
    ERR_UNIDENTIFIED,
    MSG_DATA,
    MSG_DATA_END,
    MSG_DEVICE_CLEAR_ACK,
    MSG_DEVICE_CLEAR_COMPLETE,
    MSG_ERROR,
    MSG_FATAL_ERROR,
    MSG_INITIALIZE,
    MSG_INITIALIZE_RESPONSE,
    MSG_TRIGGER,
    recv_message,
    send_message,
)
from .param_parser import extract_args
from .response_helpers import respond_error
from .scpi_parser import normalize_scpi

logger = logging.getLogger(__name__)

QUEUE_DEPTH = 8
_QUEUE_OVERFLOW_RESP = b'-350,"Queue overflow"'


class HiSLIPSession:
    """
    Per-client state machine for the HiSLIP sync channel (port 4880).

    Phase 1: INITIALIZE handshake.
    Phase 2: DATA_END command loop with optional overlap queue.

    In **sync mode** (overlap_mode=False) the receive loop blocks after enqueuing each
    command until the dispatch coroutine has sent the response.

    In **overlap mode** (overlap_mode=True) the receive loop enqueues up to QUEUE_DEPTH
    commands without waiting; the dispatch coroutine processes them concurrently.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        registry: CommandRegistry,
        backend: HardwareBackend,
        session_id: int,
    ) -> None:
        self.reader = reader
        self.writer = writer
        self.registry = registry
        self.backend = backend
        self.session_id = session_id

        self.overlap_mode = False
        self.remote_mode = False

        # IEEE 488.2 status byte: bit 4 = MAV, bit 5 = ESB
        self.status_byte: int = 0

        self._send_lock = asyncio.Lock()
        self._cmd_queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_DEPTH)

        # Device-clear coordination
        self._device_clear_pending = False
        self._device_clear_done = asyncio.Event()

        # Sync-mode: reference to the in-flight done event so device_clear can unblock it
        self._current_sync_done: Optional[asyncio.Event] = None

        # Async channel state (set by HiSLIPServer when async channel connects)
        self.async_open: bool = False
        self.sync_open: bool = False

        # Weak reference to async writer for SRQ emission (set externally)
        self._async_writer: Optional[asyncio.StreamWriter] = None
        self._async_send_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run(self) -> None:
        self.sync_open = True
        try:
            if not await self._handshake():
                return
            dispatch_task = asyncio.ensure_future(self._dispatch_loop())
            try:
                await self._receive_loop()
            finally:
                # Send sentinel to stop dispatch loop
                try:
                    self._cmd_queue.put_nowait(None)
                except asyncio.QueueFull:
                    pass
                await dispatch_task
        finally:
            self.sync_open = False
            try:
                self.writer.close()
                await self.writer.wait_closed()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Phase 1: INITIALIZE handshake
    # ------------------------------------------------------------------

    async def _handshake(self) -> bool:
        try:
            msg_type, control_code, msg_param, payload = await recv_message(self.reader)
        except Exception as exc:
            logger.warning("Session %d: failed to read first message: %s", self.session_id, exc)
            return False

        if msg_type != MSG_INITIALIZE:
            logger.warning("Session %d: expected INITIALIZE (0), got %d", self.session_id, msg_type)
            await self._send_raw(MSG_FATAL_ERROR, ERR_INVALID_INIT_SEQ, 0)
            return False

        client_version = (msg_param >> 16) & 0xFFFF
        negotiated = client_version if 0 < client_version <= 0x0100 else 0x0100
        self.overlap_mode = bool(control_code & 0x01)

        if payload:
            logger.debug("Session %d: sub-address %r", self.session_id, payload.decode("ascii", errors="replace"))

        response_param = (negotiated << 16) | (self.session_id & 0xFFFF)
        await self._send_raw(
            MSG_INITIALIZE_RESPONSE,
            0x01 if self.overlap_mode else 0x00,
            response_param,
        )
        logger.info(
            "Session %d: initialized version=0x%04x mode=%s",
            self.session_id,
            negotiated,
            "overlap" if self.overlap_mode else "sync",
        )
        return True

    # ------------------------------------------------------------------
    # Phase 2: receive loop
    # ------------------------------------------------------------------

    async def _receive_loop(self) -> None:
        cmd_buf = bytearray()

        while True:
            try:
                msg_type, control_code, msg_param, payload = await recv_message(self.reader)
            except asyncio.IncompleteReadError:
                logger.info("Session %d: client disconnected", self.session_id)
                break
            except Exception as exc:
                logger.warning("Session %d: recv error: %s", self.session_id, exc)
                break

            if msg_type == MSG_DATA:
                cmd_buf.extend(payload)

            elif msg_type == MSG_DATA_END:
                cmd_buf.extend(payload)
                cmd_str = cmd_buf.decode("ascii", errors="replace").strip()
                cmd_buf.clear()

                if self.overlap_mode:
                    try:
                        self._cmd_queue.put_nowait((msg_param, cmd_str, None))
                    except asyncio.QueueFull:
                        await self._send_payload(msg_param, _QUEUE_OVERFLOW_RESP)
                else:
                    # Sync mode: enqueue and block until dispatch signals completion.
                    # Device-clear interrupts by setting this same event via start_device_clear().
                    done = asyncio.Event()
                    self._current_sync_done = done
                    try:
                        self._cmd_queue.put_nowait((msg_param, cmd_str, done))
                    except asyncio.QueueFull:
                        self._current_sync_done = None
                        await self._send_payload(msg_param, _QUEUE_OVERFLOW_RESP)
                        continue
                    await done.wait()
                    self._current_sync_done = None

            elif msg_type == MSG_TRIGGER:
                if self.registry.trigger_callback:
                    try:
                        self.registry.trigger_callback()
                    except Exception as exc:
                        logger.warning("Trigger callback error: %s", exc)
                await self._send_raw(MSG_DATA_END, 0, msg_param)

            elif msg_type == MSG_DEVICE_CLEAR_ACK:
                # Drain the queue
                while True:
                    try:
                        item = self._cmd_queue.get_nowait()
                        if item is not None:
                            _, _, done = item
                            if done is not None:
                                done.set()
                    except asyncio.QueueEmpty:
                        break
                cmd_buf.clear()
                self._device_clear_pending = False
                # Run *CLS inline
                await self._dispatch_inline("*CLS", 0)
                self._device_clear_done.set()
                logger.info("Session %d: device clear complete", self.session_id)

            else:
                logger.warning("Session %d: unexpected sync-channel message type %d", self.session_id, msg_type)
                await self._send_raw(MSG_ERROR, ERR_UNIDENTIFIED, 0)

    # ------------------------------------------------------------------
    # Phase 2: dispatch loop
    # ------------------------------------------------------------------

    async def _dispatch_loop(self) -> None:
        while True:
            item = await self._cmd_queue.get()
            if item is None:
                break

            msg_param, cmd_str, done_event = item

            if self._device_clear_pending:
                if done_event:
                    done_event.set()
                continue

            response = await self._dispatch_command(cmd_str)

            if response:
                payload = response.encode("ascii", errors="replace")
                await self._send_payload(msg_param, payload)
                self.status_byte |= 0x10  # MAV
                await self._emit_srq()

            if done_event:
                done_event.set()

    async def _dispatch_inline(self, cmd_str: str, msg_param: int) -> None:
        response = await self._dispatch_command(cmd_str)
        if response:
            await self._send_payload(msg_param, response.encode("ascii", errors="replace"))

    # ------------------------------------------------------------------
    # Command dispatcher
    # ------------------------------------------------------------------

    async def _dispatch_command(self, cmd_str: str) -> str:
        mnemonic, args = normalize_scpi(cmd_str)
        if not mnemonic:
            return ""

        descriptor = self.registry.lookup(mnemonic)
        if descriptor is None:
            push_error(-100, "Undefined header")
            return respond_error(-100, "Undefined command")

        # Overlay check
        if descriptor.requires_ips:
            if not self.backend.is_overlay_loaded():
                push_error(-200, "Hardware not ready")
                return respond_error(-200, "Hardware not ready")
            # Verify required IPs exist
            missing: List[str] = []
            for ip_name in descriptor.requires_ips:
                try:
                    self.backend.get_ip(ip_name)
                except (KeyError, RuntimeError):
                    missing.append(ip_name)
            if missing:
                push_error(-200, f"Hardware not ready: missing IPs {missing}")
                return respond_error(-200, f"Hardware not ready: missing IPs {missing}")

        # Inject IP objects + parse SCPI args
        try:
            injected = [self.backend.get_ip(name) for name in descriptor.requires_ips]
            parsed = extract_args(descriptor.handler, args, len(injected))
            all_args = injected + parsed
        except Exception as exc:
            push_error(-200, str(exc))
            return respond_error(-200, str(exc))

        # Call handler
        timeout = descriptor.timeout_ms / 1000.0 if descriptor.timeout_ms else None
        try:
            if asyncio.iscoroutinefunction(descriptor.handler):
                coro = descriptor.handler(*all_args)
                result = await asyncio.wait_for(coro, timeout=timeout)
            else:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None, lambda: descriptor.handler(*all_args)
                )
        except asyncio.TimeoutError:
            push_error(-200, "Command timeout")
            return respond_error(-200, "Command timeout")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            push_error(-300, str(exc))
            return respond_error(-300, str(exc))

        return result or ""

    # ------------------------------------------------------------------
    # Device clear (called by async channel)
    # ------------------------------------------------------------------

    async def start_device_clear(self) -> None:
        self._device_clear_pending = True
        self._device_clear_done.clear()
        # Unblock receive loop if it is blocked waiting on a sync-mode command
        if self._current_sync_done is not None:
            self._current_sync_done.set()

    async def send_device_clear_complete(self) -> None:
        await self._send_raw(MSG_DEVICE_CLEAR_COMPLETE, 0, 0)

    async def wait_device_clear_acked(self, timeout: float = 5.0) -> bool:
        try:
            await asyncio.wait_for(self._device_clear_done.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            self._device_clear_pending = False
            logger.warning("Session %d: device clear timed out", self.session_id)
            return False

    # ------------------------------------------------------------------
    # SRQ emission (async channel)
    # ------------------------------------------------------------------

    async def _emit_srq(self) -> None:
        if self._async_writer is None:
            return
        from .hislip import MSG_ASYNC_SERVICE_REQUEST
        from .scpi_standard import get_status_byte

        stb = get_status_byte(mav=bool(self.status_byte & 0x10))
        try:
            async with self._async_send_lock:
                await send_message(self._async_writer, MSG_ASYNC_SERVICE_REQUEST, stb, 0)
        except Exception as exc:
            logger.debug("SRQ emit failed: %s", exc)

    # ------------------------------------------------------------------
    # Internal send helpers
    # ------------------------------------------------------------------

    async def _send_raw(
        self, msg_type: int, control_code: int, msg_param: int, payload: bytes = b""
    ) -> None:
        async with self._send_lock:
            try:
                await send_message(self.writer, msg_type, control_code, msg_param, payload)
            except Exception as exc:
                logger.debug("Session %d: send error: %s", self.session_id, exc)

    async def _send_payload(self, msg_param: int, payload: bytes) -> None:
        await self._send_raw(MSG_DATA_END, 0, msg_param, payload)
