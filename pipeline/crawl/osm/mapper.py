from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from collections.abc import Iterator

from tqdm.auto import tqdm

from models import Platform, Stop, StopKind, TransportMode
from models.amenity import Amenity
from pipeline.clean import distance_meters

from .admin import AdministrativeArea, AdministrativeLocator
from .reader import OsmFeature, is_platform, is_station, is_stop_position

logger = logging.getLogger(__name__)

CELL_SIZE = 0.01
STATION_SEARCH_METERS = 300
PLATFORM_SEARCH_METERS = 250
POSITION_SEARCH_METERS = 60


def distance(a: OsmFeature, b: OsmFeature) -> float:
    lat1, lat2 = math.radians(a.lat), math.radians(b.lat)
    dlat = lat2 - lat1
    dlon = math.radians(b.lon - a.lon)
    x = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return 2 * 6_371_000 * math.asin(min(1.0, math.sqrt(x)))


def cell(item: OsmFeature) -> tuple[int, int]:
    return math.floor(item.lat / CELL_SIZE), math.floor(item.lon / CELL_SIZE)


def route_refs(tags: dict[str, str]) -> list[str]:
    return list(
        dict.fromkeys(
            ref.strip() for ref in tags.get("route_ref", "").split(";") if ref.strip()
        )
    )


def bus_candidate(tags: dict[str, str]) -> bool:
    return (
        tags.get("bus") == "yes"
        or tags.get("highway") == "bus_stop"
        or tags.get("amenity") == "bus_station"
    )


def modes(tags: dict[str, str]) -> list[TransportMode]:
    result: list[TransportMode] = []

    railway = tags.get("railway")
    station = tags.get("station")
    special = {"subway", "light_rail", "monorail", "funicular", "tram"}

    if tags.get("train") == "yes" or (
        railway in {"station", "halt"}
        and tags.get("train") != "no"
        and station not in special
        and tags.get("subway") != "yes"
        and tags.get("tram") != "yes"
    ):
        result.append(TransportMode.TRAIN)

    if railway == "tram_stop" or tags.get("tram") == "yes" or station == "tram":
        result.append(TransportMode.TRAM)

    if tags.get("subway") == "yes" or station == "subway":
        result.append(TransportMode.SUBWAY)

    if tags.get("light_rail") == "yes" or station == "light_rail":
        result.append(TransportMode.LIGHT_RAIL)

    if tags.get("monorail") == "yes" or station == "monorail":
        result.append(TransportMode.MONORAIL)

    if tags.get("funicular") == "yes" or station == "funicular":
        result.append(TransportMode.FUNICULAR)

    if tags.get("trolleybus") == "yes":
        result.append(TransportMode.TROLLEYBUS)

    if tags.get("aerialway") in {"station", "yes"}:
        result.append(TransportMode.CABLE_CAR)

    if (
        tags.get("amenity") == "ferry_terminal"
        or tags.get("ferry") == "yes"
        or tags.get("route") == "ferry"
    ):
        result.append(TransportMode.FERRY)

    if bus_candidate(tags) and tags.get("bus") != "no":
        refs = route_refs(tags)
        if not refs:
            result.append(TransportMode.REGIONAL_BUS)
        for ref in refs:
            if re.fullmatch(r"[A-Za-z]+[0-9]+|[0-9]{1,2}", ref):
                result.append(TransportMode.LOCAL_BUS)
            else:
                result.append(TransportMode.REGIONAL_BUS)

    return list(dict.fromkeys(result))


def amenities(tags: dict[str, str]) -> list[Amenity]:
    result: list[Amenity] = []

    for key, amenity in (
        ("bench", Amenity.BENCH),
        ("bin", Amenity.BIN),
        ("lit", Amenity.LIGHTING),
        ("tactile_paving", Amenity.TACTILE_PAVING),
    ):
        if tags.get(key) == "yes":
            result.append(amenity)

    if tags.get("shelter") == "yes" or tags.get("covered") == "yes":
        result.append(Amenity.SHELTER)

    if tags.get("wheelchair") in {"yes", "designated"}:
        result.append(Amenity.WHEELCHAIR)

    if tags.get("departures_board") in {"yes", "timetable"}:
        result.append(Amenity.TIMETABLE)

    if (
        tags.get("passenger_information_display") == "yes"
        or tags.get("departures_board") == "electronic"
    ):
        result.append(Amenity.DIGITAL_DISPLAY)

    return result


def stop_kind(tags: dict[str, str]) -> StopKind:
    if (
        tags.get("railway") == "station"
        or tags.get("public_transport") == "station"
        or tags.get("amenity") in {"bus_station", "ferry_terminal"}
        or tags.get("aerialway") == "station"
    ):
        return StopKind.STATION

    return StopKind.STOP


def common_kwargs(
    feature: OsmFeature, name: str, area: AdministrativeArea
) -> dict[str, object]:
    return {
        "name": name,
        "country": area.country,
        "region": area.region,
        "district": area.district,
        "municipality": area.municipality,
        "modes": modes(feature.tags),
        "amenities": amenities(feature.tags),
        "source": "osm",
        "original_id": feature.original_id,
    }


def make_stop(
    feature: OsmFeature,
    name: str,
    area: AdministrativeArea,
    *,
    area_id: str | None = None,
) -> Stop:
    kind = stop_kind(feature.tags) if area_id is None else StopKind.STOP
    kwargs = common_kwargs(feature, name, area)
    kwargs["original_id"] = area_id or feature.original_id
    return Stop(
        id=f"osm-s-{area_id.replace('/', '-') if area_id else feature.id_suffix}",
        kind=kind,
        lat=feature.lat if kind == StopKind.STATION else None,
        lon=feature.lon if kind == StopKind.STATION else None,
        **kwargs,
    )


def make_platform(
    feature: OsmFeature,
    parent: Stop,
    area: AdministrativeArea,
    *,
    fallback: bool = False,
) -> Platform:
    return Platform(
        id=f"osm-p-{'fallback-' if fallback else ''}{feature.id_suffix}",
        parent=parent,
        platform_code=(feature.tags.get("local_ref") or feature.tags.get("ref"))
        if not fallback
        else None,
        lat=feature.lat,
        lon=feature.lon,
        **common_kwargs(feature, parent.name, area),
    )


def map_features(
    features: list[OsmFeature], admin: AdministrativeLocator
) -> Iterator[Stop | Platform]:
    areas: dict[str, AdministrativeArea] = {}

    def area_of(feature: OsmFeature) -> AdministrativeArea:
        if feature.original_id not in areas:
            areas[feature.original_id] = admin.lookup(
                feature.lat, feature.lon, osm_id=feature.original_id
            )
        return areas[feature.original_id]

    stations: list[tuple[OsmFeature, Stop]] = []
    stations_by_id: dict[str, Stop] = {}
    area_parents: dict[str, Stop] = {}
    grid: dict[tuple[int, int], list[tuple[OsmFeature, Stop]]] = defaultdict(list)

    station_features = [feature for feature in features if is_station(feature.tags)]
    station_features.sort(
        key=lambda feature: (
            stop_kind(feature.tags) != StopKind.STATION,
            feature.original_id,
        )
    )
    for feature in tqdm(station_features, desc="Mapping stops", unit="feature"):
        name = feature.tags.get("name") or feature.stop_area_name
        if not name:
            continue

        unique_area = (
            feature.stop_area_ids[0] if len(feature.stop_area_ids) == 1 else None
        )

        if unique_area is not None and unique_area in area_parents:
            parent = area_parents[unique_area]
            stations_by_id[feature.original_id] = parent

            parent.modes = list(dict.fromkeys([*parent.modes, *modes(feature.tags)]))
            parent.amenities = list(
                dict.fromkeys([*parent.amenities, *amenities(feature.tags)])
            )

            stations.append((feature, parent))
            grid[cell(feature)].append((feature, parent))

            continue

        stop = make_stop(feature, name, area_of(feature))
        stations.append((feature, stop))
        stations_by_id[feature.original_id] = stop
        grid[cell(feature)].append((feature, stop))
        if unique_area is not None:
            area_parents[unique_area] = stop

        yield stop

    def nearby(feature: OsmFeature) -> Iterator[tuple[OsmFeature, Stop]]:
        row, col = cell(feature)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                yield from grid.get((row + dr, col + dc), ())

    def name_of(feature: OsmFeature) -> str | None:
        name = feature.tags.get("name") or feature.stop_area_name
        if name:
            return name
        candidates = [
            (distance(feature, other), stop.name) for other, stop in nearby(feature)
        ]
        close = [(d, n) for d, n in candidates if d <= PLATFORM_SEARCH_METERS]
        return min(close, default=(float("inf"), None))[1]

    platforms: list[tuple[OsmFeature, str]] = []
    platform_grid: dict[tuple[int, int], list[tuple[OsmFeature, str]]] = defaultdict(
        list
    )
    unnamed = 0
    for feature in tqdm(features, desc="Finding platforms", unit="feature"):
        if not is_platform(feature.tags):
            continue
        if name := name_of(feature):
            platforms.append((feature, name))
            platform_grid[cell(feature)].append((feature, name))
        else:
            unnamed += 1

    platform_area_ids = {
        area_id
        for platform_feature, _ in platforms
        for area_id in platform_feature.stop_area_ids
    }

    for feature in tqdm(features, desc="Stop-position fallbacks", unit="feature"):
        if (
            not is_stop_position(feature.tags)
            or is_platform(feature.tags)
            or is_station(feature.tags)
        ):
            continue

        if not (name := name_of(feature)):
            continue

        row, col = cell(feature)
        has_platform = any(
            area_id in platform_area_ids for area_id in feature.stop_area_ids
        )

        if not has_platform:
            has_platform = any(
                other_name == name
                and distance(feature, other) <= POSITION_SEARCH_METERS
                for dr in (-1, 0, 1)
                for dc in (-1, 0, 1)
                for other, other_name in platform_grid.get((row + dr, col + dc), ())
            )

        if not has_platform:
            platforms.append((feature, name))
            platform_grid[cell(feature)].append((feature, name))
            platform_area_ids.update(feature.stop_area_ids)

    platforms_by_parent: dict[str, list[Platform]] = defaultdict(list)
    for feature, name in tqdm(platforms, desc="Mapping platforms", unit="platform"):
        unique_area = (
            feature.stop_area_ids[0] if len(feature.stop_area_ids) == 1 else None
        )
        if unique_area is not None and unique_area in area_parents:
            parent = area_parents[unique_area]
            platform = make_platform(feature, parent, area_of(feature))
            platforms_by_parent[parent.id].append(platform)
            yield platform

            continue

        if unique_area is not None:
            parent = make_stop(
                feature,
                feature.stop_area_name or name,
                area_of(feature),
                area_id=unique_area,
            )
            area_parents[unique_area] = parent
            yield parent

            platform = make_platform(feature, parent, area_of(feature))
            platforms_by_parent[parent.id].append(platform)
            yield platform

            continue

        candidates = [
            (d, stop)
            for other, stop in nearby(feature)
            if stop.name == name
            if (d := distance(feature, other)) <= STATION_SEARCH_METERS
        ]

        if candidates:
            parent = min(candidates, key=lambda value: value[0])[1]
        elif feature.original_id in stations_by_id:
            parent = stations_by_id[feature.original_id]
        else:
            parent = make_stop(feature, name, area_of(feature))
            yield parent

        platform = make_platform(feature, parent, area_of(feature))
        platforms_by_parent[parent.id].append(platform)
        yield platform

    for feature, stop in tqdm(stations, desc="Station fallbacks", unit="station"):
        nearby_platforms = [
            platform
            for platform in platforms_by_parent[stop.id]
            if distance_meters((feature.lat, feature.lon), (platform.lat, platform.lon))
            <= POSITION_SEARCH_METERS
        ]
        station_modes = modes(feature.tags)
        present_modes = {
            mode for platform in nearby_platforms for mode in platform.modes
        }
        missing_modes = [mode for mode in station_modes if mode not in present_modes]

        if station_modes:
            if not missing_modes:
                continue
        elif nearby_platforms:
            continue

        fallback = make_platform(feature, stop, area_of(feature), fallback=True)
        if station_modes:
            fallback.modes = missing_modes
        platforms_by_parent[stop.id].append(fallback)
        yield fallback

    if unnamed:
        logger.warning("Skipped %d platforms without resolvable names", unnamed)
