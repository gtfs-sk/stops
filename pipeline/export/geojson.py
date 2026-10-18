from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import final, override

from models import Stop
from pipeline.export import BaseExporter


@final
class GeoJsonExporter(BaseExporter):
    filename = "stops.geojson"

    @override
    def export(
        self,
        stops: Sequence[Stop],
        destination: Path,
    ) -> None:
        features: list[dict[str, object]] = []

        for stop in stops:
            if stop.platforms:
                for p in stop.platforms:
                    features.append(
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Point",
                                "coordinates": [p.lon, p.lat],
                            },
                            "properties": {
                                "id": p.id,
                                "name": p.name,
                                "platform": p.platform_code,
                                "parent_id": p.parent_station_id,
                                "modes": [str(m) for m in p.modes],
                                "amenities": [str(a) for a in p.amenities],
                            },
                        }
                    )
            else:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {
                            "type": "Point",
                            "coordinates": [stop.lon, stop.lat],
                        },
                        "properties": {
                            "id": stop.id,
                            "name": stop.name,
                            "platform": None,
                            "parent_id": None,
                            "modes": [str(m) for m in stop.modes],
                        },
                    }
                )

        geojson = {
            "type": "FeatureCollection",
            "features": features,
        }

        _ = destination.write_text(
            json.dumps(geojson, ensure_ascii=False), encoding="utf-8"
        )
