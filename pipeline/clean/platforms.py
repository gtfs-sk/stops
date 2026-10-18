from __future__ import annotations

import logging
from collections import defaultdict
from typing import final, override

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from tqdm.auto import tqdm

from models import Platform, Stop
from models.transport_mode import TransportMode
from pipeline.clean import BaseCleaner, distance_meters

logger = logging.getLogger(__name__)

BUS_MODES = frozenset({TransportMode.LOCAL_BUS, TransportMode.REGIONAL_BUS})


def get_platform_code(platform: Platform) -> str:
    return (platform.platform_code or "").strip().casefold()


def mode_families(platform: Platform) -> set[str]:
    return {"bus" if str(mode) in BUS_MODES else str(mode) for mode in platform.modes}


def compatible_modes(left: Platform, right: Platform) -> bool:
    left_modes = mode_families(left)
    right_modes = mode_families(right)
    return not left_modes or not right_modes or not left_modes.isdisjoint(right_modes)


def quality(platform: Platform) -> tuple[int, int, int, int, int, int]:
    """
    Prioritize a passenger platform with useful transport classification.

    Prefer explicit rail/tram/other transport types over a generic bus guess;
    then the amount of available information. OSM ways win only ties.
    """

    mode_names = {str(mode) for mode in platform.modes}
    specific_modes = mode_names - BUS_MODES
    return (
        int(not platform.id.startswith("osm-p-fallback-")),
        int(bool(specific_modes)),
        int(bool(mode_names)),
        len(mode_names),
        len(platform.amenities),
        int(platform.id.startswith("osm-p-way-")),
    )


def rank_key(platform: Platform) -> tuple[int, int, int, int, int, int, str]:
    """Ascending sort: best record first, deterministic ID tie-break."""

    complete, specific, classified, modes, amenities, way = quality(platform)
    return (-complete, -specific, -classified, -modes, -amenities, -way, platform.id)


def nearby(left: Platform, right: Platform, limit: float) -> bool:
    return distance_meters((left.lat, left.lon), (right.lat, right.lon)) <= limit


@final
class PlatformDeduplicationCleaner(BaseCleaner):
    name = "platform_deduplication"
    max_distance_meters = 25.0

    @override
    def clean(self, session: Session) -> None:
        stops = session.scalars(
            select(Stop).options(selectinload(Stop.platforms))
        ).all()

        removed_count = 0
        affected_stops = 0

        for stop in tqdm(stops, desc="Deduplicating platforms", unit="stop"):
            by_code: dict[str, list[Platform]] = defaultdict(list)
            for platform in stop.platforms:
                code = get_platform_code(platform)
                if code:
                    by_code[code].append(platform)

            stop_removed = 0
            for candidates in by_code.values():
                if len(candidates) < 2:
                    continue

                clusters: list[list[Platform]] = []
                for platform in sorted(candidates, key=rank_key):
                    for cluster in clusters:
                        if all(
                            nearby(platform, other, self.max_distance_meters)
                            and compatible_modes(platform, other)
                            for other in cluster
                        ):
                            cluster.append(platform)
                            break
                    else:
                        clusters.append([platform])

                for cluster in clusters:
                    if len(cluster) < 2:
                        continue

                    master, *duplicates = sorted(cluster, key=rank_key)
                    for duplicate in duplicates:
                        master.modes = list(
                            dict.fromkeys(
                                [
                                    *master.modes,
                                    *duplicate.modes,
                                ]
                            )
                        )
                        master.amenities = list(
                            dict.fromkeys(
                                [
                                    *master.amenities,
                                    *duplicate.amenities,
                                ]
                            )
                        )
                        session.delete(duplicate)
                        stop_removed += 1
                        logger.debug(
                            "Merged duplicate platform %s into %s (%s / %s)",
                            duplicate.id,
                            master.id,
                            stop.name,
                            master.platform_code,
                        )

            if stop_removed:
                affected_stops += 1
                removed_count += stop_removed

        logger.info(
            "Deduplicated %d platforms across %d stops",
            removed_count,
            affected_stops,
        )
