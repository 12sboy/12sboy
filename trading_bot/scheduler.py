from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass
class HourlyGate:
    last_hour_key: str | None = None

    def should_fire(self, now: datetime) -> bool:
        key = now.strftime("%Y-%m-%d-%H")
        if key == self.last_hour_key:
            return False
        self.last_hour_key = key
        return True
