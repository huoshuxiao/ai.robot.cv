from __future__ import annotations

import random
import time
from collections.abc import Callable


class RateLimiter:
    def __init__(
        self,
        min_seconds: float,
        max_seconds: float,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ):
        self.min_seconds = max(0.0, float(min_seconds))
        self.max_seconds = max(self.min_seconds, float(max_seconds))
        self._sleeper = sleeper
        self._rng = rng or random.Random()
        self.count = 0

    def before_send(self) -> None:
        if self.count > 0:
            self._sleeper(self._rng.uniform(self.min_seconds, self.max_seconds))
        self.count += 1
