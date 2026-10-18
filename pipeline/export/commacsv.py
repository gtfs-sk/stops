from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import final, override

from models import Platform, Stop
from pipeline.export import BaseCsvExporter

from .records import iter_locations


@final
class CsvExporter(BaseCsvExporter):
    filename = "stops.csv"
    headers: Sequence[str] = [
        "id",
        "iscp_id",
        "name",
        "type",
        "lat",
        "lon",
        "country",
        "region",
        "district",
        "municipality",
        "platform_code",
        "stop_id",
        "modes",
        "amenities",
    ]

    @override
    def format_rows(self, stop: Stop) -> Iterator[Sequence[object]]:
        for item, kind in iter_locations([stop]):
            yield [
                item.id,
                item.iscp_id,
                item.name,
                kind,
                item.lat,
                item.lon,
                item.country,
                item.region or "",
                item.district or "",
                item.municipality or "",
                (item.platform_code or "") if isinstance(item, Platform) else "",
                item.stop_id if isinstance(item, Platform) else "",
                ";".join(map(str, item.modes or [])),
                ";".join(map(str, item.amenities or [])),
            ]
