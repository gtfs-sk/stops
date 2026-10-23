"""
Validate a directory of exported dataset files before publishing.

Structural sanity gates for the release workflow (and the CI smoke test):
every expected file exists and is non-empty, the curated CSV headers match
the schemas in docs/exports.md, and the row counts agree across the CSV,
GeoJSON, GTFS and SQLite exports of the same dataset.

    python3 scripts/validate_exports.py data/export --min-stops 1000

``--min-stops 0`` accepts an empty dataset (used by the smoke test, which
runs the exporters on an empty database). Prints one ``key: value`` stat
per line so the release workflow can quote the numbers in release notes.
"""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path
from zipfile import ZipFile

EXPECTED_FILES = [
    "stops.csv",
    "platforms.csv",
    "stops-and-platforms.csv",
    "stops.full.csv",
    "platforms.full.csv",
    "stops-and-platforms.full.csv",
    "stops.geojson",
    "stops.gtfs.zip",
    "stops.sqlite",
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


def fail(message: str) -> None:
    raise SystemExit(f"validate_exports: error: {message}")


def read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        return list(reader.fieldnames or []), rows


def check_header(path: Path, expected: list[str]) -> list[dict[str, str]]:
    header, rows = read_csv(path)
    if header != expected:
        fail(f"{path.name}: header {header} != expected {expected}")
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("export_dir", type=Path)
    parser.add_argument("--min-stops", type=int, default=1)
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

    db = sqlite3.connect(f"file:{export_dir / 'stops.sqlite'}?mode=ro", uri=True)
    try:
        db_stops = db.execute("SELECT COUNT(*) FROM stops").fetchone()[0]
        db_platforms = db.execute("SELECT COUNT(*) FROM platforms").fetchone()[0]
    finally:
        db.close()
    if db_stops != stops:
        fail(f"stops.sqlite has {db_stops} stops but stops.csv has {stops} rows")
    if db_platforms != len(combined_rows) - stops:
        fail(
            f"stops.sqlite has {db_platforms} platforms but the CSV exports "
            f"imply {len(combined_rows) - stops}"
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
    print(f"gtfs_rows: {len(gtfs_rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
