"""Standalone ISCP registry export.

Publishes the registry exactly as stored in iscp_stops / iscp_platforms
-- every record, including cancelled ones -- as one ZIP asset containing
CSV, GeoJSON and SQLite forms of the same two tables.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import tempfile
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import final, override
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from models import IscpStop, Stop
from pipeline.export import BaseExporter

from .records import (
    ISCP_PLATFORMS_HEADER,
    ISCP_STOPS_HEADER,
    iscp_stop_values,
)
from .sqlite import create_iscp_tables, create_metadata_table, insert_iscp_records
from .sqlite import write_metadata as write_sqlite_metadata


def _csv_text(header: Sequence[str], rows: Sequence[Sequence[object]]) -> str:
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(header)
    writer.writerows(rows)
    return stream.getvalue()


def _geojson(records: Sequence[IscpStop]) -> str:
    features: list[dict[str, object]] = []
    for record in sorted(records, key=lambda item: item.iscp_id):
        properties: dict[str, object] = {
            "iscp_id": record.iscp_id,
            "name": record.display_name,
            "municipality": record.municipality,
            "local_part": record.local_part,
            "locality": record.locality,
            "name_ids": record.name_ids,
            "name_mhd": record.name_mhd,
            "district": record.district,
            "country": record.country,
            "transport_system": record.transport_system,
            "validity": record.validity,
            "is_cancelled": record.is_cancelled,
        }
        if not record.platforms:
            features.append(
                {"type": "Feature", "geometry": None, "properties": properties}
            )
        for platform in sorted(record.platforms, key=lambda item: item.position):
            features.append(
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [platform.lon, platform.lat],
                    },
                    "properties": {**properties, "position": platform.position},
                }
            )

    return json.dumps(
        {"type": "FeatureCollection", "features": features}, ensure_ascii=False
    )


def _sqlite_bytes(records: Sequence[IscpStop], directory: Path) -> bytes:
    path = directory / "iscp.sqlite"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        create_metadata_table(conn)
        write_sqlite_metadata(conn, "iscp-registry")
        create_iscp_tables(conn)
        insert_iscp_records(conn, records)
        conn.commit()
    return path.read_bytes()


@final
class IscpRegisterExporter(BaseExporter):
    filename = "iscp-register.zip"

    @override
    def export(self, stops: Sequence[Stop], destination: Path) -> None:
        raise RuntimeError(
            "IscpRegisterExporter requires a session; use run_exporters()"
        )

    @override
    def export_from_session(
        self,
        session: Session,
        stops: Sequence[Stop],
        destination: Path,
    ) -> None:
        records = list(
            session.scalars(select(IscpStop).options(selectinload(IscpStop.platforms)))
            .unique()
            .all()
        )
        records.sort(key=lambda item: item.iscp_id)

        stop_rows = [iscp_stop_values(record) for record in records]
        platform_rows = [
            (record.iscp_id, platform.position, platform.lat, platform.lon)
            for record in records
            for platform in sorted(record.platforms, key=lambda item: item.position)
        ]

        with tempfile.TemporaryDirectory() as tmp:
            sqlite_bytes = _sqlite_bytes(records, Path(tmp))
            with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr(
                    "iscp-stops.csv", _csv_text(ISCP_STOPS_HEADER, stop_rows)
                )
                archive.writestr(
                    "iscp-platforms.csv",
                    _csv_text(ISCP_PLATFORMS_HEADER, platform_rows),
                )
                archive.writestr("iscp-stops.geojson", _geojson(records))
                archive.writestr("iscp.sqlite", sqlite_bytes)
