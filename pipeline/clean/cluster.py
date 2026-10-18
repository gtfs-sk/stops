from __future__ import annotations

import math
from collections import defaultdict
from typing import final, override

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Platform, Stop
from pipeline.clean import BaseCleaner


def _distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


@final
class StopClusterCleaner(BaseCleaner):
    name = "stop_cluster"
    max_distance_meters = 150.0

    @override
    def clean(self, session: Session) -> None:
        stops = session.scalars(select(Stop)).all()

        by_name: dict[str, list[Stop]] = defaultdict(list)
        for stop in stops:
            by_name[stop.name].append(stop)

        for group in by_name.values():
            if len(group) < 2:
                continue

            merged: set[str] = set()

            for i, master in enumerate(group):
                if master.id in merged:
                    continue

                for other in group[i + 1 :]:
                    if other.id in merged:
                        continue

                    dist = _distance_meters(
                        master.lat, master.lon, other.lat, other.lon
                    )

                    if dist <= self.max_distance_meters:
                        platforms = session.scalars(
                            select(Platform).where(
                                Platform.parent_station_id == other.id
                            )
                        ).all()

                        for platform in platforms:
                            platform.parent = master

                        master.is_station = master.is_station or other.is_station
                        master.modes = list(
                            dict.fromkeys([*master.modes, *other.modes])
                        )
                        master.amenities = list(
                            dict.fromkeys([*master.amenities, *other.amenities])
                        )

                        session.delete(other)
                        merged.add(other.id)
