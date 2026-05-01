"""Shared fixtures for the pynq_instrument test suite.

All tests use MockBackend so no PYNQ hardware is needed on x86.
Hardware tests live in tests/hardware/ and require --pynq.
"""

from __future__ import annotations

import asyncio
from typing import List

import pytest

from pynq_instrument.command_registry import CommandRegistry
from pynq_instrument.hardware_backend import MockBackend
from pynq_instrument.instrument import Instrument


def pytest_addoption(parser):
    parser.addoption(
        "--pynq",
        action="store_true",
        default=False,
        help="Run hardware tests that require a real PYNQ board",
    )


def pytest_collection_modifyitems(config, items):
    if not config.getoption("--pynq"):
        skip_hw = pytest.mark.skip(reason="requires --pynq flag and a real PYNQ board")
        for item in items:
            if "hardware" in str(item.fspath):
                item.add_marker(skip_hw)


@pytest.fixture(autouse=True)
def reset_error_queue():
    """Clear the module-level SCPI error queue and status registers before each test."""
    from pynq_instrument.errors import clear_errors
    from pynq_instrument.scpi_standard import reset_registers
    clear_errors()
    reset_registers()
    yield
    clear_errors()


@pytest.fixture
def mock_backend() -> MockBackend:
    return MockBackend()


@pytest.fixture
def mock_backend_with_overlay() -> MockBackend:
    backend = MockBackend()
    backend.load_mock_overlay(["adc_0", "dma_0", "gpio_0"])
    return backend


@pytest.fixture
def registry() -> CommandRegistry:
    return CommandRegistry()


@pytest.fixture
def instrument() -> Instrument:
    """Minimal instrument with MockBackend — no hardware required."""
    return Instrument("TestCo", "TestInst", "SN-0001", "0.1.0")


@pytest.fixture
def instrument_with_overlay(mock_backend_with_overlay) -> Instrument:
    inst = Instrument("TestCo", "TestInst", "SN-0001", "0.1.0", backend=mock_backend_with_overlay)
    return inst


# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------

async def hislip_client(port: int = 4880):
    """Open a raw asyncio TCP connection to the local HiSLIP server."""
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    return reader, writer
