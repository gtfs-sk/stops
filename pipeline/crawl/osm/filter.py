from __future__ import annotations

import hashlib
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from .download import CACHE_DIR

logger = logging.getLogger(__name__)

FILTER_VERSION = 1

FILTERS = (
    "nwr/highway=bus_stop,platform",
    "nwr/railway=station,halt,platform,platform_edge,tram_stop,stop",
    "nwr/public_transport=station,platform,stop_position",
    "nwr/amenity=bus_station,ferry_terminal",
    "nwr/aerialway=station",
    "nwr/ferry=yes",
    "nwr/funicular=yes",
    "nwr/light_rail=yes",
    "nwr/monorail=yes",
    "nwr/station=funicular,light_rail,monorail",
    "r/public_transport=stop_area",
)


def ensure_filtered_pbf(path: Path) -> Path:
    fingerprint = hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:10]
    destination = (
        CACHE_DIR
        / "filtered"
        / (
            f"{path.name.removesuffix('.osm.pbf')}-{fingerprint}-stops-v{FILTER_VERSION}.osm.pbf"
        )
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if (
        destination.is_file()
        and destination.stat().st_size > 0
        and destination.stat().st_mtime >= path.stat().st_mtime
    ):
        return destination

    if shutil.which("osmium") is None:
        raise RuntimeError(
            "Missing osmium-tool; on macOS run: brew install osmium-tool"
        )

    logger.info("Filtering OSM: %s", path)
    with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
        temporary = Path(tmp) / "stops.osm.pbf"
        command = [
            "osmium",
            "tags-filter",
            str(path),
            *FILTERS,
            "-t",
            "-o",
            str(temporary),
        ]
        _ = subprocess.run([*command, "--progress"], check=True)

        if not temporary.is_file() or temporary.stat().st_size == 0:
            raise ValueError("Filtered OSM PBF is empty")
        _ = temporary.replace(destination)

    return destination
