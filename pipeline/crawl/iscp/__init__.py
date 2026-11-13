from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import final, override

from models import Base, IscpPlatform, IscpStop
from pipeline.crawl import BaseCrawler

from .registry import load_registry

logger = logging.getLogger(__name__)


@final
class IscpCrawler(BaseCrawler):
    """Download the ISCP registry into its own database tables.

    The registry is a source dataset in its own right: records are stored
    faithfully in iscp_stops/iscp_platforms and are only linked to the
    canonical stops later, by the ISCP matching patcher.
    """

    name = "iscp"

    @override
    def fetch(self) -> Iterator[Base]:
        records, provenance = load_registry()
        logger.info(
            "ISCP registry: %d records (%s)",
            len(records),
            provenance,
        )

        seen: set[str] = set()
        for rec in records:
            if rec.iscp_id in seen:
                logger.warning("ISCP registry: duplicate id %s, skipping", rec.iscp_id)
                continue
            seen.add(rec.iscp_id)
            yield IscpStop(
                iscp_id=rec.iscp_id,
                municipality=rec.municipality,
                local_part=rec.local_part or None,
                locality=rec.locality or None,
                name_ids=rec.name_ids or None,
                name_mhd=rec.name_mhd or None,
                display_name=rec.display_name,
                district=rec.district or None,
                country=rec.country or None,
                transport_system=rec.transport_system or None,
                validity=rec.validity or None,
                is_cancelled=rec.is_cancelled,
                source_file=rec.source_file,
                raw_fields=rec.raw_fields,
                platforms=[
                    IscpPlatform(
                        iscp_id=rec.iscp_id,
                        position=position,
                        lat=lat,
                        lon=lon,
                    )
                    for position, (lat, lon) in enumerate(rec.coordinates, start=1)
                ],
            )
