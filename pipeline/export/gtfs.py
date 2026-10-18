from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import final, override

from models import Stop
from models.amenity import Amenity
from models.stop import Platform
from pipeline.export import BaseCsvExporter


@final
class GtfsStopsExporter(BaseCsvExporter):
    filename = "stops.txt"
    headers: Sequence[str] = [
        "stop_id",
        "stop_name",
        "stop_lat",
        "stop_lon",
        "location_type",
        "parent_station",
        "platform_code",
        "wheelchair_boarding",
    ]

    def is_wheelchair_accessible(self, item: Stop | Platform) -> int:
        return 1 if Amenity.WHEELCHAIR in item.amenities else 0

    @override
    def format_rows(self, stop: Stop) -> Iterator[Sequence[object]]:
        yield [
            stop.id,
            stop.name,
            stop.lat,
            stop.lon,
            stop.stop_type.value,
            "",
            "",
            self.is_wheelchair_accessible(stop),
        ]

        for p in stop.platforms:
            yield [
                p.id,
                p.name,
                p.lat,
                p.lon,
                p.stop_type.value,
                p.parent_station_id,
                p.platform_code or "",
                self.is_wheelchair_accessible(p),
            ]
