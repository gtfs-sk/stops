"""
Validate a directory of exported dataset files before publishing.

Structural sanity gates for the release workflow (and the CI smoke test):
every expected file exists and is non-empty, the curated CSV headers match
the schemas in docs/exports.md, the dedicated SQLite export uses the
public schema (platforms.platform_parent_stop_id, ISCP tables, metadata),
and the row counts agree across the CSV, GeoJSON, GTFS, SQLite and ISCP
register exports of the same dataset.

    python3 scripts/validate_exports.py data/export --min-stops 1000

``--min-stops 0`` accepts an empty dataset (used by the smoke test, which
runs the exporters on an empty database). Prints one ``key: value`` stat
per line so the release workflow can quote the numbers in release notes.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sqlite3
import sys
from pathlib import Path
from zipfile import ZipFile

EXPECTED_FILES = [
    "stops.csv",
    "platforms.csv",
    "stops-and-platforms.csv",
    "stops.geojson",
    "stops.gtfs.zip",
    "stops.sqlite",
    "iscp-register.zip",
]

STOPS_HEADER = [
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
]

CURATED_HEADER = STOPS_HEADER + ["platform_code", "platform_parent_stop_id"]

GTFS_HEADER = [
    "stop_id",
    "stop_name",
    "stop_lat",
    "stop_lon",
    "location_type",
    "parent_station",
    "platform_code",
    "wheelchair_boarding",
]

ISCP_STOPS_HEADER = [
    "iscp_id",
    "municipality",
    "local_part",
    "locality",
    "name_ids",
    "name_mhd",
    "display_name",
    "district",
    "country",
    "transport_system",
    "validity",
    "is_cancelled",
    "source_file",
]

ISCP_PLATFORMS_HEADER = ["iscp_id", "position", "lat", "lon"]

ISCP_ZIP_FILES = {
    "iscp-stops.csv",
    "iscp-platforms.csv",
    "iscp-stops.geojson",
    "iscp.sqlite",
}


def fail(message: str) -> None:
    raise SystemExit(f"validate_exports: error: {message}")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def read_csv_text(text: str) -> tuple[list[str], list[dict[str, str]]]:
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    return list(reader.fieldnames or []), rows


def check_header(path: Path, expected: list[str]) -> list[dict[str, str]]:
    header, rows = read_csv(path)
    if header != expected:
        fail(f"{path.name}: header {header} != expected {expected}")
    return rows


def table_columns(db: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in db.execute(f"PRAGMA table_info({table})")]


def count(db: sqlite3.Connection, table: str) -> int:
    return int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def check_sqlite(path: Path, stops: int, combined: int) -> tuple[int, int, int]:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        platform_columns = table_columns(db, "platforms")
        if "platform_parent_stop_id" not in platform_columns:
            fail("stops.sqlite: platforms has no platform_parent_stop_id column")
        if "stop_id" in platform_columns:
            fail("stops.sqlite: platforms still uses the internal stop_id name")

        db_stops = count(db, "stops")
        db_platforms = count(db, "platforms")
        db_iscp_stops = count(db, "iscp_stops")
        db_iscp_platforms = count(db, "iscp_platforms")
        view_rows = count(db, "stops_and_platforms")
        metadata = dict(db.execute("SELECT key, value FROM metadata").fetchall())
    finally:
        db.close()

    if db_stops != stops:
        fail(f"stops.sqlite has {db_stops} stops but stops.csv has {stops} rows")
    if db_platforms != combined - stops:
        fail(
            f"stops.sqlite has {db_platforms} platforms but the CSV exports "
            f"imply {combined - stops}"
        )
    if view_rows != combined:
        fail(
            f"stops.sqlite view stops_and_platforms has {view_rows} rows "
            f"but stops-and-platforms.csv has {combined}"
        )
    if metadata.get("schema_version") != "1":
        fail("stops.sqlite: missing or unexpected metadata.schema_version")
    return db_platforms, db_iscp_stops, db_iscp_platforms


def check_iscp_zip(
    path: Path, db_iscp_stops: int, db_iscp_platforms: int
) -> tuple[int, int]:
    with ZipFile(path) as archive:
        names = set(archive.namelist())
        if names != ISCP_ZIP_FILES:
            fail(
                f"iscp-register.zip contains {sorted(names)}, "
                f"expected {sorted(ISCP_ZIP_FILES)}"
            )
        stops_header, stop_rows = read_csv_text(
            archive.read("iscp-stops.csv").decode("utf-8")
        )
        platforms_header, platform_rows = read_csv_text(
            archive.read("iscp-platforms.csv").decode("utf-8")
        )
        geojson = json.loads(archive.read("iscp-stops.geojson").decode("utf-8"))
        sqlite_bytes = archive.read("iscp.sqlite")

    if stops_header != ISCP_STOPS_HEADER:
        fail(f"iscp-stops.csv header {stops_header} != expected {ISCP_STOPS_HEADER}")
    if platforms_header != ISCP_PLATFORMS_HEADER:
        fail(
            f"iscp-platforms.csv header {platforms_header} "
            f"!= expected {ISCP_PLATFORMS_HEADER}"
        )
    if len(stop_rows) != db_iscp_stops:
        fail(
            f"iscp-stops.csv has {len(stop_rows)} rows but stops.sqlite "
            f"has {db_iscp_stops} iscp_stops"
        )
    if len(platform_rows) != db_iscp_platforms:
        fail(
            f"iscp-platforms.csv has {len(platform_rows)} rows but stops.sqlite "
            f"has {db_iscp_platforms} iscp_platforms"
        )
    if len(geojson["features"]) < len(stop_rows):
        fail("iscp-stops.geojson has fewer features than iscp-stops.csv rows")

    db = sqlite3.connect(":memory:")
    try:
        db.deserialize(sqlite_bytes)
        zip_stops = count(db, "iscp_stops")
        zip_platforms = count(db, "iscp_platforms")
    finally:
        db.close()
    if (zip_stops, zip_platforms) != (db_iscp_stops, db_iscp_platforms):
        fail("iscp.sqlite inside iscp-register.zip disagrees with stops.sqlite")
    return len(stop_rows), len(platform_rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--min-stops", type=int, default=1)
    parser.add_argument("--min-iscp", type=int, default=0)
    args = parser.parse_args(argv)
    export_dir: Path = args.export_dir

    for name in EXPECTED_FILES:
        path = export_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            fail(f"missing or empty export file: {name}")

    stop_rows = check_header(export_dir / "stops.csv", STOPS_HEADER)
    check_header(export_dir / "platforms.csv", CURATED_HEADER)
    combined_rows = check_header(export_dir / "stops-and-platforms.csv", CURATED_HEADER)

    stops = len(stop_rows)
    if stops < args.min_stops:
        fail(f"only {stops} stops exported, expected at least {args.min_stops}")

    with (export_dir / "stops.geojson").open(encoding="utf-8") as stream:
        geojson = json.load(stream)
    features = len(geojson["features"])
    if features != len(combined_rows):
        fail(
            f"stops.geojson has {features} features but "
            f"stops-and-platforms.csv has {len(combined_rows)} rows"
        )
    for feature in geojson["features"]:
        props = feature["properties"]
        if props.get("type") == "platform" and "platform_parent_stop_id" not in props:
            fail("stops.geojson: platform feature without platform_parent_stop_id")

    db_platforms, db_iscp_stops, db_iscp_platforms = check_sqlite(
        export_dir / "stops.sqlite", stops, len(combined_rows)
    )
    iscp_stops, iscp_platforms = check_iscp_zip(
        export_dir / "iscp-register.zip", db_iscp_stops, db_iscp_platforms
    )
    if iscp_stops < args.min_iscp:
        fail(
            f"only {iscp_stops} ISCP records exported, "
            f"expected at least {args.min_iscp}"
        )

    with ZipFile(export_dir / "stops.gtfs.zip") as archive:
        if archive.namelist() != ["stops.txt"]:
            fail(f"stops.gtfs.zip contains {archive.namelist()}, expected stops.txt")
        with archive.open("stops.txt") as stream:
            text = (line.decode("utf-8") for line in stream)
            reader = csv.DictReader(text)
            gtfs_rows = list(reader)
            if list(reader.fieldnames or []) != GTFS_HEADER:
                fail(f"GTFS stops.txt header {reader.fieldnames} != {GTFS_HEADER}")

    if len(gtfs_rows) < stops:
        fail(f"GTFS stops.txt has {len(gtfs_rows)} rows for {stops} stops")

    with_iscp = sum(1 for row in stop_rows if row["iscp_id"])
    coverage = 100.0 * with_iscp / stops if stops else 0.0
    print(f"stops: {stops}")
    print(f"platforms: {db_platforms}")
    print(f"stops_with_iscp_id: {with_iscp}")
    print(f"iscp_coverage_pct: {coverage:.1f}")
    print(f"iscp_stops: {iscp_stops}")
    print(f"iscp_platforms: {iscp_platforms}")
    print(f"gtfs_rows: {len(gtfs_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
