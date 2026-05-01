# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Carbon PYNQ Instrument SDK** — a Python framework that turns a PYNQ-Z2 board (Xilinx Zynq-7020: dual ARM Cortex-A9 + FPGA fabric running PYNQ Linux) into a network-connected test instrument controllable via HiSLIP over TCP and SCPI commands. This is the Python/FPGA sibling of `../esp32_sdk`.

**Design mantra:** "FastAPI for FPGA instruments" — decorator-based command registration, asyncio-native network layer, explicit hardware lifecycle management.

The companion ESP32 SDK at `../esp32_sdk` is the canonical reference for protocol semantics: HiSLIP framing, SCPI command structure, profile YAML format, mDNS service type (`_hislip._tcp`), and response string format. Both SDKs must produce instruments that are interchangeable from the client's (Carbon daemon's) perspective.

## Critical Zynq-7020 / PYNQ Platform Constraints

These are non-negotiable architectural requirements that differ fundamentally from the ESP32. Every design decision must respect them.

### 1. Overlay Lifecycle is Mandatory

PYNQ requires explicit bitfile loading before any FPGA IP is accessible. There is no "always-on" FPGA fabric from the Linux side.

```python
overlay = pynq.Overlay("design.bit")  # loads .bit + .hwh (Hardware Handoff)
# Only now can you access: overlay.adc_0, overlay.gpio_0, etc.
# IP names come from the .hwh file, not from pin numbers.
```

The SDK gates all PL-dependent commands behind an overlay-loaded check. Commands with `requires_ips` return SCPI error `-200,"Hardware not ready"` if called without a compatible overlay, before reaching user handler code.

### 2. Hardware Access Model: PS vs PL

The Zynq has two separate hardware domains:

| Domain | Access method | Example |
|---|---|---|
| PS GPIO (ARM MIO) | `pynq.GPIO(pynq.GPIO.get_gpio_pin(n), 'out')` | LEDs, push buttons |
| PL GPIO (FPGA fabric) | `overlay.gpio_ip_name.write(reg_offset, value)` | Custom FPGA signals |
| PL MMIO (AXI4-Lite) | `overlay.ip_name.read/write(offset, value)` | Register-mapped IP cores |
| DMA transfer | `pynq.allocate()` + AXI DMA IP | ADC streaming, waveform capture |

Never use bare pin integers as if they were ESP32 GPIO numbers. Always use the PYNQ abstraction layer (`pynq.GPIO`, `pynq.MMIO`, `overlay.<ip>`).

### 3. No Deterministic Timing from Python

ARM Cortex-A9 runs Linux userspace with OS scheduling. `timeout_ms` in `CommandDescriptor` is a **soft client-side "give up" hint**, not a server-side enforcement deadline. OS preemption, GIL contention, and AXI bus latency variability mean no hard timing guarantees exist from Python. Waveform timing, precise ADC trigger alignment, and pulse generation must live in FPGA fabric.

### 4. asyncio.wait_for Does Not Cancel Blocking Threads

For sync handlers dispatched via `loop.run_in_executor`: `asyncio.wait_for` stops awaiting the Future when the timeout expires, but the underlying thread keeps executing. There is no preemptive cancellation of a blocked MMIO read.

Design implication: MMIO-accessing handlers must be async, using a polling loop with `await asyncio.sleep()` rather than blocking reads, so cancellation is cooperative:

```python
async def adc_read(adc_0):
    for _ in range(100):             # 100 ms max
        if adc_0.read(0x04) & 0x01: # poll ready bit
            return respond_float(adc_0.read(0x00) * 3.3 / 4095.0)
        await asyncio.sleep(0.001)
    return respond_error(-200, "ADC timeout")
```

### 5. mDNS Reliability on USB-Only Deployments

PYNQ-Z2 common deployment modes:
- **USB-OTG only** (RNDIS/ECM gadget): static IP `192.168.2.1`; mDNS may not traverse the host RNDIS stack reliably.
- **Ethernet (eth0)**: standard Zeroconf mDNS works normally.
- **No WiFi** without an add-on module.

`discovery.py` binds to all available interfaces. When `eth0` has no carrier, a warning is logged and the USB static-IP fallback is printed: `"USB mode: connect to 192.168.2.1:4880"`.

---

## Development Commands

UV is the preferred package manager for x86 development. On the PYNQ board use
pip against the system Python (PYNQ OS pre-installs pynq and its FPGA drivers).

```bash
# --- x86 dev machine (UV) ---

# Install in editable mode with dev extras
uv sync --extra dev

# Run all tests (no hardware required — uses MockBackend)
uv run pytest tests/ -v

# Run tests that require a real PYNQ board
uv run pytest tests/ --pynq -v

# Run a single test
uv run pytest tests/test_hislip_server.py::test_initialize_handshake -v

# Lint and format
uv run ruff check pynq_instrument/ && uv run ruff format pynq_instrument/

# Type checking
uv run mypy pynq_instrument/

# Build distribution
uv build

# --- PYNQ board (pip against system Python) ---

# pynq is already installed by the OS image; install only this package
pip install -e "." --no-deps

# Run instrument server from CLI
python -m pynq_instrument.cli --overlay design.bit --port 4880
```

### pyproject.toml

```toml
[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "pynq-instrument"
version = "0.1.0"
description = "Carbon PYNQ Instrument SDK"
requires-python = ">=3.8"
dependencies = [
    "zeroconf>=0.131.0",
    "pynq>=3.0.0",
]

[project.optional-dependencies]
dev = ["pytest>=7.0", "ruff>=0.1.0", "mypy>=1.0"]
```

---

## Architecture

### Four-Layer Design

```
SCPI string (wire)
       ↓
[Protocol Layer]         hislip.py, hislip_server.py, hislip_session.py, async_channel.py
  HiSLIPSession          Parse frames, enforce handshake, serialize, overlap queue
       ↓
[Instrument API]         instrument.py, command_registry.py, scpi_parser.py, scpi_standard.py
  CommandRegistry        Normalize SCPI, validate params, route to handler
       ↓
[Backend Abstraction]    hardware_backend.py, overlay_manager.py, dma_buffer.py
  HardwareBackend        Check overlay state, inject IP objects, manage DMA buffers
       ↓
[Hardware]               pynq.Overlay, pynq.MMIO, pynq.GPIO, pynq.allocate()
  PYNQ / Zynq            AXI4-Lite registers, AXI DMA, PS GPIO
```

User code lives in the Instrument API layer only. Users write handler functions; the Backend layer handles IP injection and safety gates; users never interact with the Protocol layer.

### Package Layout

```
pynq_sdk/
├── pynq_instrument/
│   ├── __init__.py            # Public API: Instrument, CommandType, respond_*, HardwareBackend
│   ├── instrument.py          # Instrument class, @inst.command decorator, startup orchestration
│   ├── command_registry.py    # CommandDescriptor, ParamDescriptor, registry dict
│   ├── scpi_parser.py         # SCPI normalization: uppercase mnemonics, whitespace, arg split
│   ├── param_parser.py        # Type-hint introspection → typed arg extraction from SCPI string
│   ├── response_helpers.py    # respond_float/int/bool/enum/float_array/error
│   ├── hislip.py              # 16-byte header pack/unpack (identical format to esp32_sdk)
│   ├── hislip_server.py       # asyncio TCP server: accept loop + session dispatch
│   ├── hislip_session.py      # Per-client sync-channel state machine + overlap queue
│   ├── async_channel.py       # Per-client async channel (port 4881) for *STB? queries
│   ├── hardware_backend.py    # HardwareBackend ABC + PYNQBackend + MockBackend
│   ├── overlay_manager.py     # Overlay load/unload/status, HWH IP inventory, version check
│   ├── dma_buffer.py          # pynq.allocate() async context manager
│   ├── discovery.py           # MDNSAdvertiser (zeroconf) + USB/static-IP fallback
│   ├── scpi_standard.py       # IEEE 488.2 built-ins: *IDN?, *RST, *TST?, *OPC?, *CLS,
│   │                          #   *WAI, *ESR?, *ESE/*ESE?, *SRE/*SRE?, *STB?
│   ├── scpi_system.py         # SYST:ERR?, SYSTEM:COMMANDS?
│   ├── scpi_overlay.py        # OVERLAY:LOAD, OVERLAY:STATUS?, OVERLAY:VERSION?, OVERLAY:UNLOAD
│   └── errors.py              # CarbonError, SCPI error queue (FIFO), error codes
├── examples/
│   ├── hello_world.ipynb      # Software-only: no overlay, PS GPIO only
│   ├── adc_overlay.ipynb      # PL ADC IP with DMA streaming
│   └── reconfigure.ipynb      # Dynamic overlay switching demo
├── tests/
│   ├── conftest.py            # MockBackend fixture — all tests run without hardware
│   ├── test_hislip_server.py  # Protocol framing, INITIALIZE handshake, overlap mode
│   ├── test_scpi_parser.py    # SCPI normalization, query detection, arg extraction
│   ├── test_param_validation.py
│   ├── test_response_helpers.py
│   ├── test_overlay_manager.py
│   ├── test_mdns_advertiser.py  # Zeroconf service registration, USB fallback path
│   ├── test_integration.py    # End-to-end: SCPI command → HiSLIP → response (MockBackend)
│   └── test_concurrent.py     # Multi-client + overlap mode stress test
├── tools/
│   └── generate_profile.py    # Query live device → profile.yaml (keep in sync with ../esp32_sdk/tools/)
├── pyproject.toml
├── README.md
└── GUIDE.md
```

### Core Abstractions

**`SCPIParser` (`scpi_parser.py`)** — mirrors `scpi_parser.c` from the ESP32 SDK. Normalizes raw SCPI strings before registry lookup. This is a **separate module**, not inline in the session:

```python
def normalize_scpi(raw: str) -> tuple[str, list[str]]:
    """
    Returns (normalized_mnemonic, args).
    "gpio:set 1 HIGH" → ("GPIO:SET", ["1", "HIGH"])
    "*idn?"            → ("*IDN?", [])
    "  TEMP : READ?  " → ("TEMP:READ?", [])
    """
```

Rules: strip whitespace → uppercase the mnemonic token → split on first whitespace boundary → return mnemonic and raw arg list. The mnemonic is everything before the first space or end-of-string; args are not uppercased (string values are case-sensitive).

**`Instrument`** — top-level user entry point. Owns `CommandRegistry`, `HardwareBackend`, `OverlayManager`. Orchestrates startup: validate backend → register built-ins → start mDNS → start HiSLIP server (sync + async channels).

**`CommandDescriptor`** — mirrors `carbon_cmd_descriptor_t` from the ESP32 SDK, extended for PYNQ:
- `scpi_command`, `type`, `handler`, `params`, `timeout_ms`, `group`, `description` — identical semantics
- `requires_ips: list[str]` — FPGA IP names required from the loaded overlay (e.g. `["adc_0", "dma_0"]`). SDK resolves these from `OverlayManager` and injects them as leading positional arguments to the handler.
- `requires_overlay: str | None` — if set, checks that the loaded overlay name matches before dispatching.

**`HardwareBackend` (ABC)** — decouples command handlers from hardware:
```python
class HardwareBackend(ABC):
    @abstractmethod
    def is_overlay_loaded(self) -> bool: ...
    @abstractmethod
    def get_ip(self, name: str) -> Any: ...
    @abstractmethod
    def get_ps_gpio(self, index: int) -> Any: ...
    @abstractmethod
    def allocate_dma_buffer(self, shape, dtype) -> AsyncContextManager: ...
```

`PYNQBackend` is the concrete implementation using `pynq.Overlay`. `MockBackend` is used in all tests — it never imports `pynq`, so the full test suite runs on x86 development machines.

**`OverlayManager`** — owns overlay state:
- `load(bitfile)` — calls `pynq.Overlay(bitfile)`, parses HWH, builds IP name inventory, drains in-flight commands first
- `unload()` — graceful: waits for in-flight commands to finish, then releases overlay
- `inventory() -> list[str]` — IP names available in the current overlay
- `version() -> str` — from HWH metadata or .bit filename

**`HiSLIPSession` (`hislip_session.py`)** — per-client sync-channel state machine: INITIALIZE handshake → DATA_END command loop with overlap queue → DEVICE_CLEAR. Separated from `hislip_server.py` (which owns the accept loop only).

### HiSLIP Protocol

Identical 16-byte big-endian header to esp32_sdk:
```python
struct.pack(">2sBBIQ", b"HS", msg_type, control_code, msg_param, payload_len)
```

Relevant message types: `0` INITIALIZE, `1` INITIALIZE_RESPONSE, `7` DATA_END, `8/9` DEVICE_CLEAR. Port 4880 default (sync channel).

### Async Channel (HiSLIP 2.0)

HiSLIP 2.0 requires a second TCP connection per session for non-blocking status queries. Implemented in `async_channel.py`:

- **Sync channel** (port 4880): carries DATA_END commands; blocks until handler returns response.
- **Async channel** (port 4881): carries `*STB?` only; returns the status byte immediately without waiting for any in-progress sync-channel command.

`HiSLIPServer` accepts connections on both ports. A client connects to 4881 after the sync-channel INITIALIZE handshake; the session correlates the two connections by session ID (from the INITIALIZE message). `async_channel.py` owns the 4881 accept loop and routes `*STB?` to the status register maintained by `scpi_standard.py`.

### Overlap Mode

Mirrors the ESP32 SDK's command queue (Kconfig `HISLIP_WORKER_QUEUE_DEPTH`). `HiSLIPSession` maintains a per-client `asyncio.Queue` (default depth 8). The DATA_END receive loop enqueues commands; a separate coroutine dequeues and dispatches them. Responses are sent in completion order. This allows clients to pipeline commands without waiting for each response.

### User Developer Flow

```python
from pynq_instrument import Instrument, CommandType, respond_float, respond_enum

inst = Instrument("Acme", "ADC-Logger", "SN-001", "1.0.0")

# Step 1: load overlay (mandatory for any PL-dependent command)
inst.load_overlay("adc_design.bit")
# SDK builds IP inventory from HWH. Omit this if all commands are PS-only.

# Step 2: register commands
# PL-dependent: SDK resolves and injects `adc_0` from the loaded overlay
@inst.command("ADC:READ?", type=CommandType.QUERY, requires=["adc_0"])
async def adc_read(adc_0):           # adc_0 is the pynq AXI IP object
    for _ in range(100):
        if adc_0.read(0x04) & 0x01: # poll ready bit — cooperative cancellation
            return respond_float(adc_0.read(0x00) * 3.3 / 4095.0)
        await asyncio.sleep(0.001)
    return respond_error(-200, "ADC timeout")

# PS-only: no overlay dependency
@inst.command("LED:SET", type=CommandType.WRITE)
def led_set(value: int):
    inst.backend.get_ps_gpio(0).write(value)
    return respond_enum("OK")

# DMA burst: async context manager handles buffer lifecycle
@inst.command("ADC:BURST?", type=CommandType.QUERY,
              requires=["adc_0", "dma_0"], timeout_ms=10000)
async def adc_burst(adc_0, dma_0):
    async with inst.backend.allocate_dma_buffer((1024,), "uint16") as buf:
        dma_0.recvchannel.transfer(buf)
        await dma_0.recvchannel.wait_async()
        return respond_float_array(buf.tolist())
    # buf.freebuffer() called automatically on context exit

# Step 3: start (blocks until interrupted)
await inst.start_async()
```

The SDK automatically registers: `*IDN?`, `*RST`, `*TST?`, `*OPC?`, `*OPC`, `*CLS`, `*WAI`, `*ESR?`, `*ESE`, `*ESE?`, `*SRE`, `*SRE?`, `*STB?`, `SYST:ERR?`, `SYSTEM:COMMANDS?`, `OVERLAY:LOAD`, `OVERLAY:STATUS?`, `OVERLAY:VERSION?`, `OVERLAY:UNLOAD`.

### Parameter Parsing

`scpi_parser.py` normalizes the raw string first. `param_parser.py` then introspects handler type hints to extract and validate typed arguments. IP objects declared in `requires=` are prepended to the argument list before SCPI-parsed args:

```
SCPI wire:  "ADC:CONFIG 12 1000"
Handler:    def config(adc_0, bits: int, rate: int)
                       ^^^^^  injected from overlay (not from wire)
                              ^^^^^^^^^^^^^^^^^^^^ parsed from wire
```

Async handlers (`async def`) are awaited. Sync handlers run in `loop.run_in_executor` — suitable only for fast, bounded operations where hardware is known to respond.

### Response Helpers

All in `response_helpers.py`. Return bare strings — no units (units belong in `profile.yaml`). Identical output format to esp32_sdk:

| Helper | Output |
|---|---|
| `respond_float(3.14)` | `"3.14"` |
| `respond_int(42)` | `"42"` |
| `respond_bool(True)` | `"1"` |
| `respond_enum("RISING")` | `"RISING"` |
| `respond_float_array([1.0, 2.5])` | `"1.0,2.5"` |
| `respond_error(2, "bad pin")` | `"ERR:2:bad pin"` |

### `*TST?` Self-Test Specification

Returns `"0"` (pass) or `"1"` (fail). Checks in order:
1. If any registered command has `requires_ips`, verify an overlay is loaded.
2. Verify all required IPs across all registered commands are present in the HWH inventory.
3. If any command uses PS GPIO, verify the GPIO sysfs path exists (no loopback write — toggling output pins on self-test is unsafe).
4. Return `"0"` if all checks pass, `"1"` otherwise, and push a SCPI error to the queue on failure.

### DMA Buffer Management

`allocate_dma_buffer()` returns an async context manager. Handlers must use `async with` — never call `pynq.allocate()` directly in user code:

```python
async with inst.backend.allocate_dma_buffer((1024,), "uint16") as buf:
    dma_0.recvchannel.transfer(buf)
    await dma_0.recvchannel.wait_async()
    return respond_float_array(buf.tolist())
# buf.freebuffer() is always called on exit, including on exception or timeout
```

### IEEE 488.2 Status Registers (`scpi_standard.py`)

Three registers maintained as module-level state in `scpi_standard.py`:
- **ESR** (Event Status Register, 8-bit): bits set by events (`*TST?` fail, command error, etc.)
- **ESE** (Event Status Enable mask, 8-bit): which ESR bits propagate to status byte bit 5
- **SRE** (Service Request Enable mask, 8-bit): which status byte bits assert SRQ

Status byte bit layout: bit 6 = MAV (message available), bit 5 = ESB (ESR summary).

`*STB?` returns the status byte and is only served on the async channel — it must not block on the sync channel. `*CLS` clears ESR and the error queue. `*WAI` is a no-op (all commands execute synchronously relative to the sync channel).

### OVERLAY:* Commands (`scpi_overlay.py`)

Allows remote overlay management via SCPI without SSH:

```
OVERLAY:LOAD "adc_design.bit"  → loads bitfile, returns "OK" or ERR
OVERLAY:STATUS?                → "LOADED:adc_design.bit" or "NONE"
OVERLAY:VERSION?               → version string from HWH metadata
OVERLAY:UNLOAD                 → graceful unload, drains in-flight commands first
```

`OVERLAY:LOAD` blocks the sync channel until loading completes. Any command with `requires_ips` that arrives during loading is queued (overlap queue) or returns `-200` if the queue is full.

### Discovery (`discovery.py`)

Tries mDNS first (Zeroconf, `_hislip._tcp`, hostname `{prefix}-{last4mac}.local`). Binds to all interfaces. On USB-only (`usb0` RNDIS gadget at `192.168.2.1`), logs the static-IP fallback. Mirrors the ESP32 SDK mDNS behavior so Carbon's discovery finds both device types identically.

### Profile Generation

`tools/generate_profile.py` connects via HiSLIP, queries `SYSTEM:COMMANDS?` and `*IDN?`, emits Carbon-compatible `profile.yaml`. Must produce the same YAML schema as `../esp32_sdk/tools/generate_profile.py` — keep both in sync when the format changes.

### Testing

All tests use `MockBackend` from `conftest.py`. Hardware tests (requiring a real PYNQ board) live in `tests/hardware/` and are skipped unless `--pynq` is passed to pytest. `test_integration.py` runs a full SCPI command flow end-to-end using `MockBackend` and an in-process HiSLIP client — no hardware, no network. `test_mdns_advertiser.py` tests Zeroconf service registration and the USB fallback code path using monkeypatched network interface enumeration.

---

## Key Invariants

- Response strings must never include units (`"3.3V"` is wrong; `"3.3"` is correct). Units live in `profile.yaml`.
- `SYSTEM:COMMANDS?` must return valid JSON — the daemon and profile generator parse it strictly.
- `scpi_parser.normalize_scpi()` must be called on every incoming SCPI string before registry lookup. The registry stores uppercase mnemonics; raw client strings are never compared directly.
- HiSLIP INITIALIZE handshake must complete before any DATA_END is accepted per client; drop clients that skip it.
- Commands with `requires_ips` return SCPI error `-200,"Hardware not ready"` if called without a compatible overlay loaded. They never reach user handler code.
- `timeout_ms` is a soft client-side "give up" hint. The server does not enforce it as a hard deadline. Do not design handlers that rely on it for correctness.
- `asyncio.wait_for` on a sync executor task does not cancel the thread. Handlers dispatched to `run_in_executor` must be bounded by design (hardware known to respond). Unbounded MMIO access must use async polling instead.
- All PL IP access goes through `HardwareBackend.get_ip()` — never directly via `pynq.Overlay` in user handler code. This keeps `MockBackend` substitution clean.
- DMA buffers must only be allocated inside `async with inst.backend.allocate_dma_buffer(...)` — never via `pynq.allocate()` directly in handler code.
- `*STB?` is only valid on the async channel. Sending it on the sync channel returns `"0"` with a logged warning (not an error — clients may not know which channel they're on).
- Timing-critical logic (waveform generation, ADC triggering, pulse sequencing) must live in FPGA fabric. Python/asyncio handles control plane only.
- Built-in commands (`*IDN?`, `SYST:ERR?`, etc.) are registered after user commands so user commands registered with the same name take precedence.
