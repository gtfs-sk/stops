from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn, final

import osmium
from osmium.osm import Node, Relation, TagList, Way
from shapely.geometry import LineString, Polygon
from tqdm.auto import tqdm

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OsmFeature:
    original_id: str
    tags: dict[str, str]
    lat: float
    lon: float
    stop_area_name: str | None = None

    @property
    def id_suffix(self) -> str:
        return self.original_id.replace("/", "-")


def is_station(tags: TagList | dict[str, str]) -> bool:
    return (
        tags.get("railway") in {"station", "halt"}
        or tags.get("public_transport") == "station"
        or tags.get("amenity") in {"bus_station", "ferry_terminal"}
        or tags.get("aerialway") == "station"
    )


def is_platform(tags: TagList | dict[str, str]) -> bool:
    return (
        tags.get("highway") in {"bus_stop", "platform"}
        or tags.get("public_transport") == "platform"
        or tags.get("railway") in {"platform", "platform_edge", "tram_stop"}
    )


def is_stop_position(tags: TagList | dict[str, str]) -> bool:
    return (
        tags.get("public_transport") == "stop_position" or tags.get("railway") == "stop"
    )


def _representative_point(coords: list[tuple[float, float]]) -> tuple[float, float]:
    """Accept (longitude, latitude), return (latitude, longitude)."""
    if len(coords) == 1:
        return coords[0][1], coords[0][0]
    if len(coords) >= 4 and coords[0] == coords[-1]:
        polygon = Polygon(coords)
        if polygon.is_valid and not polygon.is_empty and polygon.area > 0:
            point = polygon.representative_point()
            return point.y, point.x
    line = LineString(coords)
    point = line.interpolate(0.5, normalized=True)
    return point.y, point.x


@final
class _StopHandler(osmium.SimpleHandler):
    def __init__(self, progress: tqdm[NoReturn]) -> None:
        super().__init__()
        self.features: dict[str, OsmFeature] = {}
        self.area_names: dict[str, str] = {}
        self.station_relations: list[tuple[str, dict[str, str], list[str]]] = []
        self.progress = progress
        self.processed = 0

    def _tick(self) -> None:
        self.processed += 1
        if self.processed % 50_000 == 0:
            _ = self.progress.update(50_000)
            self.progress.set_postfix(features=len(self.features))  # pyright: ignore[reportUnknownMemberType]

    def node(self, obj: Node) -> None:
        self._tick()
        if not (
            is_station(obj.tags) or is_platform(obj.tags) or is_stop_position(obj.tags)
        ):
            return
        if not obj.location.valid():
            return
        key = f"node/{obj.id}"
        self.features[key] = OsmFeature(
            key, dict(obj.tags), obj.location.lat, obj.location.lon
        )

    def way(self, obj: Way) -> None:
        self._tick()
        if not (
            is_station(obj.tags) or is_platform(obj.tags) or is_stop_position(obj.tags)
        ):
            return
        coords = [
            (n.location.lon, n.location.lat) for n in obj.nodes if n.location.valid()
        ]
        if not coords:
            return
        lat, lon = _representative_point(coords)
        key = f"way/{obj.id}"
        self.features[key] = OsmFeature(key, dict(obj.tags), lat, lon)

    def relation(self, obj: Relation) -> None:
        self._tick()
        is_area = obj.tags.get("public_transport") == "stop_area"
        is_station_relation = is_station(obj.tags)
        if not is_area and not is_station_relation:
            return
        members = [
            f"{'node' if m.type == 'n' else 'way'}/{m.ref}"
            for m in obj.members
            if m.type in {"n", "w"}
        ]
        if is_area and (name := obj.tags.get("name")):
            for member in members:
                _ = self.area_names.setdefault(member, name)
        if is_station_relation:
            self.station_relations.append(
                (f"relation/{obj.id}", dict(obj.tags), members)
            )


def read_pbf(path: Path) -> list[OsmFeature]:
    with tqdm(
        desc="Reading OSM", unit="obj", unit_scale=True, dynamic_ncols=True
    ) as progress:
        handler = _StopHandler(progress)
        handler.apply_file(str(path), locations=True)
        _ = progress.update(handler.processed % 50_000)

    for original_id, name in handler.area_names.items():
        if feature := handler.features.get(original_id):
            feature.stop_area_name = name

    for original_id, tags, member_ids in tqdm(
        handler.station_relations,
        desc="Resolving station relations",
        unit="relation",
        dynamic_ncols=True,
    ):
        members = [handler.features[m] for m in member_ids if m in handler.features]
        if not members:
            continue
        handler.features[original_id] = OsmFeature(
            original_id,
            tags,
            sum(m.lat for m in members) / len(members),
            sum(m.lon for m in members) / len(members),
        )

    logger.info("Read %s OSM stop features", len(handler.features))
    return list(handler.features.values())
