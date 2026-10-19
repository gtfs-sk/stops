from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from contextlib import closing
from pathlib import Path
from typing import final, override

from sqlalchemy.orm import Session

from models import Stop
from pipeline.export import BaseExporter


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

        source = session.connection().connection.driver_connection
        if not isinstance(source, sqlite3.Connection):
            raise TypeError("Expected the standard sqlite3 driver")

        if source.in_transaction:
            raise RuntimeError(
                "Commit the SQLite transaction before exporting a snapshot"
            )

        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)
