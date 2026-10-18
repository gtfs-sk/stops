from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import override

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from models import Stop


class BaseExporter(ABC):
    filename: str

    @abstractmethod
    def export(
        self,
        stops: Sequence[Stop],
        destination: Path,
    ) -> None:
        pass


class BaseCsvExporter(BaseExporter, ABC):
    headers: Sequence[str]

    @abstractmethod
    def format_rows(self, stop: Stop) -> Iterator[Sequence[object]]:
        pass

    @override
    def export(self, stops: Sequence[Stop], destination: Path) -> None:
        with destination.open("w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(self.headers)
            for stop in stops:
                for row in self.format_rows(stop):
                    writer.writerow(row)


def run_exporters(
    session: Session,
    exporters: Sequence[type[BaseExporter]],
    output_dir: Path,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    stops = (
        session.scalars(select(Stop).options(selectinload(Stop.platforms)))
        .unique()
        .all()
    )

    for exporter_cls in exporters:
        exporter = exporter_cls()
        destination = output_dir / exporter.filename
        exporter.export(stops, destination)
