from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .text import normalize


@dataclass
class IscpRecord:
    """One row of the ISCP "globalne zastavky" registry."""

    cislo: str
    municipality: str
    names: list[str] = field(default_factory=list)
    lat: float | None = None
    lon: float | None = None
    cancelled: bool = False
    norm_names: set[str] = field(default_factory=set)
    norm_municipality: str = ""

    def reindex(self) -> None:
        self.norm_names = {n for n in (normalize(x) for x in self.names) if n}
        self.norm_municipality = normalize(self.municipality)

    def to_json(self) -> dict[str, object]:
        return {
            "cislo": self.cislo,
            "municipality": self.municipality,
            "names": self.names,
            "lat": self.lat,
            "lon": self.lon,
            "cancelled": self.cancelled,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> IscpRecord:
        rec = cls(
            cislo=data["cislo"],
            municipality=data["municipality"],
            names=data["names"],
            lat=data["lat"],
            lon=data["lon"],
            cancelled=data["cancelled"],
        )
        rec.reindex()
        return rec
