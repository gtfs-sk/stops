from __future__ import annotations

import logging
from collections.abc import Iterator
from time import perf_counter
from typing import final, override

from models import Platform, Stop
from pipeline.crawl import BaseCrawler

from .admin import AdministrativeLocator
from .download import ensure_pbf
from .filter import ensure_filtered_pbf
from .mapper import map_features
from .reader import read_pbf

logger = logging.getLogger(__name__)


@final
class OsmCrawler(BaseCrawler):
    name = "osm"

    @override
    def fetch(self) -> Iterator[Stop | Platform]:
        start = perf_counter()
        pbf = ensure_pbf()
        filtered = ensure_filtered_pbf(pbf)
        logger.info("OSM download/filter: %.1fs", perf_counter() - start)

        admin = AdministrativeLocator.load()
        logger.info("Admin index: %.1fs", perf_counter() - start)

        features = read_pbf(filtered)
        logger.info(
            "OSM read (%d features): %.1fs", len(features), perf_counter() - start
        )

        yield from map_features(features, admin)
        logger.info("OSM total: %.1fs", perf_counter() - start)
