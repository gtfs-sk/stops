from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from models import Base
from pipeline.clean import run_cleaners
from pipeline.clean.cluster import StopClusterCleaner
from pipeline.clean.platforms import PlatformDeduplicationCleaner
from pipeline.crawl import run_crawlers
from pipeline.crawl.osm import OsmCrawler
from pipeline.export import run_exporters
from pipeline.export.commasv.curated import (
    CombinedCsvExporter,
    PlatformsCsvExporter,
    StopsCsvExporter,
)
from pipeline.export.commasv.full import (
    FullCombinedCsvExporter,
    FullPlatformsCsvExporter,
    FullStopsCsvExporter,
)
from pipeline.export.geojson import GeoJsonExporter
from pipeline.export.gtfs import GtfsStopsExporter
from pipeline.export.sqlite import SqliteExporter
from pipeline.patch import run_patchers
from pipeline.patch.iscp import IscpIdPatcher
from pipeline.patch.names import StopNamePatcher

DATA_DIR = Path("data")
DB_PATH = DATA_DIR / "stops.sqlite"
OUTPUT_DIR = DATA_DIR / "export"

CRAWLERS = [OsmCrawler]
CLEANERS = [StopClusterCleaner, PlatformDeduplicationCleaner]
PATCHERS = [IscpIdPatcher, StopNamePatcher]
EXPORTERS = [
    GtfsStopsExporter,
    GeoJsonExporter,
    StopsCsvExporter,
    PlatformsCsvExporter,
    CombinedCsvExporter,
    FullStopsCsvExporter,
    FullPlatformsCsvExporter,
    FullCombinedCsvExporter,
    SqliteExporter,
]

logging.basicConfig(level=os.environ.get("LOGLEVEL", "INFO").upper())


def build() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    engine = create_engine(f"sqlite:///{DB_PATH}")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        run_crawlers(session, CRAWLERS)
        run_cleaners(session, CLEANERS)
        run_patchers(session, PATCHERS)
        session.commit()
        run_exporters(session, EXPORTERS, OUTPUT_DIR)


if __name__ == "__main__":
    build()
