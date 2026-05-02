# Getting Started with the Carbon PYNQ Instrument SDK

This tutorial walks you from a fresh PYNQ-Z2 board to a fully connected instrument discoverable by the Carbon daemon. No prior HiSLIP or SCPI knowledge required.

---

## What This SDK Does

The SDK turns your PYNQ-Z2 into a network-connected test instrument. You write Python functions; the SDK handles everything else:

- **HiSLIP server** (TCP, port 4880) — the same wire protocol used by Keysight/R&S benchtop instruments
- **SCPI command dispatch** — routes incoming command strings to your handler functions
- **FPGA overlay management** — loads bitstreams, inventories IP cores, and gates commands behind hardware checks
- **mDNS advertisement** — the Carbon daemon finds your board automatically on the local network

From the Carbon daemon's perspective, your PYNQ board is indistinguishable from an ESP32-based Carbon instrument. Both SDKs speak the same protocol.

---

## Prerequisites

| What | Where to get it |
|---|---|
| PYNQ-Z2 board | Running PYNQ OS (ships with `pynq` Python package pre-installed) |
| Network connection | Ethernet cable (built-in RJ45) or USB WiFi dongle |
| `pynq-instrument` package | See installation below |
| FPGA overlay | `.bit` + `.hwh` file pair (only needed for PL-dependent commands) |

---

## Part 1: Installation

### On the PYNQ Board

Connect via SSH over Ethernet (`pynq.local` or the board's DHCP address):

```bash
ssh xilinx@pynq.local   # mDNS hostname (works on most networks)
# or: ssh xilinx@<board-ip>
# default password: xilinx
```

Install the package. The `--no-deps` flag is important — `pynq` is already installed by the OS image and should not be upgraded:

```bash
pip install --no-deps pynq-instrument
```

Verify:

```bash
python -c "import pynq_instrument; print(pynq_instrument.__version__)"
# 0.1.0
```

### On Your Development Machine (x86)

UV is the recommended package manager. The `pynq` package is not available on x86 — the SDK's `MockBackend` lets the full test suite run without any hardware:

```bash
git clone <repo-url>
cd pynq_sdk
uv sync --extra dev
uv run pytest tests/ -v   # all tests pass, no board needed
```

---

## Part 2: Your First Instrument (No FPGA Required)

You don't need a bitstream to start. PS GPIO (ARM-side pins) and pure Python logic work immediately.

Create `/home/xilinx/my_instrument.py` on the board:

```python
import asyncio
from pynq_instrument import Instrument, CommandType, respond_enum, respond_int

# Four identity strings: manufacturer, model, serial, firmware version
inst = Instrument("Acme", "Hello-World", "SN-001", "1.0.0")

@inst.command("LED:SET", type=CommandType.WRITE)
def led_set(value: int):
    inst.backend.get_ps_gpio(0).write(value)
    return respond_enum("OK")

@inst.command("LED:GET?", type=CommandType.QUERY)
def led_get():
    return respond_int(inst.backend.get_ps_gpio(0).read())

@inst.command("ECHO", type=CommandType.WRITE)
def echo(message: str):
    print(f"[instrument] {message}")
    return respond_enum("OK")

asyncio.run(inst.start_async())
```

Run it:

```bash
python /home/xilinx/my_instrument.py
# INFO  Listening on 0.0.0.0:4880 (sync) and 0.0.0.0:4881 (async)
# INFO  Advertising _hislip._tcp as acme-a1b2.local
```

Or use a proper HiSLIP client from your development machine — see Part 5.

---

## Part 3: Loading a Bitstream

The PYNQ-Z2 has an FPGA fabric (PL — Programmable Logic) that is separate from the ARM cores (PS — Processing System). The FPGA starts unconfigured. You load a bitstream to enable your custom IP cores.

### What you need

A bitstream is always **two files**:
- `design.bit` — the FPGA configuration binary
- `design.hwh` — the Hardware Handoff file (XML describing IP cores and their register maps)

Both files must be in the same directory with the same base name.

### Method A: Load at Startup in Python

Call `load_overlay` before registering any PL-dependent commands:

```python
import asyncio
from pynq_instrument import Instrument, CommandType, respond_float, respond_error

inst = Instrument("Acme", "ADC-Logger", "SN-001", "1.0.0")

# Switch from MockBackend to real PYNQ hardware
inst.use_pynq_backend()

# Load the bitstream. Blocks until the FPGA is configured (~1-2 seconds).
# The .hwh file must be in the same directory as the .bit file.
inst.load_overlay("/home/xilinx/overlays/adc_design.bit")

# Now register commands that use FPGA IP cores.
# The SDK reads the HWH to learn that "adc_0" exists.
@inst.command("ADC:READ?", type=CommandType.QUERY, requires=["adc_0"])
async def adc_read(adc_0):
    # adc_0 is the pynq AXI IP object — injected automatically
    for _ in range(100):
        if adc_0.read(0x04) & 0x01:          # poll the ready bit
            return respond_float(adc_0.read(0x00) * 3.3 / 4095.0)
        await asyncio.sleep(0.001)
    return respond_error(-200, "ADC timeout")

asyncio.run(inst.start_async())
```

The `requires=["adc_0"]` declaration does three things:
1. Gates the command — if no overlay is loaded, returns `ERR:-200:Hardware not ready` without calling your handler
2. Verifies `adc_0` exists in the loaded HWH
3. Injects the `pynq` IP object as the first argument to your handler

### Method B: Load via the CLI (No Script Required)

The SDK ships with a built-in CLI that exposes overlay loading as a flag. Use this when you want to run the instrument without writing a script:

```bash
python -m pynq_instrument.cli \
    --overlay /home/xilinx/overlays/adc_design.bit \
    --manufacturer Acme \
    --model ADC-Logger \
    --serial SN-001 \
    --firmware 1.0.0
```

All flags and their defaults:

| Flag | Default | Description |
|---|---|---|
| `--overlay FILE` | _(none)_ | Bitfile to load on startup |
| `--port N` | `4880` | HiSLIP sync channel port |
| `--async-port N` | `4881` | HiSLIP async channel port |
| `--manufacturer` | `Carbon` | `*IDN?` field 1 |
| `--model` | `PYNQ-Instrument` | `*IDN?` field 2 |
| `--serial` | `SN-0001` | `*IDN?` field 3 |
| `--firmware` | `0.1.0` | `*IDN?` field 4 |
| `--no-mdns` | _(flag)_ | Disable mDNS advertisement |
| `--log-level` | `INFO` | `DEBUG / INFO / WARNING / ERROR` |

The CLI starts the server with all built-in commands (`*IDN?`, `OVERLAY:*`, `SYST:ERR?`, etc.) but without any custom application commands. It is useful for verifying the overlay loads and the network stack works before writing application code.

### Method C: Load Remotely Over SCPI

You can reload the FPGA fabric without SSH using the built-in `OVERLAY:*` commands. The sync channel blocks until loading completes, so commands queued by other clients wait in the pipeline:

```
OVERLAY:LOAD "/home/xilinx/overlays/v2_design.bit"
→ OK

OVERLAY:STATUS?
→ LOADED:v2_design.bit

OVERLAY:VERSION?
→ 1.2.0

OVERLAY:UNLOAD
→ OK
```

This is how the Carbon daemon performs remote firmware updates on instruments.

### What Happens If You Forget to Load

If a client sends `ADC:READ?` when no overlay is loaded, the SDK catches it before your handler is called:

```
ADC:READ?
→ ERR:-200:Hardware not ready
```

The error is also pushed to the SCPI error queue, readable via `SYST:ERR?`.

---

## Part 4: Writing Handlers

### Handler Signatures

The SDK introspects your handler's type hints to parse SCPI arguments:

```python
# SCPI wire: "ADC:CONFIG 12 1000"
@inst.command("ADC:CONFIG", type=CommandType.WRITE, requires=["adc_0"])
async def adc_config(adc_0, bits: int, rate: int):
    #            ^^^^^ injected from overlay (not from wire)
    #                         ^^^^^^^^^^^^^^ parsed from wire string
    adc_0.write(0x00, bits)
    adc_0.write(0x04, rate)
    return respond_enum("OK")
```

Supported parameter types:

| Type hint | SCPI input | Notes |
|---|---|---|
| `int` | `"42"`, `"0xFF"`, `"0b1010"` | Hex and binary literals supported |
| `float` | `"3.14"`, `"1e-6"` | |
| `bool` | `"1"`, `"0"`, `"ON"`, `"OFF"`, `"TRUE"`, `"FALSE"` | Case-insensitive |
| `str` | `"RISING"`, `'"quoted value"'` | Quotes stripped automatically |
| _(no hint)_ | any | Passed as raw string |

### Async vs Sync Handlers

Use `async def` when the handler polls hardware — this keeps the server responsive while waiting:

```python
@inst.command("ADC:READ?", type=CommandType.QUERY, requires=["adc_0"])
async def adc_read(adc_0):
    for _ in range(100):
        if adc_0.read(0x04) & 0x01:
            return respond_float(adc_0.read(0x00) * 3.3 / 4095.0)
        await asyncio.sleep(0.001)   # yields to the event loop between polls
    return respond_error(-200, "ADC timeout")
```

Use `def` (sync) only for fast, bounded operations where you know the hardware responds immediately:

```python
@inst.command("LED:SET", type=CommandType.WRITE)
def led_set(value: int):
    inst.backend.get_ps_gpio(0).write(value)
    return respond_enum("OK")
```

Do not use `def` for MMIO reads that could stall — a blocked thread cannot be cancelled by the asyncio timeout system.

### DMA Burst Transfers

For streaming data from the FPGA fabric, use the DMA buffer context manager. This ensures the buffer is always freed, even if the handler raises an exception:

```python
@inst.command("ADC:BURST?", type=CommandType.QUERY,
              requires=["adc_0", "dma_0"], timeout_ms=10000)
async def adc_burst(adc_0, dma_0):
    async with inst.backend.allocate_dma_buffer((1024,), "uint16") as buf:
        dma_0.recvchannel.transfer(buf)
        await dma_0.recvchannel.wait_async()
        return respond_float_array(buf.tolist())
    # buf.freebuffer() is always called on exit
```

Never call `pynq.allocate()` directly in handler code.

### Response Helpers

All helpers return plain strings — **no units**. Units belong in `profile.yaml`.

```python
from pynq_instrument import (
    respond_float,        # respond_float(3.14)       → "3.14"
    respond_int,          # respond_int(42)            → "42"
    respond_bool,         # respond_bool(True)         → "1"
    respond_enum,         # respond_enum("RISING")     → "RISING"
    respond_float_array,  # respond_float_array([1,2]) → "1.0,2.0"
    respond_error,        # respond_error(-200, "msg") → "ERR:-200:msg"
)
```

---

## Part 5: Connecting with the Carbon Daemon

The Carbon daemon (`carbond`) discovers and controls instruments automatically. There are two steps: generating a profile, then registering it.

### Step 1: Generate a Profile

With the instrument running on the board, run the profile generator from your development machine:

```bash
python tools/generate_profile.py \
    --host 192.168.2.1 \
    --out profile.yaml
```

This connects to your board via HiSLIP, queries `*IDN?` and `SYSTEM:COMMANDS?`, and writes a `profile.yaml` file. The output looks like:

```yaml
identity:
  manufacturer: Acme
  model: ADC-Logger
  serial: SN-001
  firmware: 1.0.0

connection:
  host: 192.168.2.1
  port: 4880
  protocol: hislip

commands:
  - scpi: "ADC:READ?"
    type: query
    timeout_ms: 5000
    group: ADC
    description: "Read ADC voltage"

  - scpi: "ADC:CONFIG"
    type: write
    group: ADC
    params:
      - name: bits
        type: int
      - name: rate
        type: int
```

### Step 2: Register with `carbond`

Copy `profile.yaml` to your Carbon configuration directory and restart the daemon. The daemon will:

1. Read the profile at startup
2. Connect to `192.168.2.1:4880` via HiSLIP
3. Make the instrument's commands available through the Carbon API

If mDNS is working (Ethernet connection with a functioning mDNS stack), the daemon can discover the board automatically using the `_hislip._tcp` service type — no static IP needed.

### Step 3: Verify Discovery

Check that the board is advertising itself:

```bash
# Linux
avahi-browse -r _hislip._tcp

# macOS
dns-sd -B _hislip._tcp
```

You should see an entry like:

```
+ eth0 IPv4 acme-a1b2._hislip._tcp.local  Acme ADC-Logger
= eth0 IPv4 acme-a1b2._hislip._tcp.local
   hostname = [acme-a1b2.local]
   address  = [192.168.1.42]
   port     = [4880]
   txt      = ["manufacturer=Acme" "model=ADC-Logger"]
```

If the board is on a network where mDNS works, `carbond` will discover it automatically. Otherwise, use the board's static IP in the profile.

---

## Part 6: Testing Without a Board

The full test suite runs on x86 using `MockBackend`, which never imports `pynq`. Use this during development before deploying to the board.

```bash
uv run pytest tests/ -v
```

For your own code, construct a `MockBackend` directly:

```python
from pynq_instrument import MockBackend, Instrument, CommandType, respond_float

backend = MockBackend()
backend.load_mock_overlay(["adc_0", "dma_0"])  # simulate a loaded overlay

inst = Instrument("Acme", "ADC-Logger", "SN-001", "1.0.0", backend=backend)

@inst.command("ADC:READ?", type=CommandType.QUERY, requires=["adc_0"])
async def adc_read(adc_0):
    # adc_0 is a MockIP — read(offset) returns 0 by default
    # you can pre-populate registers:
    #   backend.mock_overlay["adc_0"].registers[0x00] = 2048
    return respond_float(adc_0.read(0x00) * 3.3 / 4095.0)
```

`MockIP` has a `registers` dict. Write to it in your test setup to simulate hardware state:

```python
backend.mock_overlay["adc_0"].registers[0x04] = 0x01  # set ready bit
backend.mock_overlay["adc_0"].registers[0x00] = 2048   # raw ADC count
```

---

## Part 7: Running as a Service

To start the instrument automatically at boot, create a systemd service on the board:

```ini
# /etc/systemd/system/my-instrument.service
[Unit]
Description=Carbon PYNQ Instrument
After=network.target

[Service]
User=xilinx
ExecStart=/usr/bin/python /home/xilinx/my_instrument.py
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl enable my-instrument
sudo systemctl start my-instrument
sudo journalctl -u my-instrument -f   # follow logs
```

Or use the CLI directly as the `ExecStart`:

```bash
ExecStart=/usr/bin/python -m pynq_instrument.cli \
    --overlay /home/xilinx/overlays/adc_design.bit \
    --manufacturer Acme \
    --model ADC-Logger \
    --serial SN-001
```

---

## Quick Reference

### Built-in Commands (always available)

| Command | Returns | Notes |
|---|---|---|
| `*IDN?` | `Manufacturer,Model,Serial,Firmware` | Four-field identity string |
| `*RST` | `OK` | Reset instrument state |
| `*CLS` | `OK` | Clear error queue and status registers |
| `*TST?` | `0` or `1` | Self-test: 0 = pass, 1 = fail |
| `*OPC?` | `1` | Operation complete query |
| `SYST:ERR?` | Error string or `0,"No error"` | Pop from error queue |
| `SYST:ERR:COUN?` | Integer | Errors in queue |
| `SYSTEM:COMMANDS?` | JSON | All registered commands |
| `OVERLAY:LOAD "f.bit"` | `OK` or `ERR:...` | Load bitfile |
| `OVERLAY:STATUS?` | `LOADED:name` or `NONE` | |
| `OVERLAY:VERSION?` | Version string | From HWH metadata |
| `OVERLAY:UNLOAD` | `OK` | Graceful unload |

### Common Patterns

```python
# Enforce a specific overlay is loaded (not just any overlay)
@inst.command("ADC:READ?", type=CommandType.QUERY,
              requires=["adc_0"], requires_overlay="adc_design")
async def adc_read(adc_0): ...

# Optional parameter with default
@inst.command("ADC:SCALE", type=CommandType.WRITE)
def set_scale(channel: int, gain: float = 1.0): ...

# Grouping for SYSTEM:COMMANDS? output
@inst.command("ADC:READ?", type=CommandType.QUERY,
              group="ADC", description="Read voltage from ADC channel",
              requires=["adc_0"])
async def adc_read(adc_0): ...
```
