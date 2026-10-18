from __future__ import annotations

import json
import unicodedata
import urllib.parse
import urllib.request
from collections.abc import Iterator
from typing import override

from models import Platform, Stop, TransportMode
from models.amenity import Amenity
from pipeline.crawl import BaseCrawler

OVERPASS_URL = "https://overpass.private.coffee/api/interpreter"

OVERPASS_QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="SK"]->.sk;
(
  node["highway"="bus_stop"](area.sk);
  node["railway"="station"](area.sk);
  node["railway"="halt"](area.sk);
);
out body;
"""


def _normalize(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip()


def _resolve_modes(tags: dict[str, str]) -> list[TransportMode]:
    modes: list[TransportMode] = []

    railway = tags.get("railway")
    if railway in ("station", "halt") or tags.get("train") == "yes":
        modes.append(TransportMode.TRAIN)
    if railway == "tram_stop" or tags.get("tram") == "yes":
        modes.append(TransportMode.TRAM)

    if tags.get("trolleybus") == "yes":
        modes.append(TransportMode.TROLLEYBUS)

    highway = tags.get("highway")
    if highway == "bus_stop" or tags.get("bus") == "yes":
        modes.append(TransportMode.REGIONAL_BUS)
        modes.append(TransportMode.MHD)

    if not modes:
        modes.append(TransportMode.MHD)

    return list(set(modes))


def _resolve_amenities(tags: dict[str, str]) -> list[Amenity]:
    amenities: list[Amenity] = []

    if tags.get("bench") == "yes":
        amenities.append(Amenity.BENCH)

    if tags.get("shelter") == "yes" or tags.get("covered") == "yes":
        amenities.append(Amenity.SHELTER)

    if tags.get("bin") == "yes":
        amenities.append(Amenity.BIN)

    if tags.get("lit") == "yes":
        amenities.append(Amenity.LIGHTING)

    if tags.get("tactile_paving") == "yes":
        amenities.append(Amenity.TACTILE_PAVING)

    if tags.get("wheelchair") in ("yes", "designated"):
        amenities.append(Amenity.WHEELCHAIR)

    if tags.get("departures_board") in ("timetable", "yes"):
        amenities.append(Amenity.TIMETABLE)

    if (
        tags.get("passenger_information_display") == "yes"
        or tags.get("departures_board") == "electronic"
    ):
        amenities.append(Amenity.DIGITAL_DISPLAY)

    return amenities


class OsmCrawler(BaseCrawler):
    name: str = "osm"

    @override
    def fetch(self) -> Iterator[Stop | Platform]:
        encoded_query = urllib.parse.urlencode({"data": OVERPASS_QUERY}).encode("utf-8")
        request = urllib.request.Request(
            OVERPASS_URL,
            data=encoded_query,
            headers={
                "User-Agent": "gtfs-sk-crawler/1.0 (https://gtfs.sk)",
                "Accept": "*/*",
                "Content-Type": "text/plain;charset=UTF-8",
                "Referer": "https://gtfs.sk",
            },
        )

        with urllib.request.urlopen(request, timeout=200) as response:
            payload = json.loads(response.read().decode("utf-8"))

        elements: list[dict[str, object]] = payload.get("elements", [])

        for el in elements:
            raw_tags = el.get("tags")
            if not isinstance(raw_tags, dict):
                continue

            tags: dict[str, str] = {
                str(k): str(v)
                for k, v in raw_tags.items()
                if isinstance(v, (str, int, float))
            }

            name = tags.get("name")
            if not name:
                continue

            osm_id = str(el.get("id"))
            lat = float(str(el.get("lat")))
            lon = float(str(el.get("lon")))
            platform_code = tags.get("ref") or tags.get("local_ref")
            modes = _resolve_modes(tags)
            amenities = _resolve_amenities(tags)
            norm_name = _normalize(name)

            station = Stop(
                id=f"osm-s-{osm_id}",
                name=name,
                lat=lat,
                lon=lon,
                modes=modes,
                amenities=amenities,
                source=self.name,
                original_id=f"node/{osm_id}",
                normalized_name=norm_name,
            )
            yield station

            yield Platform(
                id=f"osm-p-{osm_id}",
                platform_code=platform_code,
                lat=lat,
                lon=lon,
                parent=station,
                modes=modes,
                amenities=amenities,
                source=self.name,
                original_id=f"node/{osm_id}",
                normalized_name=norm_name,
            )
