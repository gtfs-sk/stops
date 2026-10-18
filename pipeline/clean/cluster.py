from __future__ import annotations

import logging
import math
from collections import defaultdict
from typing import final, override

from sqlalchemy import select
from sqlalchemy.orm import Session
from tqdm.auto import tqdm

from models import Stop, StopKind
from pipeline.clean import BaseCleaner

logger = logging.getLogger(__name__)


def _distance_meters(a: tuple[float, float], b: tuple[float, float]) -> float:
    lat1, lon1 = a
    lat2, lon2 = b
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dlat, dlon = p2 - p1, math.radians(lon2 - lon1)
    hav = (
        math.sin(dlat / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlon / 2) ** 2
    )
    return 2 * r * math.atan2(math.sqrt(hav), math.sqrt(max(0.0, 1 - hav)))


def _position(stop: Stop) -> tuple[float, float]:
    if stop.kind == StopKind.STATION:
        if stop.lat is None or stop.lon is None:
            raise ValueError(f"Station {stop.id} must have coordinates")
        return stop.lat, stop.lon

    if not stop.platforms:
        raise ValueError(f"Logical stop {stop.id} has no boarding platforms")

    return (
        sum(platform.lat for platform in stop.platforms) / len(stop.platforms),
        sum(platform.lon for platform in stop.platforms) / len(stop.platforms),
    )


@final
class StopClusterCleaner(BaseCleaner):
    name = "stop_cluster"
    max_distance_meters = 250.0

    @override
    def clean(self, session: Session) -> None:
        stops = session.scalars(select(Stop)).all()

        by_name: dict[tuple[str, str | None, str | None, str], list[Stop]] = (
            defaultdict(list)
        )
        for stop in stops:
            key = (
                stop.country,
                stop.district,
                stop.municipality,
                stop.name.casefold().strip(),
            )
            by_name[key].append(stop)

        merged_count = 0
        for group in tqdm(by_name.values(), desc="Clustering stops", unit="group"):
            if len(group) <= 1:
                continue

            group.sort(key=lambda stop: (stop.kind != StopKind.STATION, stop.id))
            original_positions = {stop.id: _position(stop) for stop in group}
            clusters: list[list[Stop]] = []

            for stop in group:
                for cluster in clusters:
                    if all(
                        _distance_meters(
                            original_positions[stop.id], original_positions[member.id]
                        )
                        <= self.max_distance_meters
                        for member in cluster
                    ):
                        cluster.append(stop)
                        break
                else:
                    clusters.append([stop])

            for cluster in clusters:
                master, *others = cluster
                for other in others:
                    master.modes = list(dict.fromkeys([*master.modes, *other.modes]))
                    master.amenities = list(
                        dict.fromkeys([*master.amenities, *other.amenities])
                    )
                    for platform in list(other.platforms):
                        platform.parent = master
                    session.delete(other)
                    merged_count += 1

        logger.info("Merged %d logical stops", merged_count)
