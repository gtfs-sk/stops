from __future__ import annotations

import logging
from typing import final, override

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from models import IscpMatch, IscpStop, Stop
from pipeline.patch import BasePatcher

from .config import UNMATCHED_CSV
from .matching import RegistryIndex, match_stops, stop_position, write_unmatched_csv

logger = logging.getLogger(__name__)


@final
class IscpIdPatcher(BasePatcher):
    """Link canonical stops to ISCP registry records.

    The registry itself is loaded by the ISCP crawler into iscp_stops /
    iscp_platforms. This patcher only records the match (in iscp_matches,
    with its quality and distance) and mirrors the matched id onto
    Stop.iscp_id for convenience. Platforms do not get their own iscp_id:
    the match is a property of the stop, not of each platform.
    """

    name = "iscp_id"

    @override
    def patch(self, session: Session) -> None:
        records = list(session.scalars(select(IscpStop)).all())
        active = [r for r in records if not r.is_cancelled]
        logger.info(
            "ISCP registry: %d records (%d active)",
            len(records),
            len(active),
        )

        stops = list(session.scalars(select(Stop)).all())

        # Make the patcher idempotent: drop previous links first.
        session.execute(delete(IscpMatch))
        for stop in stops:
            stop.iscp_id = None
            for platform in stop.platforms:
                platform.iscp_id = None
        session.flush()

        assigned, with_candidates = match_stops(stops, RegistryIndex(active))

        by_id = {s.id: s for s in stops}
        quality_counts = {"exact": 0, "high": 0, "medium": 0}
        for stop_id, (rec, quality, distance) in assigned.items():
            stop = by_id[stop_id]
            stop.iscp_id = rec.iscp_id
            session.add(
                IscpMatch(
                    stop_id=stop_id,
                    iscp_id=rec.iscp_id,
                    quality=quality,
                    distance_m=distance,
                )
            )
            quality_counts[quality] += 1

        positions = {s.id: stop_position(s) for s in stops}
        unmatched_count = write_unmatched_csv(
            UNMATCHED_CSV, stops, positions, assigned, with_candidates
        )

        logger.info(
            "ISCP matching: %d/%d stops matched (exact=%d, high=%d, medium=%d), "
            "%d unmatched -> %s",
            len(assigned),
            len(stops),
            quality_counts["exact"],
            quality_counts["high"],
            quality_counts["medium"],
            unmatched_count,
            UNMATCHED_CSV,
        )
