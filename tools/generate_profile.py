#!/usr/bin/env python3
"""
generate_profile.py — Query a live PYNQ instrument and emit profile.yaml.

Usage:
    python tools/generate_profile.py --host 192.168.2.1 --port 4880 --out profile.yaml

The output YAML schema is identical to esp32_sdk/tools/generate_profile.py so
Carbon's daemon can use profiles from both device types interchangeably.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import sys
from typing import Optional

import yaml  # type: ignore[import]


HEADER_FMT = ">2sBBIQ"
HEADER_SIZE = 16


def pack_header(msg_type: int, cc: int, param: int, payload_len: int) -> bytes:
    return struct.pack(HEADER_FMT, b"HS", msg_type, cc, param, payload_len)


async def recv_msg(reader: asyncio.StreamReader) -> tuple:
    header = await reader.readexactly(HEADER_SIZE)
    _, msg_type, cc, param, plen = struct.unpack(HEADER_FMT, header)
    payload = await reader.readexactly(int(plen)) if plen else b""
    return msg_type, cc, param, payload


async def send_msg(
    writer: asyncio.StreamWriter, msg_type: int, cc: int, param: int, payload: bytes = b""
) -> None:
    writer.write(pack_header(msg_type, cc, param, len(payload)))
    if payload:
        writer.write(payload)
    await writer.drain()


async def query_instrument(host: str, port: int) -> tuple:
    """Connect, run *IDN? and SYSTEM:COMMANDS?, return (idn, commands_json)."""
    reader, writer = await asyncio.open_connection(host, port)

    # INITIALIZE
    await send_msg(writer, 0, 0, (0x0100 << 16) | 1)
    await recv_msg(reader)  # INITIALIZE_RESPONSE

    async def scpi(cmd: str) -> str:
        await send_msg(writer, 7, 0, 1, cmd.encode("ascii"))  # DATA_END
        _, _, _, payload = await recv_msg(reader)
        return payload.decode("ascii")

    idn = await scpi("*IDN?")
    commands_json = await scpi("SYSTEM:COMMANDS?")

    writer.close()
    await writer.wait_closed()
    return idn, commands_json


def build_profile(idn: str, commands_json: str, host: str, port: int) -> dict:
    parts = idn.split(",")
    manufacturer = parts[0] if len(parts) > 0 else "Unknown"
    model = parts[1] if len(parts) > 1 else "Unknown"
    serial = parts[2] if len(parts) > 2 else "Unknown"
    firmware = parts[3] if len(parts) > 3 else "Unknown"

    data = json.loads(commands_json)

    profile: dict = {
        "identity": {
            "manufacturer": manufacturer,
            "model": model,
            "serial": serial,
            "firmware": firmware,
        },
        "connection": {
            "host": host,
            "port": port,
            "protocol": "hislip",
        },
        "commands": [],
    }

    for cmd in data.get("commands", []):
        entry: dict = {
            "scpi": cmd["scpi"],
            "type": cmd["type"],
            "timeout_ms": cmd.get("timeout_ms", 5000),
        }
        if cmd.get("group"):
            entry["group"] = cmd["group"]
        if cmd.get("description"):
            entry["description"] = cmd["description"]
        if cmd.get("params"):
            entry["params"] = cmd["params"]
        profile["commands"].append(entry)

    return profile


async def main_async(args: argparse.Namespace) -> None:
    print(f"Connecting to {args.host}:{args.port} ...", file=sys.stderr)
    try:
        idn, commands_json = await asyncio.wait_for(
            query_instrument(args.host, args.port),
            timeout=args.timeout,
        )
    except asyncio.TimeoutError:
        print(f"ERROR: Timed out connecting to {args.host}:{args.port}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"IDN: {idn}", file=sys.stderr)
    profile = build_profile(idn, commands_json, args.host, args.port)

    output = yaml.dump(profile, default_flow_style=False, sort_keys=False, allow_unicode=True)
    if args.out == "-":
        print(output)
    else:
        with open(args.out, "w") as f:
            f.write(output)
        print(f"Profile written to {args.out}", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="192.168.2.1", help="Instrument IP address")
    parser.add_argument("--port", type=int, default=4880, help="HiSLIP port")
    parser.add_argument("--out", default="profile.yaml", help="Output file (- for stdout)")
    parser.add_argument("--timeout", type=float, default=10.0, help="Connection timeout (s)")
    args = parser.parse_args()

    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
