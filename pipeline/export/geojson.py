from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import final, override

from models import Platform, Stop
from pipeline.export import BaseExporter

from .records import iter_locations


@final
class GeoJsonExporter(BaseExporter):
    filename = "stops.geojson"

    @override
    def export(self, stops: Sequence[Stop], destination: Path) -> None:
        features: list[dict[str, object]] = []
        for item, kind in iter_locations(stops):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [item.lon, item.lat]},
                    "properties": {
                        "id": item.id,
                        "name": item.name,
                        "type": kind,
                        "country": item.country,
                        "region": item.region,
                        "district": item.district,
                        "municipality": item.municipality,
                        "platform_code": item.platform_code
                        if isinstance(item, Platform)
                        else None,
                        "stop_id": item.stop_id if isinstance(item, Platform) else None,
                        "modes": [str(mode) for mode in item.modes or []],
                        "amenities": [str(amenity) for amenity in item.amenities or []],
                    },
                }
            )

        _ = destination.write_text(
            json.dumps(
                {"type": "FeatureCollection", "features": features}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
