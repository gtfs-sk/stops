from __future__ import annotations

from collections.abc import Iterable
from enum import StrEnum

from models.transport_mode import TransportMode


class TransportGroup(StrEnum):
    """
    Only heavy-rail passenger platforms are kept separate from other transit.

    UNKNOWN / MIXED are intentionally never silently classified as OTHER.
    """

    RAILWAY = "railway"
    OTHER = "other"
    UNKNOWN = "unknown"
    MIXED = "mixed"


def transport_group(modes: Iterable[TransportMode]) -> TransportGroup:
    values = set(modes)
    if not values:
        return TransportGroup.UNKNOWN
    if TransportMode.TRAIN in values:
        return TransportGroup.MIXED if len(values) > 1 else TransportGroup.RAILWAY

    return TransportGroup.OTHER
