from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any


@asynccontextmanager
async def pynq_dma_buffer(shape: Any, dtype: Any):
    """
    Async context manager that allocates a pynq DMA buffer and frees it on exit.

    Usage::

        async with pynq_dma_buffer((1024,), "uint16") as buf:
            dma_0.recvchannel.transfer(buf)
            await dma_0.recvchannel.wait_async()
            return respond_float_array(buf.tolist())

    Do not call pynq.allocate() directly in handler code; use this wrapper so
    buffer lifetime is always paired with freebuffer().
    """
    import pynq  # type: ignore[import]

    buf = pynq.allocate(shape, dtype=dtype)
    try:
        yield buf
    finally:
        buf.freebuffer()
