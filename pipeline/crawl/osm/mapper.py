from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterator

from tqdm.auto import tqdm

from models import Platform, Stop, TransportMode
from models.amenity import Amenity

from .admin import AdministrativeArea, AdministrativeLocator
from .reader import OsmFeature, is_platform, is_station, is_stop_position

logger = logging.getLogger(__name__)

CELL_SIZE = 0.01  # degrees; adjacent cells cover a 300 m search radius in Slovakia
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
    modes: list[TransportMode] = []

    railway = tags.get("railway")
    station = tags.get("station")

    special_stations = {
        "subway",
        "light_rail",
        "monorail",
        "funicular",
        "tram",
    }

    if tags.get("train") == "yes" or (
        railway in {"station", "halt"}
        and tags.get("train") != "no"
        and station not in special_stations
        and tags.get("subway") != "yes"
        and tags.get("tram") != "yes"
    ):
        modes.append(TransportMode.TRAIN)

    if railway == "tram_stop" or tags.get("tram") == "yes" or station == "tram":
        modes.append(TransportMode.TRAM)

    if tags.get("subway") == "yes" or station == "subway":
        modes.append(TransportMode.SUBWAY)

    if tags.get("light_rail") == "yes" or station == "light_rail":
        modes.append(TransportMode.LIGHT_RAIL)

    if tags.get("monorail") == "yes" or station == "monorail":
        modes.append(TransportMode.MONORAIL)

    if tags.get("funicular") == "yes" or station == "funicular":
        modes.append(TransportMode.FUNICULAR)

    if tags.get("trolleybus") == "yes":
        modes.append(TransportMode.TROLLEYBUS)

    if tags.get("aerialway") == "station" or tags.get("aerialway") == "yes":
        modes.append(TransportMode.CABLE_CAR)

    if (
        tags.get("amenity") == "ferry_terminal"
        or tags.get("ferry") == "yes"
        or tags.get("route") == "ferry"
    ):
        modes.append(TransportMode.FERRY)

    if bus_candidate(tags) and tags.get("bus") != "no":
        refs = route_refs(tags)

        if not refs:
            modes.append(TransportMode.REGIONAL_BUS)

        for ref in refs:
            if re.fullmatch(r"[A-Za-z]+[0-9]+|[0-9]{1,2}", ref):
                modes.append(TransportMode.MHD)
            else:
                modes.append(TransportMode.REGIONAL_BUS)

    return list(dict.fromkeys(modes))


def _amenities(tags: dict[str, str]) -> list[Amenity]:
    result: list[Amenity] = []

    for tag, amenity in (
        ("bench", Amenity.BENCH),
        ("bin", Amenity.BIN),
        ("lit", Amenity.LIGHTING),
        ("tactile_paving", Amenity.TACTILE_PAVING),
    ):
        if tags.get(tag) == "yes":
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


def _common_kwargs(
    feature: OsmFeature, name: str, area: AdministrativeArea
) -> dict[str, object]:
    return {
        "name": name,
        "lat": feature.lat,
        "lon": feature.lon,
        "country": area.country,
        "region": area.region,
        "district": area.district,
        "municipality": area.municipality,
        "modes": modes(feature.tags),
        "amenities": _amenities(feature.tags),
        "source": "osm",
        "original_id": feature.original_id,
    }


def make_stop(feature: OsmFeature, name: str, area: AdministrativeArea) -> Stop:
    return Stop(
        id=f"osm-s-{feature.id_suffix}",
        is_station=is_station(feature.tags),
        **_common_kwargs(feature, name, area),
    )


def make_platform(
    feature: OsmFeature,
    parent: Stop,
    area: AdministrativeArea,
    *,
    fallback: bool = False,
) -> Platform:
    values = _common_kwargs(feature, parent.name, area)
    return Platform(
        id=f"osm-p-{'fallback-' if fallback else ''}{feature.id_suffix}",
        parent=parent,
        platform_code=(feature.tags.get("local_ref") or feature.tags.get("ref"))
        if not fallback
        else None,
        **values,
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
    grid: dict[tuple[int, int], list[tuple[OsmFeature, Stop]]] = defaultdict(list)
    for feature in tqdm(features, desc="Mapping stations", unit="feature"):
        if not is_station(feature.tags):
            continue

        name = feature.tags.get("name") or feature.stop_area_name
        if not name:
            continue

        stop = make_stop(feature, name, area_of(feature))
        stations.append((feature, stop))
        stations_by_id[feature.original_id] = stop
        grid[cell(feature)].append((feature, stop))

        yield stop

    def nearby(feature: OsmFeature) -> Iterator[tuple[OsmFeature, Stop]]:
        row, col = cell(feature)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                yield from grid.get((row + dr, col + dc), [])

    def name_of(feature: OsmFeature) -> str | None:
        name = feature.tags.get("name") or feature.stop_area_name
        if name:
            return name

        candidates: list[tuple[float, str]] = [
            (distance(feature, neighbor), stop.name)
            for neighbor, stop in nearby(feature)
        ]

        close = [
            (distance, name)
            for distance, name in candidates
            if distance <= PLATFORM_SEARCH_METERS
        ]

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

    # stop_position represents the vehicle stopping location, use it only
    # when no mapped passenger platform with the same name is nearby.
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
            other_name == name and distance(feature, other) <= POSITION_SEARCH_METERS
            for dr in (-1, 0, 1)
            for dc in (-1, 0, 1)
            for other, other_name in platform_grid.get((row + dr, col + dc), ())
        )

        if not has_platform:
            platforms.append((feature, name))
            platform_grid[cell(feature)].append((feature, name))

    for feature, name in tqdm(platforms, desc="Mapping platforms", unit="platform"):
        candidates = [
            (d, stop)
            for neighbor, stop in nearby(feature)
            if stop.name == name
            if (d := distance(feature, neighbor)) <= STATION_SEARCH_METERS
        ]

        if candidates:
            parent = min(candidates, key=lambda value: value[0])[1]
        elif feature.original_id in stations_by_id:
            parent = stations_by_id[feature.original_id]
        else:
            parent = make_stop(feature, name, area_of(feature))
            yield parent

        yield make_platform(feature, parent, area_of(feature))

    # stations that have no mapped platform still need one fallback platform
    created_fallbacks: dict[str, list[OsmFeature]] = defaultdict(list)
    for feature, stop in tqdm(stations, desc="Station fallbacks", unit="station"):
        row, col = cell(feature)

        if any(
            name == stop.name and distance(feature, platform) <= PLATFORM_SEARCH_METERS
            for dr in (-1, 0, 1)
            for dc in (-1, 0, 1)
            for platform, name in platform_grid.get((row + dr, col + dc), ())
        ):  # already has a platform with the same name nearby
            continue

        if any(
            distance(feature, previous) <= 150
            for previous in created_fallbacks[stop.name]
        ):
            continue

        created_fallbacks[stop.name].append(feature)
        yield make_platform(feature, stop, area_of(feature), fallback=True)

    if unnamed:
        logger.warning("Skipped %d platform features without resolvable names", unnamed)
