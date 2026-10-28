from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Optional, Tuple

_DEFAULT_WINDOW_SECONDS = 5.0


class SpeedTracker:
    def __init__(self, window_seconds: float = _DEFAULT_WINDOW_SECONDS):
        self._window = window_seconds
        self._samples: Deque[Tuple[float, int]] = deque()
        self._lock = threading.Lock()

    def record(self, total_bytes: int, now: Optional[float] = None) -> None:
        moment = time.monotonic() if now is None else now
        with self._lock:
            self._samples.append((moment, total_bytes))
            cutoff = moment - self._window
            while len(self._samples) > 2 and self._samples[1][0] < cutoff:
                self._samples.popleft()

    @property
    def bytes_per_second(self) -> float:
        with self._lock:
            if len(self._samples) < 2:
                return 0.0
            first, last = self._samples[0], self._samples[-1]
        seconds = last[0] - first[0]
        return (last[1] - first[1]) / seconds if seconds > 0 else 0.0

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()
