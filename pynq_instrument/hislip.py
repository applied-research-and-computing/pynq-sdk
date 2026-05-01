from __future__ import annotations

import asyncio
import struct

HEADER_SIZE = 16
MAX_PAYLOAD = 8 * 1024

# Message types
MSG_INITIALIZE = 0
MSG_INITIALIZE_RESPONSE = 1
MSG_FATAL_ERROR = 2
MSG_ERROR = 3
MSG_ASYNC_LOCK = 4
MSG_ASYNC_LOCK_RESPONSE = 5
MSG_DATA = 6
MSG_DATA_END = 7
MSG_DEVICE_CLEAR_COMPLETE = 8
MSG_DEVICE_CLEAR_ACK = 9
MSG_ASYNC_REMOTE_LOCAL_CTRL = 10
MSG_ASYNC_REMOTE_LOCAL_RESP = 11
MSG_TRIGGER = 12
MSG_INTERRUPTED = 13
MSG_ASYNC_INTERRUPTED = 14
MSG_ASYNC_MAX_MSG_SIZE = 15
MSG_ASYNC_MAX_MSG_SIZE_RESP = 16
MSG_ASYNC_INITIALIZE = 17
MSG_ASYNC_INITIALIZE_RESP = 18
MSG_ASYNC_DEVICE_CLEAR = 19
MSG_ASYNC_SERVICE_REQUEST = 20
MSG_ASYNC_STATUS_QUERY = 21
MSG_ASYNC_STATUS_RESPONSE = 22
MSG_ASYNC_DEV_CLEAR_ACK = 23
MSG_ASYNC_LOCK_INFO = 24
MSG_ASYNC_LOCK_INFO_RESP = 25

# Error codes
ERR_UNIDENTIFIED = 0
ERR_POORLY_FORMED = 1
ERR_ATTEMPT_TO_USE_CONN = 2
ERR_INVALID_INIT_SEQ = 3
ERR_SERVER_REFUSED = 4

# Header layout: "HS" | msg_type (1) | control_code (1) | msg_param (4) | payload_len (8)
_HEADER_FMT = ">2sBBIQ"


def pack_header(msg_type: int, control_code: int, msg_param: int, payload_len: int) -> bytes:
    return struct.pack(_HEADER_FMT, b"HS", msg_type, control_code, msg_param, payload_len)


def unpack_header(data: bytes) -> tuple:
    prologue, msg_type, control_code, msg_param, payload_len = struct.unpack(_HEADER_FMT, data)
    if prologue != b"HS":
        raise ValueError(f"Invalid HiSLIP prologue: {prologue!r}")
    return msg_type, control_code, msg_param, payload_len


async def recv_message(reader: asyncio.StreamReader) -> tuple:
    """Read one complete HiSLIP message. Returns (msg_type, control_code, msg_param, payload)."""
    header_data = await reader.readexactly(HEADER_SIZE)
    msg_type, control_code, msg_param, payload_len = unpack_header(header_data)
    if payload_len > MAX_PAYLOAD:
        raise ValueError(f"Payload too large: {payload_len} > {MAX_PAYLOAD}")
    payload = b""
    if payload_len > 0:
        payload = await reader.readexactly(int(payload_len))
    return msg_type, control_code, msg_param, payload


async def send_message(
    writer: asyncio.StreamWriter,
    msg_type: int,
    control_code: int,
    msg_param: int,
    payload: bytes = b"",
) -> None:
    frame = pack_header(msg_type, control_code, msg_param, len(payload))
    if payload:
        frame += payload
    writer.write(frame)
    await writer.drain()
