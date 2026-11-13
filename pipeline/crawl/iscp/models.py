from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pipeline.text import normalize


@dataclass
class IscpRegistryRecord:
    """One row of the ISCP registry, exactly as parsed from the source.

    This is the crawler's record shape. It keeps the source fields
    separate (no name collapsing beyond the derived display name and
    matching variants) and retains every GPS coordinate pair.
    """

    iscp_id: str
    municipality: str
    local_part: str = ""
    locality: str = ""
    name_ids: str = ""
    name_mhd: str = ""
    display_name: str = ""
    district: str = ""
    country: str = ""
    transport_system: str = ""
    validity: str = ""
    is_cancelled: bool = False
    source_file: str = ""
    coordinates: list[tuple[float, float]] = field(default_factory=list)
    raw_fields: dict[str, str] = field(default_factory=dict)
    names: list[str] = field(default_factory=list)
    norm_names: set[str] = field(default_factory=set)
    norm_municipality: str = ""

    @property
    def lat(self) -> float | None:
        return self.coordinates[0][0] if self.coordinates else None

    @property
    def lon(self) -> float | None:
        return self.coordinates[0][1] if self.coordinates else None

    def reindex(self) -> None:
        self.norm_names = {n for n in (normalize(x) for x in self.names) if n}
        self.norm_municipality = normalize(self.municipality)

    def to_json(self) -> dict[str, object]:
        return {
            "iscp_id": self.iscp_id,
            "municipality": self.municipality,
            "local_part": self.local_part,
            "locality": self.locality,
            "name_ids": self.name_ids,
            "name_mhd": self.name_mhd,
            "display_name": self.display_name,
            "district": self.district,
            "country": self.country,
            "transport_system": self.transport_system,
            "validity": self.validity,
            "is_cancelled": self.is_cancelled,
            "source_file": self.source_file,
            "coordinates": [list(pair) for pair in self.coordinates],
            "raw_fields": self.raw_fields,
            "names": self.names,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> IscpRegistryRecord:
        rec = cls(
            iscp_id=data["iscp_id"],
            municipality=data["municipality"],
            local_part=data.get("local_part", ""),
            locality=data.get("locality", ""),
            name_ids=data.get("name_ids", ""),
            name_mhd=data.get("name_mhd", ""),
            display_name=data.get("display_name", ""),
            district=data.get("district", ""),
            country=data.get("country", ""),
            transport_system=data.get("transport_system", ""),
            validity=data.get("validity", ""),
            is_cancelled=data.get("is_cancelled", False),
            source_file=data.get("source_file", ""),
            coordinates=[(float(lat), float(lon)) for lat, lon in data["coordinates"]],
            raw_fields=data.get("raw_fields", {}),
            names=data.get("names", []),
        )
        rec.reindex()
        return rec
