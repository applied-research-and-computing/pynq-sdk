# Carbon PYNQ Instrument SDK

Turn a PYNQ-Z2 board (Zynq-7020: dual ARM Cortex-A9 + FPGA fabric) into a network-connected Carbon instrument. You write Python handler functions; the SDK handles the HiSLIP server, SCPI dispatch, mDNS discovery, and Carbon daemon integration.

```python
from pynq_instrument import Instrument, CommandType, respond_float

inst = Instrument("Acme", "ADC-Logger", "SN-001", "1.0.0")

@inst.command("ADC:READ?", type=CommandType.QUERY, requires=["adc_0"])
async def adc_read(adc_0):
    for _ in range(100):
        if adc_0.read(0x04) & 0x01:
            return respond_float(adc_0.read(0x00) * 3.3 / 4095.0)
        await asyncio.sleep(0.001)
    return respond_error(-200, "ADC timeout")

await inst.start_async()
```

The companion [ESP32 SDK](../esp32_sdk) covers microcontroller-based instruments. Both SDKs produce instruments that are interchangeable from the Carbon daemon's perspective.

---

## Hardware Requirements

- **PYNQ-Z2** (or any Zynq-7000 PYNQ board)
- Ethernet cable or USB-OTG cable for network access
- FPGA overlay (`.bit` + `.hwh` files) for custom IP cores

---

## Installation

### x86 Development Machine

[UV](https://docs.astral.sh/uv/) is recommended. The `pynq` package is **not** installed on x86 — tests run against `MockBackend` which requires no FPGA hardware.

```bash
git clone ...
cd pynq_sdk
uv sync --extra dev
uv run pytest tests/ -v   # all 72 tests pass, no hardware needed
```

### On the PYNQ Board

`pynq` is pre-installed by the PYNQ OS image. Install only this package (no extra dependencies needed):

```bash
ssh xilinx@192.168.2.1   # or pynq.local if mDNS works
pip install --no-deps pynq-instrument
```

---

## Quick Start

### 1. Software-only (no overlay)

PS GPIO and pure-Python logic work without loading a bitfile:

```python
import asyncio
from pynq_instrument import Instrument, CommandType, respond_enum, respond_int

inst = Instrument("Acme", "Hello", "SN-001", "1.0.0")

@inst.command("LED:SET", type=CommandType.WRITE)
def led_set(value: int):
    inst.backend.get_ps_gpio(0).write(value)
    return respond_enum("OK")

@inst.command("LED:GET?", type=CommandType.QUERY)
def led_get():
    return respond_int(inst.backend.get_ps_gpio(0).read())

asyncio.run(inst.start_async())
```

### 2. With FPGA overlay

```python
import asyncio
from pynq_instrument import Instrument, CommandType, respond_float, respond_error

inst = Instrument("Acme", "ADC-Logger", "SN-001", "1.0.0")

# Load bitfile + HWH before registering PL-dependent commands
inst.use_pynq_backend()
inst.load_overlay("/home/xilinx/overlays/adc_design.bit")

@inst.command("ADC:READ?", type=CommandType.QUERY, requires=["adc_0"])
async def adc_read(adc_0):
    for _ in range(100):
        if adc_0.read(0x04) & 0x01:          # poll ready bit
            return respond_float(adc_0.read(0x00) * 3.3 / 4095.0)
        await asyncio.sleep(0.001)
    return respond_error(-200, "ADC timeout")

asyncio.run(inst.start_async())
```

### 3. Connect and send commands

```bash
# Discover via mDNS
avahi-browse -r _hislip._tcp          # Linux
dns-sd -B _hislip._tcp                # macOS

# USB-OTG static IP fallback
nc 192.168.2.1 4880
# send: *IDN?   receive: Acme,ADC-Logger,SN-001,1.0.0
```

---

## The `@inst.command` Decorator

```python
@inst.command(
    "ADC:CONFIG",
    type=CommandType.WRITE,         # or CommandType.QUERY
    requires=["adc_0"],             # IP names from .hwh; injected as leading args
    requires_overlay="adc_design",  # optional: enforce specific overlay name
    timeout_ms=5000,                # soft hint for the client; not server-enforced
    group="ADC",                    # for SYSTEM:COMMANDS? JSON grouping
    description="Configure ADC",
)
async def adc_config(adc_0, bits: int, rate: int):
    #            ^^^^^ injected from overlay — not from SCPI wire
    #                         ^^^^^^^^^^^^^^ parsed from "ADC:CONFIG 12 1000"
    adc_0.write(0x00, bits)
    adc_0.write(0x04, rate)
    return respond_enum("OK")
```

**Handler types:**
- `async def` — awaited directly; use for MMIO polling (`await asyncio.sleep(...)` inside)
- `def` — dispatched via `run_in_executor`; only for fast, bounded operations

**`requires=["ip_name"]`** — the SDK calls `backend.get_ip(name)` for each entry and prepends the result to the handler arguments. If the overlay is not loaded, or the IP is absent, the command returns `ERR:-200:Hardware not ready` before reaching your handler.

---

## Response Helpers

Return bare strings — **no units**. Units belong in `profile.yaml`.

| Helper | Wire output | Carbon `TypedValue` |
|---|---|---|
| `respond_float(3.14)` | `"3.14"` | `float_value` |
| `respond_int(42)` | `"42"` | `int_value` |
| `respond_bool(True)` | `"1"` | `bool_value` |
| `respond_enum("RISING")` | `"RISING"` | `enum_value` |
| `respond_float_array([1.0, 2.5])` | `"1,2.5"` | `float_array` |
| `respond_error(-200, "timeout")` | `"ERR:-200:timeout"` | _(error field)_ |

---

## Built-in Commands

Registered automatically after your commands (so you can override any of them).

**IEEE 488.2:** `*IDN?` `*RST` `*CLS` `*TST?` `*OPC?` `*OPC` `*WAI` `*ESR?` `*ESE` `*ESE?` `*SRE` `*SRE?` `*STB?`

**System:** `SYST:ERR?` `SYST:ERR:COUN?` `SYSTEM:COMMANDS?`

**Overlay management** (registered only when an overlay manager is present):

| Command | Description |
|---|---|
| `OVERLAY:LOAD "path.bit"` | Load bitfile; blocks sync channel until done |
| `OVERLAY:STATUS?` | `LOADED:filename.bit` or `NONE` |
| `OVERLAY:VERSION?` | Version string from HWH metadata |
| `OVERLAY:UNLOAD` | Graceful unload |

---

## DMA Bursts

```python
@inst.command("ADC:BURST?", type=CommandType.QUERY,
              requires=["adc_0", "dma_0"], timeout_ms=10000)
async def adc_burst(adc_0, dma_0):
    async with inst.backend.allocate_dma_buffer((1024,), "uint16") as buf:
        dma_0.recvchannel.transfer(buf)
        await dma_0.recvchannel.wait_async()
        return respond_float_array(buf.tolist())
    # buf.freebuffer() called automatically, even on exception
```

Never call `pynq.allocate()` directly in handler code; use `allocate_dma_buffer()` so the buffer is always freed.

---

## Testing Without Hardware

All tests use `MockBackend` and run on x86:

```bash
uv run pytest tests/ -v                         # all tests, no hardware
uv run pytest tests/ --pynq -v                  # includes hardware/ tests (requires board)
uv run pytest tests/test_integration.py -v      # end-to-end SCPI flow
```

`MockBackend` never imports `pynq`. Simulate overlay loading in tests:

```python
backend = MockBackend()
backend.load_mock_overlay(["adc_0", "dma_0"])
inst = Instrument("Co", "Model", "SN", "1.0", backend=backend)
```

---

## Generating an Instrument Profile

Query a live device and emit `profile.yaml` for Carbon daemon integration:

```bash
python tools/generate_profile.py --host 192.168.2.1 --out profile.yaml
```

The YAML schema is identical to the ESP32 SDK's `generate_profile.py` output.

---

## Architecture

```
SCPI string (wire)
       ↓
[Protocol Layer]    hislip.py · hislip_server.py · hislip_session.py · async_channel.py
       ↓
[Instrument API]    instrument.py · command_registry.py · scpi_parser.py · scpi_standard.py
       ↓
[Backend]           hardware_backend.py · overlay_manager.py · dma_buffer.py
       ↓
[Hardware]          pynq.Overlay · pynq.MMIO · pynq.GPIO · pynq.allocate()
```

**Key constraints (Zynq-7020 / Linux userspace):**
- Overlay must be loaded before any PL-dependent command runs — the SDK enforces this automatically
- `asyncio.wait_for` does not cancel a blocked `run_in_executor` thread — use async polling (`await asyncio.sleep()`) for MMIO access
- No hard timing guarantees from Python — waveform generation and ADC triggering must live in FPGA fabric

---

## Development Commands

```bash
# Install (x86, UV)
uv sync --extra dev

# Test
uv run pytest tests/ -v

# Lint / format
uv run ruff check pynq_instrument/ && uv run ruff format pynq_instrument/

# Type check
uv run mypy pynq_instrument/

# Build wheel
uv build

# Run on PYNQ board (after pip install --no-deps pynq-instrument)
python -m pynq_instrument.cli --overlay design.bit --port 4880
```

---

## Differences from ESP32 SDK

| | ESP32 SDK | PYNQ SDK |
|---|---|---|
| Language | C / FreeRTOS | Python / asyncio |
| SoC | ESP32 (Xtensa LX7) | Zynq-7020 (ARM + FPGA) |
| Command registration | `carbon_register_command(&desc)` | `@inst.command(...)` |
| Hardware access | GPIO pin integers | Overlay IP names from HWH |
| Timing | Hard real-time | Linux soft real-time |
| Iteration speed | Compile → flash → test | Edit → run (instant) |
