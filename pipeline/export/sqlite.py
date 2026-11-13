"""Dedicated SQLite export.

The exported database is built from scratch with an explicit, stable
schema. It is deliberately not a backup of the working database, whose
layout is an implementation detail. Public naming follows the CSV
exports: a platform's parent stop is ``platform_parent_stop_id``
everywhere (the internal model keeps the conventional ``stop_id``).

The same database also carries the ISCP registry tables and the match
links, so the register can be joined with the canonical stops without
any other file. The standalone register export (iscp-register.zip)
reuses the ISCP table helpers defined here.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from typing import final, override

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from models import IscpMatch, IscpStop, Stop
from pipeline.export import BaseExporter

from .records import iscp_stop_values

SCHEMA_VERSION = "1"

MAIN_DDL = """
CREATE TABLE stops (
    id TEXT PRIMARY KEY,
    iscp_id TEXT,
    name TEXT NOT NULL,
    type TEXT NOT NULL,
    lat REAL,
    lon REAL,
    country TEXT NOT NULL,
    region TEXT,
    district TEXT,
    municipality TEXT,
    modes TEXT NOT NULL,
    amenities TEXT NOT NULL,
    original_name TEXT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL
);
CREATE INDEX idx_stops_iscp_id ON stops(iscp_id);

CREATE TABLE platforms (
    id TEXT PRIMARY KEY,
    platform_parent_stop_id TEXT NOT NULL REFERENCES stops(id),
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    country TEXT NOT NULL,
    region TEXT,
    district TEXT,
    municipality TEXT,
    modes TEXT NOT NULL,
    amenities TEXT NOT NULL,
    platform_code TEXT,
    original_name TEXT,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL
);
CREATE INDEX idx_platforms_parent ON platforms(platform_parent_stop_id);

CREATE TABLE iscp_matches (
    stop_id TEXT PRIMARY KEY REFERENCES stops(id),
    iscp_id TEXT NOT NULL UNIQUE REFERENCES iscp_stops(iscp_id),
    quality TEXT NOT NULL,
    distance_m REAL
);
CREATE INDEX idx_iscp_matches_iscp_id ON iscp_matches(iscp_id);
"""

VIEW_DDL = """
CREATE VIEW stops_and_platforms AS
SELECT id, iscp_id, name, type, lat, lon, country, region, district,
       municipality, modes, amenities,
       NULL AS platform_code, NULL AS platform_parent_stop_id
FROM stops
UNION ALL
SELECT id, NULL, name, 'platform', lat, lon, country, region, district,
       municipality, modes, amenities,
       platform_code, platform_parent_stop_id
FROM platforms;
"""

ISCP_DDL = """
CREATE TABLE iscp_stops (
    iscp_id TEXT PRIMARY KEY,
    municipality TEXT NOT NULL,
    local_part TEXT,
    locality TEXT,
    name_ids TEXT,
    name_mhd TEXT,
    display_name TEXT NOT NULL,
    district TEXT,
    country TEXT,
    transport_system TEXT,
    validity TEXT,
    is_cancelled INTEGER NOT NULL,
    source_file TEXT NOT NULL,
    raw_fields TEXT NOT NULL
);

CREATE TABLE iscp_platforms (
    iscp_id TEXT NOT NULL REFERENCES iscp_stops(iscp_id),
    position INTEGER NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL,
    PRIMARY KEY (iscp_id, position)
);
"""

METADATA_DDL = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def _json_list(values: Sequence[object] | None) -> str:
    return json.dumps([str(value) for value in values or []], ensure_ascii=False)


def create_metadata_table(conn: sqlite3.Connection) -> None:
    conn.execute(METADATA_DDL)


def write_metadata(conn: sqlite3.Connection, dataset: str) -> None:
    conn.executemany(
        "INSERT INTO metadata VALUES (?, ?)",
        [
            ("schema_version", SCHEMA_VERSION),
            ("dataset", dataset),
            ("generated_at", datetime.now(UTC).isoformat()),
        ],
    )


def create_iscp_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(ISCP_DDL)


def insert_iscp_records(conn: sqlite3.Connection, records: Sequence[IscpStop]) -> None:
    for record in sorted(records, key=lambda item: item.iscp_id):
        conn.execute(
            "INSERT INTO iscp_stops VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                *iscp_stop_values(record),
                json.dumps(record.raw_fields or {}, ensure_ascii=False),
            ],
        )
        for platform in sorted(record.platforms, key=lambda item: item.position):
            conn.execute(
                "INSERT INTO iscp_platforms VALUES (?,?,?,?)",
                (record.iscp_id, platform.position, platform.lat, platform.lon),
            )


@final
class SqliteExporter(BaseExporter):
    filename = "stops.sqlite"

    @override
    def export(self, stops: Sequence[Stop], destination: Path) -> None:
        raise RuntimeError("SqliteExporter requires a session; use run_exporters()")

    @override
    def export_from_session(
        self,
        session: Session,
        stops: Sequence[Stop],
        destination: Path,
    ) -> None:
        if session.new or session.dirty or session.deleted:
            raise RuntimeError("Commit pending ORM changes before exporting SQLite")

        iscp_records = list(
            session.scalars(select(IscpStop).options(selectinload(IscpStop.platforms)))
            .unique()
            .all()
        )
        matches = list(session.scalars(select(IscpMatch)).all())

        destination.unlink(missing_ok=True)
        with closing(sqlite3.connect(destination)) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            create_metadata_table(conn)
            write_metadata(conn, "gtfs-sk/stops")
            conn.executescript(MAIN_DDL)
            create_iscp_tables(conn)

            for stop in sorted(stops, key=lambda item: item.id):
                conn.execute(
                    "INSERT INTO stops VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        stop.id,
                        stop.iscp_id,
                        stop.name,
                        str(stop.kind),
                        stop.lat,
                        stop.lon,
                        stop.country,
                        stop.region,
                        stop.district,
                        stop.municipality,
                        _json_list(stop.modes),
                        _json_list(stop.amenities),
                        stop.original_name,
                        stop.source,
                        stop.original_id,
                    ),
                )
                for platform in sorted(stop.platforms, key=lambda item: item.id):
                    conn.execute(
                        "INSERT INTO platforms VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            platform.id,
                            platform.stop_id,
                            platform.name,
                            platform.lat,
                            platform.lon,
                            platform.country,
                            platform.region,
                            platform.district,
                            platform.municipality,
                            _json_list(platform.modes),
                            _json_list(platform.amenities),
                            platform.platform_code,
                            platform.original_name,
                            platform.source,
                            platform.original_id,
                        ),
                    )

            insert_iscp_records(conn, iscp_records)

            for match in sorted(matches, key=lambda item: item.stop_id):
                conn.execute(
                    "INSERT INTO iscp_matches VALUES (?,?,?,?)",
                    (match.stop_id, match.iscp_id, match.quality, match.distance_m),
                )

            conn.executescript(VIEW_DDL)
            conn.commit()

            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(f"SQLite export violates foreign keys: {violations}")
