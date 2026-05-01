from __future__ import annotations

import threading
from collections import deque
from typing import Deque, Tuple

ERR_UNDEFINED_COMMAND = -100
ERR_INVALID_PARAM = -200
ERR_PARAM_OUT_OF_RANGE = -222
ERR_HARDWARE = -300

_lock: threading.Lock = threading.Lock()
_queue: Deque[Tuple[int, str]] = deque(maxlen=20)


def push_error(code: int, message: str) -> None:
    with _lock:
        _queue.append((code, message))


def pop_error() -> str:
    with _lock:
        if not _queue:
            return '0,"No error"'
        code, msg = _queue.popleft()
        return f'{code},"{msg}"'


def error_count() -> int:
    with _lock:
        return len(_queue)


def clear_errors() -> None:
    with _lock:
        _queue.clear()
