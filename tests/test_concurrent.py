"""
Multi-client and overlap-mode stress tests.
"""

from __future__ import annotations

import asyncio

import pytest

from pynq_instrument.command_registry import CommandType
from pynq_instrument.response_helpers import respond_int


async def start_server(instrument) -> tuple:
    from pynq_instrument.hislip_server import HiSLIPServer

    instrument._register_builtins()
    server = HiSLIPServer(
        instrument.registry, instrument.backend, instrument.overlay_manager,
        port=0, async_port=0,
    )
    sync_srv = await asyncio.start_server(server._handle_sync, "127.0.0.1", 0)
    port = sync_srv.sockets[0].getsockname()[1]
    task = asyncio.ensure_future(sync_srv.serve_forever())
    return task, port, sync_srv


async def open_session(port: int, overlap: bool = False) -> tuple:
    from pynq_instrument.hislip import MSG_INITIALIZE, recv_message, send_message

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    cc = 0x01 if overlap else 0x00
    await send_message(writer, MSG_INITIALIZE, cc, (0x0100 << 16) | 1)
    await recv_message(reader)  # INITIALIZE_RESPONSE
    return reader, writer


async def scpi(reader, writer, cmd: str, msg_id: int = 1) -> str:
    from pynq_instrument.hislip import MSG_DATA_END, recv_message, send_message

    await send_message(writer, MSG_DATA_END, 0, msg_id, cmd.encode("ascii"))
    _, _, _, payload = await recv_message(reader)
    return payload.decode("ascii")


@pytest.mark.asyncio
async def test_two_sequential_clients(instrument):
    task, port, srv = await start_server(instrument)
    try:
        for _ in range(2):
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            from pynq_instrument.hislip import MSG_INITIALIZE, recv_message, send_message
            await send_message(writer, MSG_INITIALIZE, 0, (0x0100 << 16) | 1)
            await recv_message(reader)
            response = await scpi(reader, writer, "*IDN?")
            assert "TestCo" in response
            writer.close()
            await asyncio.sleep(0.05)
    finally:
        task.cancel()
        srv.close()


@pytest.mark.asyncio
async def test_overlap_mode_pipelining(instrument):
    """
    In overlap mode, the client sends N commands without waiting for responses,
    then collects all N responses.
    """
    counter = {"n": 0}

    @instrument.command("TICK?", type=CommandType.QUERY)
    async def tick():
        counter["n"] += 1
        await asyncio.sleep(0.005)  # simulate mild latency
        return respond_int(counter["n"])

    task, port, srv = await start_server(instrument)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        from pynq_instrument.hislip import MSG_DATA_END, MSG_INITIALIZE, recv_message, send_message

        await send_message(writer, MSG_INITIALIZE, 0x01, (0x0100 << 16) | 1)
        await recv_message(reader)  # INITIALIZE_RESPONSE

        N = 5
        for i in range(N):
            await send_message(writer, MSG_DATA_END, 0, i + 1, b"TICK?")

        responses = []
        for _ in range(N):
            _, _, _, payload = await asyncio.wait_for(recv_message(reader), timeout=3.0)
            responses.append(int(payload.decode("ascii")))

        assert sorted(responses) == list(range(1, N + 1))
        writer.close()
    finally:
        task.cancel()
        srv.close()


@pytest.mark.asyncio
async def test_sync_mode_ordering(instrument):
    """In sync mode, responses must arrive in the same order as commands."""
    values = []

    @instrument.command("VAL:PUSH", type=CommandType.WRITE)
    def push(v: int):
        values.append(v)
        return "OK"

    @instrument.command("VAL:LEN?", type=CommandType.QUERY)
    def val_len():
        return respond_int(len(values))

    task, port, srv = await start_server(instrument)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        from pynq_instrument.hislip import MSG_DATA_END, MSG_INITIALIZE, recv_message, send_message

        await send_message(writer, MSG_INITIALIZE, 0x00, (0x0100 << 16) | 1)
        await recv_message(reader)

        for i in range(5):
            resp = await scpi(reader, writer, f"VAL:PUSH {i}", i + 1)
            assert resp == "OK"

        length = await scpi(reader, writer, "VAL:LEN?", 10)
        assert length == "5"
        assert values == [0, 1, 2, 3, 4]
        writer.close()
    finally:
        task.cancel()
        srv.close()


@pytest.mark.asyncio
async def test_queue_overflow_response(instrument):
    """When overlap queue is full, server returns -350 queue overflow error."""
    slow_start = asyncio.Event()

    @instrument.command("SLOW?", type=CommandType.QUERY)
    async def slow():
        slow_start.set()
        await asyncio.sleep(1.0)  # block for a while
        return "done"

    task, port, srv = await start_server(instrument)
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        from pynq_instrument.hislip import MSG_DATA_END, MSG_INITIALIZE, recv_message, send_message

        await send_message(writer, MSG_INITIALIZE, 0x01, (0x0100 << 16) | 1)
        await recv_message(reader)

        # Fill queue beyond QUEUE_DEPTH (8) + currently-running command
        overflow_seen = False
        for i in range(20):
            await send_message(writer, MSG_DATA_END, 0, i + 1, b"SLOW?")

        # Collect responses and look for overflow
        for _ in range(20):
            try:
                _, _, _, payload = await asyncio.wait_for(recv_message(reader), timeout=0.5)
                resp = payload.decode("ascii")
                if "-350" in resp:
                    overflow_seen = True
                    break
            except asyncio.TimeoutError:
                break

        assert overflow_seen, "Expected -350 Queue overflow response"
        writer.close()
    finally:
        task.cancel()
        srv.close()
