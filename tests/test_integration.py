"""
End-to-end integration tests: full SCPI command → HiSLIP → response using
MockBackend and an in-process server. No hardware, no real network.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from pynq_instrument.command_registry import CommandType
from pynq_instrument.instrument import Instrument
from pynq_instrument.response_helpers import respond_enum, respond_float, respond_int


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def run_scpi(instrument: Instrument, *commands: str) -> list:
    """
    Start the instrument in-process, run SCPI commands, return responses.
    The server is started on a random port and torn down after the commands.
    """
    from pynq_instrument.hislip import MSG_DATA_END, recv_message, send_message
    from pynq_instrument.hislip_server import HiSLIPServer

    instrument._register_builtins()
    server = HiSLIPServer(
        instrument.registry, instrument.backend, instrument.overlay_manager,
        port=0, async_port=0,
    )
    sync_srv = await asyncio.start_server(server._handle_sync, "127.0.0.1", 0)
    port = sync_srv.sockets[0].getsockname()[1]

    async def client():
        from pynq_instrument.hislip import MSG_INITIALIZE, MSG_INITIALIZE_RESPONSE, send_message, recv_message
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        await send_message(writer, MSG_INITIALIZE, 0, (0x0100 << 16) | 1)
        await recv_message(reader)  # INITIALIZE_RESPONSE
        responses = []
        for cmd in commands:
            await send_message(writer, MSG_DATA_END, 0, 1, cmd.encode("ascii"))
            _, _, _, payload = await recv_message(reader)
            responses.append(payload.decode("ascii"))
        writer.close()
        return responses

    try:
        task = asyncio.ensure_future(sync_srv.serve_forever())
        responses = await asyncio.wait_for(client(), timeout=5.0)
        return responses
    finally:
        task.cancel()
        sync_srv.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_idn(instrument):
    responses = await run_scpi(instrument, "*IDN?")
    parts = responses[0].split(",")
    assert parts == ["TestCo", "TestInst", "SN-0001", "0.1.0"]


@pytest.mark.asyncio
async def test_rst_and_cls(instrument):
    responses = await run_scpi(instrument, "*RST", "*CLS")
    assert responses[0] == "OK"
    assert responses[1] == "OK"


@pytest.mark.asyncio
async def test_opc_query(instrument):
    responses = await run_scpi(instrument, "*OPC?")
    assert responses[0] == "1"


@pytest.mark.asyncio
async def test_tst_no_hardware_deps(instrument):
    responses = await run_scpi(instrument, "*TST?")
    assert responses[0] == "0"  # no hardware-dependent commands registered


@pytest.mark.asyncio
async def test_user_command(instrument):
    @instrument.command("TEMP:READ?", type=CommandType.QUERY, group="Sensor")
    async def read_temp():
        return respond_float(25.5)

    responses = await run_scpi(instrument, "TEMP:READ?")
    assert responses[0] == "25.5"


@pytest.mark.asyncio
async def test_user_command_with_args(instrument):
    @instrument.command("GPIO:SET", type=CommandType.WRITE)
    def gpio_set(pin: int, state: int):
        return respond_enum("OK")

    responses = await run_scpi(instrument, "GPIO:SET 0 1")
    assert responses[0] == "OK"


@pytest.mark.asyncio
async def test_command_requires_overlay_fails_without_it(instrument):
    @instrument.command("ADC:READ?", type=CommandType.QUERY, requires=["adc_0"])
    async def adc_read(adc_0):
        return respond_float(3.3)

    responses = await run_scpi(instrument, "ADC:READ?")
    assert responses[0].startswith("ERR:"), f"Expected error, got: {responses[0]!r}"


@pytest.mark.asyncio
async def test_command_requires_overlay_succeeds_with_it(instrument_with_overlay):
    @instrument_with_overlay.command("ADC:READ?", type=CommandType.QUERY, requires=["adc_0"])
    async def adc_read(adc_0):
        return respond_float(1.65)

    responses = await run_scpi(instrument_with_overlay, "ADC:READ?")
    assert responses[0] == "1.65"


@pytest.mark.asyncio
async def test_system_commands_json(instrument):
    @instrument.command("FOO:BAR?", type=CommandType.QUERY, group="Test", description="A test cmd")
    async def foo_bar():
        return respond_int(42)

    responses = await run_scpi(instrument, "SYSTEM:COMMANDS?")
    data = json.loads(responses[0])
    assert "identity" in data
    assert "commands" in data
    assert data["identity"]["manufacturer"] == "TestCo"
    # SYSTEM:COMMANDS? itself should be excluded
    scpi_names = [c["scpi"] for c in data["commands"]]
    assert "SYSTEM:COMMANDS?" not in scpi_names
    assert "FOO:BAR?" in scpi_names


@pytest.mark.asyncio
async def test_syst_err_populated_on_unknown_cmd(instrument):
    responses = await run_scpi(instrument, "XYZZY:UNKNOWN?", "SYST:ERR?")
    assert responses[0].startswith("ERR:")
    err_response = responses[1]
    # Queue should have one error from the unknown command
    assert not err_response.startswith('0,'), f"Expected error in queue, got: {err_response!r}"


@pytest.mark.asyncio
async def test_esr_cleared_by_cls(instrument):
    responses = await run_scpi(instrument, "*ESR?", "*CLS", "*ESR?")
    # After CLS, ESR reads as 0
    assert responses[2] == "0"


@pytest.mark.asyncio
async def test_multi_command_sequence(instrument):
    @instrument.command("COUNTER:INCR", type=CommandType.WRITE)
    def incr():
        incr.count += 1
        return respond_enum("OK")

    @instrument.command("COUNTER:VALUE?", type=CommandType.QUERY)
    def get_count():
        return respond_int(incr.count)

    incr.count = 0

    responses = await run_scpi(
        instrument,
        "COUNTER:INCR",
        "COUNTER:INCR",
        "COUNTER:INCR",
        "COUNTER:VALUE?",
    )
    assert responses[-1] == "3"
