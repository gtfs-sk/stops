from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import ClassVar, Literal, final, override

from models import Stop
from models.stop import Platform
from pipeline.export import BaseCsvExporter
from pipeline.export.records import iter_locations

HEADERS: Sequence[str] = [
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
    "modes",
    "amenities",
    "platform_code",
    "platform_parent_stop_id",
]


class FilteredCsvExporter(BaseCsvExporter):
    headers: Sequence[str] = HEADERS
    include: ClassVar[Literal["stops", "platforms", "both"]] = "both"

    @override
    def format_rows(self, stop: Stop) -> Iterator[Sequence[object]]:
        for item, kind in iter_locations([stop]):
            if kind == "stop" and self.include == "platforms":
                continue

            if kind == "platform" and self.include == "stops":
                continue

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
                ";".join(map(str, item.modes or [])),
                ";".join(map(str, item.amenities or [])),
                (item.platform_code or "") if isinstance(item, Platform) else "",
                item.stop_id if isinstance(item, Platform) else "",
            ]


@final
class StopsCsvExporter(FilteredCsvExporter):
    filename = "stops.csv"
    headers = tuple(
        header
        for header in HEADERS
        if header not in {"platform_code", "platform_parent_stop_id"}
    )
    include = "stops"


@final
class PlatformsCsvExporter(FilteredCsvExporter):
    filename = "platforms.csv"
    include = "platforms"


@final
class CombinedCsvExporter(FilteredCsvExporter):
    filename = "stops-and-platforms.csv"
    include = "both"
