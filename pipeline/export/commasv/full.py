from __future__ import annotations

import csv
import json
from collections.abc import Sequence
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import ClassVar, Literal, final, override
from uuid import UUID

from sqlalchemy import inspect

from models import Platform, Stop
from pipeline.export import BaseExporter


def columns(model: type[Stop | Platform]) -> list[str]:
    return [column.key for column in inspect(model).column_attrs]


def serialize(value: object) -> object:
    if value is None:
        return ""

    if isinstance(value, Enum):
        return value.value

    if isinstance(value, (date, datetime, time)):
        return value.isoformat()

    if isinstance(value, (Decimal, UUID)):
        return str(value)

    if isinstance(value, (dict, list, tuple, set)):
        if isinstance(value, set):
            value = sorted(value, key=str)
        return json.dumps(
            value, ensure_ascii=False, default=lambda item: str(serialize(item))
        )

    if isinstance(value, bytes):
        return value.hex()

    return value


class FullCsvExporter(BaseExporter):
    include: ClassVar[Literal["stops", "platforms", "both"]] = "both"

    @override
    def export(self, stops: Sequence[Stop], destination: Path) -> None:
        stop_cols = columns(Stop)
        platform_cols = columns(Platform)

        if self.include == "stops":
            headers = stop_cols
        elif self.include == "platforms":
            headers = platform_cols
        else:
            headers = ["record_type", *dict.fromkeys([*stop_cols, *platform_cols])]
            if "record_type" in stop_cols or "record_type" in platform_cols:
                raise ValueError("'record_type' conflicts with a mapped model column")

        with destination.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers)
            writer.writeheader()

            for stop in sorted(stops, key=lambda item: str(item.id)):
                if self.include in ("stops", "both"):
                    stop_row = {key: serialize(getattr(stop, key)) for key in stop_cols}
                    if self.include == "both":
                        stop_row = {"record_type": "stop", **stop_row}
                    writer.writerow(stop_row)

                if self.include in ("platforms", "both"):
                    for platform in sorted(
                        stop.platforms, key=lambda item: str(item.id)
                    ):
                        platform_row = {
                            key: serialize(getattr(platform, key))
                            for key in platform_cols
                        }
                        if self.include == "both":
                            platform_row = {"record_type": "platform", **platform_row}
                        writer.writerow(platform_row)


@final
class FullStopsCsvExporter(FullCsvExporter):
    filename = "stops.full.csv"
    include = "stops"


@final
class FullPlatformsCsvExporter(FullCsvExporter):
    filename = "platforms.full.csv"
    include = "platforms"


@final
class FullCombinedCsvExporter(FullCsvExporter):
    filename = "stops-and-platforms.full.csv"
    include = "both"
