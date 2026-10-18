from __future__ import annotations

import logging
from typing import final, override

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Stop
from pipeline.patch import BasePatcher

from .config import UNMATCHED_CSV
from .matching import RegistryIndex, match_stops, stop_position, write_unmatched_csv
from .registry import load_registry

logger = logging.getLogger(__name__)


@final
class IscpIdPatcher(BasePatcher):
    name = "iscp_id"

    @override
    def patch(self, session: Session) -> None:
        records, provenance = load_registry()
        active = [r for r in records if not r.cancelled]
        logger.info(
            "ISCP registry: %d records (%d active) — %s",
            len(records),
            len(active),
            provenance,
        )

        stops = list(session.scalars(select(Stop)).all())
        assigned, with_candidates = match_stops(stops, RegistryIndex(active))

        by_id = {s.id: s for s in stops}
        quality_counts = {"exact": 0, "high": 0, "medium": 0}
        for stop_id, (rec, quality) in assigned.items():
            stop = by_id[stop_id]
            stop.iscp_id = rec.cislo
            for platform in stop.platforms:
                platform.iscp_id = rec.cislo
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
