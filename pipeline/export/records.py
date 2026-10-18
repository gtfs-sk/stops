from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Literal

from models import Platform, Stop

LocationKind = Literal["station", "stop", "platform"]


def iter_locations(
    stops: Sequence[Stop],
) -> Iterator[tuple[Stop | Platform, LocationKind]]:
    for stop in stops:
        yield stop, "station" if stop.is_station else "stop"

        for platform in stop.platforms:
            yield platform, "platform"
