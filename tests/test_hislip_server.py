"""
Tests for HiSLIP protocol framing, INITIALIZE handshake, and basic command dispatch.
Uses an in-process asyncio server with MockBackend — no hardware required.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from pynq_instrument.hislip import (
    MSG_DATA_END,
    MSG_INITIALIZE,
    MSG_INITIALIZE_RESPONSE,
    pack_header,
    recv_message,
    send_message,
    unpack_header,
)


# ---------------------------------------------------------------------------
# Header pack/unpack
# ---------------------------------------------------------------------------

class TestHeaderCodec:
    def test_round_trip(self):
        raw = pack_header(7, 0, 0x00010001, 5)
        msg_type, cc, param, plen = unpack_header(raw)
        assert msg_type == 7
        assert cc == 0
        assert param == 0x00010001
        assert plen == 5

    def test_invalid_prologue(self):
        bad = b"XX" + b"\x00" * 14
        with pytest.raises(ValueError, match="prologue"):
            unpack_header(bad)

    def test_zero_payload(self):
        raw = pack_header(0, 0, 0, 0)
        _, _, _, plen = unpack_header(raw)
        assert plen == 0

    def test_large_payload_length(self):
        raw = pack_header(7, 0, 0, 8192)
        _, _, _, plen = unpack_header(raw)
        assert plen == 8192


# ---------------------------------------------------------------------------
# Server integration helpers
# ---------------------------------------------------------------------------

async def start_test_server(instrument) -> tuple:
    """Start instrument server on a random port and return (server_task, port)."""
    from pynq_instrument.hislip_server import HiSLIPServer

    instrument._register_builtins()
    server = HiSLIPServer(
        instrument.registry,
        instrument.backend,
        instrument.overlay_manager,
        port=0,       # OS assigns port
        async_port=0,
    )

    # Use asyncio.start_server directly to get the port
    sync_srv = await asyncio.start_server(
        server._handle_sync, host="127.0.0.1", port=0
    )
    port = sync_srv.sockets[0].getsockname()[1]
    task = asyncio.ensure_future(sync_srv.serve_forever())
    return task, port, sync_srv


async def hislip_handshake(port: int, overlap: bool = False) -> tuple:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    # Send INITIALIZE
    payload = b""  # no sub-address
    cc = 0x01 if overlap else 0x00
    msg_param = (0x0100 << 16) | 1  # version 1.0, session hint 1
    await send_message(writer, MSG_INITIALIZE, cc, msg_param, payload)
    # Receive INITIALIZE_RESPONSE
    msg_type, resp_cc, resp_param, resp_payload = await recv_message(reader)
    assert msg_type == MSG_INITIALIZE_RESPONSE
    session_id = resp_param & 0xFFFF
    return reader, writer, session_id


async def scpi_transaction(reader, writer, cmd: str) -> str:
    payload = cmd.encode("ascii")
    await send_message(writer, MSG_DATA_END, 0, 1, payload)
    msg_type, cc, param, response = await recv_message(reader)
    assert msg_type == MSG_DATA_END
    return response.decode("ascii")


# ---------------------------------------------------------------------------
# INITIALIZE handshake
# ---------------------------------------------------------------------------

class TestInitializeHandshake:
    @pytest.mark.asyncio
    async def test_sync_mode(self, instrument):
        task, port, srv = await start_test_server(instrument)
        try:
            reader, writer, session_id = await hislip_handshake(port, overlap=False)
            assert session_id >= 1
            writer.close()
        finally:
            task.cancel()
            srv.close()

    @pytest.mark.asyncio
    async def test_overlap_mode(self, instrument):
        task, port, srv = await start_test_server(instrument)
        try:
            reader, writer, session_id = await asyncio.wait_for(
                hislip_handshake(port, overlap=True), timeout=2.0
            )
            assert session_id >= 1
            writer.close()
        finally:
            task.cancel()
            srv.close()

    @pytest.mark.asyncio
    async def test_idn_after_handshake(self, instrument):
        task, port, srv = await start_test_server(instrument)
        try:
            reader, writer, _ = await asyncio.wait_for(
                hislip_handshake(port), timeout=2.0
            )
            response = await asyncio.wait_for(
                scpi_transaction(reader, writer, "*IDN?"), timeout=2.0
            )
            assert "TestCo" in response
            assert "TestInst" in response
            writer.close()
        finally:
            task.cancel()
            srv.close()


# ---------------------------------------------------------------------------
# Basic SCPI commands
# ---------------------------------------------------------------------------

class TestScpiDispatch:
    @pytest.mark.asyncio
    async def test_idn(self, instrument):
        task, port, srv = await start_test_server(instrument)
        try:
            reader, writer, _ = await asyncio.wait_for(hislip_handshake(port), timeout=2.0)
            response = await asyncio.wait_for(
                scpi_transaction(reader, writer, "*IDN?"), timeout=2.0
            )
            parts = response.split(",")
            assert len(parts) == 4
            writer.close()
        finally:
            task.cancel()
            srv.close()

    @pytest.mark.asyncio
    async def test_rst(self, instrument):
        task, port, srv = await start_test_server(instrument)
        try:
            reader, writer, _ = await asyncio.wait_for(hislip_handshake(port), timeout=2.0)
            response = await asyncio.wait_for(
                scpi_transaction(reader, writer, "*RST"), timeout=2.0
            )
            assert response == "OK"
            writer.close()
        finally:
            task.cancel()
            srv.close()

    @pytest.mark.asyncio
    async def test_unknown_command(self, instrument):
        task, port, srv = await start_test_server(instrument)
        try:
            reader, writer, _ = await asyncio.wait_for(hislip_handshake(port), timeout=2.0)
            response = await asyncio.wait_for(
                scpi_transaction(reader, writer, "FAKE:CMD?"), timeout=2.0
            )
            assert response.startswith("ERR:")
            writer.close()
        finally:
            task.cancel()
            srv.close()

    @pytest.mark.asyncio
    async def test_syst_err_empty(self, instrument):
        task, port, srv = await start_test_server(instrument)
        try:
            reader, writer, _ = await asyncio.wait_for(hislip_handshake(port), timeout=2.0)
            response = await asyncio.wait_for(
                scpi_transaction(reader, writer, "SYST:ERR?"), timeout=2.0
            )
            assert response.startswith('0,')
            writer.close()
        finally:
            task.cancel()
            srv.close()


# ---------------------------------------------------------------------------
# Fragmented DATA + DATA_END
# ---------------------------------------------------------------------------

class TestFragmentation:
    @pytest.mark.asyncio
    async def test_data_then_data_end(self, instrument):
        from pynq_instrument.hislip import MSG_DATA

        task, port, srv = await start_test_server(instrument)
        try:
            reader, writer, _ = await asyncio.wait_for(hislip_handshake(port), timeout=2.0)
            # Send "*ID" as DATA, then "N?" as DATA_END
            await send_message(writer, MSG_DATA, 0, 1, b"*ID")
            await send_message(writer, MSG_DATA_END, 0, 1, b"N?")
            msg_type, _, _, response = await asyncio.wait_for(recv_message(reader), timeout=2.0)
            assert msg_type == MSG_DATA_END
            assert "TestCo" in response.decode("ascii")
            writer.close()
        finally:
            task.cancel()
            srv.close()
