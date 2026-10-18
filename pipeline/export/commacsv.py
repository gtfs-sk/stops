from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import final, override

from models import Stop
from pipeline.export import BaseCsvExporter


@final
class CsvExporter(BaseCsvExporter):
    filename = "stops.csv"
    headers: Sequence[str] = [
        "id",
        "name",
        "type",
        "lat",
        "lon",
        "platform_code",
        "parent_station_id",
        "modes",
        "amenities",
    ]

    @override
    def format_rows(self, stop: Stop) -> Iterator[Sequence[object]]:
        yield [
            stop.id,
            stop.name,
            "station",
            stop.lat,
            stop.lon,
            "",
            "",
            ";".join(str(m) for m in stop.modes),
            ";".join(str(a) for a in stop.amenities),
        ]

        for p in stop.platforms:
            yield [
                p.id,
                p.name,
                "platform",
                p.lat,
                p.lon,
                p.platform_code or "",
                p.parent_station_id,
                ";".join(str(m) for m in p.modes),
                ";".join(str(a) for a in p.amenities),
            ]
