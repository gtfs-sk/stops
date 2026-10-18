from __future__ import annotations

import json
import logging
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import fiona
from pyproj import CRS, Geod, Transformer
from shapely.geometry import Point, shape
from shapely.ops import nearest_points
from shapely.strtree import STRtree
from tqdm.auto import tqdm

from .download import CACHE_DIR, USER_AGENT, download_file, ensure_gpkg_files

logger = logging.getLogger(__name__)

# Slovakia: GKÚ ZBGIS. Neighbours: geoBoundaries gbOpen ADM0 in WGS84.
# The latter is only used for country codes. No foreign region/district/municipality
# is derived from these polygons.
COUNTRY_DIR = CACHE_DIR / "admin" / "countries"
NEIGHBOURS = {
    "AT": "AUT",
    "CZ": "CZE",
    "HU": "HUN",
    "PL": "POL",
    "UA": "UKR",
}
COUNTRY_API = "https://www.geoboundaries.org/api/current/gbOpen/{iso3}/ADM0/"

LAYER_HINTS = {
    "region": ("kraj", "region"),
    "district": ("okres", "district"),
    "municipality": ("obec", "municipality"),
}
MIN_FEATURES = {"region": 8, "district": 79, "municipality": 2800}


@dataclass(frozen=True, slots=True)
class AdministrativeArea:
    country: str
    region: str | None = None
    district: str | None = None
    municipality: str | None = None


class AdministrativeLookupError(ValueError):
    pass


def discover_layers(files: list[Path]) -> dict[str, tuple[Path, str]]:
    candidates: dict[str, list[tuple[Path, str]]] = {key: [] for key in LAYER_HINTS}
    discovered: list[str] = []
    for path in files:
        for layer in fiona.listlayers(path):
            discovered.append(f"{path.name}:{layer}")
            for kind, hints in LAYER_HINTS.items():
                if any(hint in layer for hint in hints):
                    candidates[kind].append((path, layer))

    selected: dict[str, tuple[Path, str]] = {}
    for kind, value in LAYER_HINTS.items():
        override = os.environ.get(f"GKU_{kind.upper()}_LAYER")
        options = candidates[kind]
        if override:
            options = [
                (path, layer)
                for path in files
                for layer in fiona.listlayers(path)
                if override in (layer, f"{path.name}:{layer}")
            ]

        elif len(options) > 1:
            exact = [(p, layer) for p, layer in options if layer in value]
            if len(exact) == 1:
                options = exact

        if len(options) != 1:
            raise ValueError(
                f"Cannot uniquely identify GKÚ {kind} layer. Candidates={options}. "
                f"Available={discovered}. "
                f"Set GKU_{kind.upper()}_LAYER explicitly."
            )

        selected[kind] = options[0]

    return selected


def name_column(kind: str, fields: list[str]) -> str:
    # GKÚ ZBGIS AH GeoPackage schema: NM2=kraj, NM3=okres, NM4=obec.
    columns = {"region": "NM2", "district": "NM3", "municipality": "NM4"}
    override = os.environ.get(f"GKU_{kind.upper()}_NAME_COLUMN")
    column = override or columns[kind]
    if column not in fields:
        raise ValueError(
            f"GKÚ {kind}: expected name column {column!r}, available: {fields}. "
            f"Override with GKU_{kind.upper()}_NAME_COLUMN if the source schema changed."
        )

    return column


class PolygonLayer:
    def __init__(self, kind: str, path: Path, layer: str) -> None:
        self.kind = kind
        with fiona.open(path, layer=layer) as source:
            self.crs = CRS.from_user_input(source.crs_wkt or source.crs)
            column = name_column(kind, list(source.schema["properties"]))
            geometries = []
            names = []
            for item in tqdm(
                source, desc=f"Indexing GKÚ {kind}", unit="area", total=len(source)
            ):
                raw = item["properties"][column]
                name = str(raw).strip() if raw is not None else ""
                if not name or item["geometry"] is None:
                    raise ValueError(
                        f"GKÚ {kind}: missing name/geometry in {path.name}:{layer}"
                    )
                geom = shape(item["geometry"])
                if geom.is_empty or geom.geom_type not in {"Polygon", "MultiPolygon"}:
                    raise ValueError(f"GKÚ {kind}: invalid polygon for {name!r}")
                geometries.append(geom)
                names.append(name)

        if len(geometries) < MIN_FEATURES[kind]:
            raise ValueError(
                f"GKÚ {kind}: only {len(geometries)} geometries in {path.name}:{layer}"
            )
        self.geometries = geometries
        self.names = names
        self.tree = STRtree(geometries)

    def lookup(self, point: Point) -> str | None:
        hits = [
            self.names[int(i)]
            for i in self.tree.query(point)
            if self.geometries[int(i)].covers(point)
        ]
        matches = set(hits)
        if len(matches) > 1:
            raise AdministrativeLookupError(
                f"Ambiguous {self.kind} boundaries: {sorted(matches)}"
            )
        return next(iter(matches), None)

    def distance_to_nearest(self, point: Point) -> float:
        # GKÚ uses a projected CRS with metre units (EPSG:8353).
        index = self.tree.nearest(point)
        if index is None:
            return float("inf")
        return float(self.geometries[int(index)].distance(point))


def load_foreign_countries() -> CountryIndex:
    """
    Load neighbouring countries' ADM0 boundaries, caching downloaded GeoJSONs.

    Run with GKU_COUNTRIES_REFRESH=1 to refresh them. A missing file or API
    failure aborts the import: never silently guess a country from coordinates.
    """

    COUNTRY_DIR.mkdir(parents=True, exist_ok=True)
    refresh = os.environ.get("GKU_COUNTRIES_REFRESH") == "1"
    geometries = []
    codes = []

    for code, iso3 in tqdm(
        NEIGHBOURS.items(), desc="Loading country borders", unit="country"
    ):
        path = COUNTRY_DIR / f"{iso3}-ADM0.geojson"
        if refresh or not path.is_file() or path.stat().st_size == 0:
            metadata_url = COUNTRY_API.format(iso3=iso3)
            request = urllib.request.Request(
                metadata_url, headers={"User-Agent": USER_AGENT}
            )
            with urllib.request.urlopen(request, timeout=60) as response:
                metadata: dict[str, object] = json.load(response)
            if (
                metadata.get("boundaryISO") != iso3
                or metadata.get("boundaryType") != "ADM0"
            ):
                raise ValueError(
                    f"Unexpected geoBoundaries metadata for {iso3}: {metadata_url}"
                )
            url = metadata.get("gjDownloadURL")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError(f"No secure geoBoundaries GeoJSON URL for {iso3}")
            logger.info("Downloading %s country border: %s", code, url)
            download_file(url, path, description=f"ADM0 {code}")

        with path.open("r", encoding="utf-8") as source:
            data = json.load(source)
        if data.get("type") == "FeatureCollection":
            features = data["features"]
        elif data.get("type") == "Feature":
            features = [data]
        else:
            raise ValueError(
                f"Unexpected GeoJSON in {path}: expected FeatureCollection"
            )

        for feature in features:
            geometry = feature.get("geometry")
            if not geometry:
                raise ValueError(f"Missing geometry in {path}")
            polygon = shape(geometry)
            if polygon.is_empty or polygon.geom_type not in {"Polygon", "MultiPolygon"}:
                raise ValueError(f"Invalid country polygon in {path}")
            if not polygon.is_valid:
                raise ValueError(f"Invalid topology in country polygon {path}")
            geometries.append(polygon)
            codes.append(code)

    return CountryIndex(geometries, codes)


class CountryIndex:
    def __init__(self, geometries: list, codes: list[str]) -> None:
        self.geometries = geometries
        self.codes = codes
        self.tree = STRtree(geometries)

    def lookup(self, lat: float, lon: float) -> str | None:
        point = Point(lon, lat)
        hits = {
            self.codes[int(i)]
            for i in self.tree.query(point)
            if self.geometries[int(i)].covers(point)
        }
        if len(hits) > 1:
            raise AdministrativeLookupError(
                f"Overlapping foreign borders at ({lat}, {lon}): {sorted(hits)}"
            )
        return next(iter(hits), None)

    def nearest_distances(self, lat: float, lon: float) -> list[tuple[str, float]]:
        """Geodesic distance from the point to each foreign country's polygon."""
        point = Point(lon, lat)
        geod = Geod(ellps="WGS84")
        candidates: dict[str, float] = {}
        for polygon, code in zip(self.geometries, self.codes):
            # The nearest point in WGS84 degrees is used only to select a
            # boundary position; distance is measured geodesically in metres.
            nearest = nearest_points(point, polygon)[1]
            _, _, distance_m = geod.inv(lon, lat, nearest.x, nearest.y)
            candidates[code] = min(distance_m, candidates.get(code, float("inf")))

        return sorted(candidates.items(), key=lambda item: item[1])


class AdministrativeLocator:
    def __init__(
        self, layers: dict[str, PolygonLayer], countries: CountryIndex
    ) -> None:
        if set(layers) != set(LAYER_HINTS):
            raise ValueError("GKÚ must provide region, district, municipality layers")
        crs = {layer.crs.to_string() for layer in layers.values()}
        if len(crs) != 1:
            raise ValueError(f"GKÚ layers use different coordinate systems: {crs}")
        self.layers = layers
        self.countries = countries
        self.transformer = Transformer.from_crs(
            "EPSG:4326", next(iter(layers.values())).crs, always_xy=True
        )

    @classmethod
    def load(cls) -> AdministrativeLocator:
        files = ensure_gpkg_files()
        selected = discover_layers(files)
        layers = {kind: PolygonLayer(kind, *selected[kind]) for kind in LAYER_HINTS}
        countries = load_foreign_countries()
        return cls(layers, countries)

    def lookup(self, lat: float, lon: float, *, osm_id: str) -> AdministrativeArea:
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            raise AdministrativeLookupError(
                f"{osm_id}: invalid coordinates ({lat}, {lon})"
            )

        try:
            x, y = self.transformer.transform(lon, lat)
            sk_point = Point(x, y)
            region = self.layers["region"].lookup(sk_point)
            if region is not None:
                # GKÚ defines SK coverage and is authoritative for its admin fields.
                district = self.layers["district"].lookup(sk_point)
                municipality = self.layers["municipality"].lookup(sk_point)
                if not district or not municipality:
                    raise AdministrativeLookupError(
                        f"Missing GKÚ district/municipality inside Slovakia "
                        f"(region={region!r}, district={district!r}, "
                        f"municipality={municipality!r})"
                    )
                return AdministrativeArea(
                    country="SK",
                    region=region,
                    district=district,
                    municipality=municipality,
                )

            country = self.countries.lookup(lat, lon)
            if country is not None:
                return AdministrativeArea(country=country)

            # Different boundary sources can leave gaps, especially on rivers.
            foreign_distances = self.countries.nearest_distances(lat, lon)
            if foreign_distances:
                nearest_country, nearest_distance = foreign_distances[0]
                logger.warning(
                    "Assigning %s to %s using nearest foreign border "
                    "fallback (distance %.1f m)",
                    osm_id,
                    nearest_country,
                    nearest_distance,
                )
                return AdministrativeArea(country=nearest_country)

            sk_distance = self.layers["region"].distance_to_nearest(sk_point)
            detail = ", ".join(
                f"{code}={distance:.1f}m" for code, distance in foreign_distances[:4]
            )
            raise AdministrativeLookupError(
                "No country polygon contains point. "
                f"Nearest foreign borders: {detail or 'none'}. "
                f"Nearest GKÚ Slovakia boundary: {sk_distance:.1f}m."
            )

        except (AdministrativeLookupError, ValueError) as exc:
            raise AdministrativeLookupError(f"{osm_id} ({lat}, {lon}): {exc}") from exc
