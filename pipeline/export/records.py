from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Literal

from models import IscpStop, Platform, Stop, StopKind

LocationKind = Literal["station", "stop", "platform"]

# Public schema of an ISCP registry stop, shared by the CSV, GeoJSON and
# SQLite forms of the standalone register export.
ISCP_STOPS_HEADER: Sequence[str] = [
    "iscp_id",
    "municipality",
    "local_part",
    "locality",
    "name_ids",
    "name_mhd",
    "display_name",
    "district",
    "country",
    "transport_system",
    "validity",
    "is_cancelled",
    "source_file",
]

ISCP_PLATFORMS_HEADER: Sequence[str] = ["iscp_id", "position", "lat", "lon"]


def iter_locations(
    stops: Sequence[Stop],
) -> Iterator[tuple[Stop | Platform, LocationKind]]:
    for stop in stops:
        yield stop, "station" if stop.kind == StopKind.STATION else "stop"
        for platform in stop.platforms:
            yield platform, "platform"


def iscp_stop_values(record: IscpStop) -> list[object]:
    return [
        record.iscp_id,
        record.municipality,
        record.local_part or "",
        record.locality or "",
        record.name_ids or "",
        record.name_mhd or "",
        record.display_name,
        record.district or "",
        record.country or "",
        record.transport_system or "",
        record.validity or "",
        1 if record.is_cancelled else 0,
        record.source_file,
    ]
