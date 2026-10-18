from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Sequence

from sqlalchemy.orm import Session


def distance_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = p2 - p1, math.radians(lon2 - lon1)
    hav = (
        math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.atan2(math.sqrt(hav), math.sqrt(max(0.0, 1 - hav)))


class BaseCleaner(ABC):
    name: str

    @abstractmethod
    def clean(self, session: Session) -> None:
        pass


def run_cleaners(session: Session, cleaners: Sequence[type[BaseCleaner]]) -> None:
    for cleaner_cls in cleaners:
        cleaner = cleaner_cls()
        cleaner.clean(session)
        session.commit()
