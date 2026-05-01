from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional


class HardwareBackend(ABC):
    @abstractmethod
    def is_overlay_loaded(self) -> bool: ...

    @abstractmethod
    def get_ip(self, name: str) -> Any: ...

    @abstractmethod
    def get_ps_gpio(self, index: int) -> Any: ...

    @abstractmethod
    def allocate_dma_buffer(self, shape: Any, dtype: Any):
        """Return an async context manager yielding a DMA buffer."""
        ...


class PYNQBackend(HardwareBackend):
    """Concrete backend using pynq.Overlay. Imported lazily so tests run on x86."""

    def __init__(self, overlay_manager: Any) -> None:
        self._om = overlay_manager

    def is_overlay_loaded(self) -> bool:
        return self._om.is_loaded()

    def get_ip(self, name: str) -> Any:
        return self._om.get_ip(name)

    def get_ps_gpio(self, index: int) -> Any:
        import pynq  # type: ignore[import]
        return pynq.GPIO(pynq.GPIO.get_gpio_pin(index), "out")

    @asynccontextmanager
    async def allocate_dma_buffer(self, shape: Any, dtype: Any):
        import pynq  # type: ignore[import]
        buf = pynq.allocate(shape, dtype=dtype)
        try:
            yield buf
        finally:
            buf.freebuffer()


class MockBackend(HardwareBackend):
    """Test backend — never imports pynq; runs on x86 dev machines."""

    def __init__(self) -> None:
        self._overlay_loaded: bool = False
        self._ips: Dict[str, "MockIP"] = {}
        self._gpios: Dict[int, "MockGPIO"] = {}

    def load_mock_overlay(self, ip_names: List[str]) -> None:
        self._overlay_loaded = True
        self._ips = {name: MockIP(name) for name in ip_names}

    def unload_mock_overlay(self) -> None:
        self._overlay_loaded = False
        self._ips.clear()

    def is_overlay_loaded(self) -> bool:
        return self._overlay_loaded

    def get_ip(self, name: str) -> "MockIP":
        if name not in self._ips:
            raise KeyError(f"IP not found in mock overlay: {name!r}")
        return self._ips[name]

    def get_ps_gpio(self, index: int) -> "MockGPIO":
        if index not in self._gpios:
            self._gpios[index] = MockGPIO(index)
        return self._gpios[index]

    @asynccontextmanager
    async def allocate_dma_buffer(self, shape: Any, dtype: Any):
        import numpy as np  # type: ignore[import]
        buf = MockDMABuffer(np.zeros(shape, dtype=dtype))
        yield buf


class MockIP:
    def __init__(self, name: str) -> None:
        self.name = name
        self._registers: Dict[int, int] = {}

    def read(self, offset: int) -> int:
        return self._registers.get(offset, 0)

    def write(self, offset: int, value: int) -> None:
        self._registers[offset] = value


class MockGPIO:
    def __init__(self, index: int) -> None:
        self.index = index
        self._value: int = 0

    def write(self, value: int) -> None:
        self._value = value

    def read(self) -> int:
        return self._value


class MockDMABuffer:
    def __init__(self, array: Any) -> None:
        self._array = array

    def tolist(self) -> list:
        return self._array.tolist()

    def __len__(self) -> int:
        return len(self._array)

    def __getitem__(self, key: Any) -> Any:
        return self._array[key]

    def __setitem__(self, key: Any, value: Any) -> None:
        self._array[key] = value
