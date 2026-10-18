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

    def wheelchair_boarding(self, item: Stop | Platform) -> int:
        return 1 if Amenity.WHEELCHAIR in item.amenities else 0

    @override
    def format_rows(self, stop: Stop) -> Iterator[Sequence[object]]:
        if stop.is_station:
            yield [
                stop.id,
                stop.name,
                stop.lat,
                stop.lon,
                1,
                "",
                "",
                self.wheelchair_boarding(stop),
            ]
        elif not stop.platforms:
            yield [
                stop.id,
                stop.name,
                stop.lat,
                stop.lon,
                0,
                "",
                "",
                self.wheelchair_boarding(stop),
            ]

        for platform in sorted(stop.platforms, key=lambda item: item.id):
            yield [
                platform.id,
                platform.name,
                platform.lat,
                platform.lon,
                0,
                stop.id if stop.is_station else "",
                platform.platform_code or "",
                self.wheelchair_boarding(platform),
            ]
