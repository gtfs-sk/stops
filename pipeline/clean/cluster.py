from __future__ import annotations

import logging
from collections import defaultdict
from typing import final, override

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from tqdm.auto import tqdm

from models import Platform, Stop, StopKind
from models.transport_group import TransportGroup, transport_group
from pipeline.clean import BaseCleaner, distance_meters

logger = logging.getLogger(__name__)


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
    max_distance_meters = 500.0

    @override
    def clean(self, session: Session) -> None:
        before_platform_ids = set(session.scalars(select(Platform.id)))
        stops = session.scalars(
            select(Stop).options(selectinload(Stop.platforms))
        ).all()

        by_name: dict[tuple[str, str | None, str, str], list[Stop]] = defaultdict(list)
        for stop in stops:
            group = transport_group(stop.modes)
            key = (
                stop.country,
                stop.region,
                stop.name.casefold().strip(),
                group.value,
            )

            if group == TransportGroup.MIXED:
                logger.warning("Skipping mixed-mode stop %s: %s", stop.id, stop.modes)
                continue

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
                        distance_meters(
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

        session.flush()
        after_platform_ids = set(session.scalars(select(Platform.id)))
        if before_platform_ids != after_platform_ids:
            lost = sorted(before_platform_ids - after_platform_ids)
            extra = sorted(after_platform_ids - before_platform_ids)
            raise RuntimeError(
                f"Stop clustering changed platform identities: "
                f"lost={lost[:15]!r}, added={extra[:15]!r}"
            )

        logger.info(
            "Merged %d logical stops; preserved %d platforms",
            merged_count,
            len(before_platform_ids),
        )
