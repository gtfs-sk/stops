from __future__ import annotations

import csv
import logging
import math
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from tqdm.auto import tqdm

from models import IscpStop, Stop, StopKind
from pipeline.clean import distance_meters
from pipeline.text import normalize

from .config import (
    EXACT_MAX_M,
    FALLBACK_RADIUS_M,
    GRID_CELL_DEG,
    HIGH_MAX_M,
    MEDIUM_MAX_M,
    MEDIUM_MIN_SIMILARITY,
)

logger = logging.getLogger(__name__)

QUALITY_RANK = {"exact": 0, "high": 1, "medium": 2}


def stop_position(stop: Stop) -> tuple[float, float] | None:
    if stop.kind == StopKind.STATION:
        if stop.lat is None or stop.lon is None:
            return None
        return (stop.lat, stop.lon)
    plats = stop.platforms
    if not plats:
        return None
    return (
        sum(p.lat for p in plats) / len(plats),
        sum(p.lon for p in plats) / len(plats),
    )


def registry_name_variants(rec: IscpStop) -> list[str]:
    municipality = rec.municipality or ""
    local_part = rec.local_part or ""
    locality = rec.locality or ""
    return [
        value
        for value in [
            rec.name_ids or "",
            f"{municipality}, {locality}" if municipality and locality else "",
            f"{municipality}, {local_part}" if municipality and local_part else "",
            rec.name_mhd or "",
            locality,
            local_part,
        ]
        if value
    ]


def registry_norm_names(rec: IscpStop) -> set[str]:
    return {n for n in (normalize(x) for x in registry_name_variants(rec)) if n}


def stop_name_variants(stop: Stop) -> set[str]:
    out = {normalize(stop.name)}
    municipality = normalize(stop.municipality)
    full = normalize(stop.name)

    if (
        municipality
        and full.startswith(municipality)
        and (rest := full[len(municipality) :].strip(" ,"))
    ):
        out.add(rest)

    return {v for v in out if v}


def match_quality(
    name_vars: set[str],
    norm_names: set[str],
    pos: tuple[float, float] | None,
    rec: IscpStop,
) -> tuple[str, float | None] | None:
    dist = (
        distance_meters(pos, (rec.lat, rec.lon))
        if pos is not None and rec.lat is not None and rec.lon is not None
        else None
    )

    if name_vars & norm_names:
        if dist is None:
            return ("high", None)
        if dist <= EXACT_MAX_M:
            return ("exact", dist)
        if dist <= HIGH_MAX_M:
            return ("high", dist)
        return None

    if dist is not None and dist <= MEDIUM_MAX_M and norm_names:
        best = max(
            SequenceMatcher(None, v, rn).ratio() for v in name_vars for rn in norm_names
        )
        if best >= MEDIUM_MIN_SIMILARITY:
            return ("medium", dist)

    return None


class RegistryIndex:
    def __init__(self, records: list[IscpStop]) -> None:
        self.by_municipality: dict[str, list[IscpStop]] = defaultdict(list)
        self.by_name: dict[str, list[IscpStop]] = defaultdict(list)
        self.grid: dict[tuple[int, int], list[IscpStop]] = defaultdict(list)
        self.norm_names: dict[str, set[str]] = {}
        for rec in records:
            norm_names = registry_norm_names(rec)
            self.norm_names[rec.iscp_id] = norm_names
            self.by_municipality[normalize(rec.municipality)].append(rec)
            for norm_name in norm_names:
                self.by_name[norm_name].append(rec)
            if rec.lat is not None and rec.lon is not None:
                key = (
                    math.floor(rec.lat / GRID_CELL_DEG),
                    math.floor(rec.lon / GRID_CELL_DEG),
                )
                self.grid[key].append(rec)

    def candidates(self, stop: Stop, pos: tuple[float, float] | None) -> list[IscpStop]:
        seen: set[str] = set()
        out: list[IscpStop] = []
        for rec in self.by_municipality.get(normalize(stop.municipality), []):
            out.append(rec)
            seen.add(rec.iscp_id)
        if pos is not None:
            for rec in self.nearby(pos):
                if rec.iscp_id not in seen:
                    out.append(rec)
        return out

    def nearby(
        self, pos: tuple[float, float], radius_m: float = FALLBACK_RADIUS_M
    ) -> list[IscpStop]:
        lat, lon = pos
        center = (math.floor(lat / GRID_CELL_DEG), math.floor(lon / GRID_CELL_DEG))
        out: list[IscpStop] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for rec in self.grid.get((center[0] + dx, center[1] + dy), ()):
                    if (
                        rec.lat is not None
                        and rec.lon is not None
                        and distance_meters(pos, (rec.lat, rec.lon)) <= radius_m
                    ):
                        out.append(rec)
        return out

    def exact_name_match(self, name_vars: set[str]) -> list[IscpStop]:
        """
        Records matching the full normalized name.

        Fallback for records geography can't reach (no GPS). Requires a
        distinctive multi-word name unique in the registry.
        """

        found: dict[str, IscpStop] = {}
        for var in name_vars:
            if len(var.split()) < 2:
                continue
            for rec in self.by_name.get(var, ()):
                found.setdefault(rec.iscp_id, rec)

        if len(found) == 1:
            return list(found.values())

        return []


def match_stops(
    stops: list[Stop], index: RegistryIndex
) -> tuple[dict[str, tuple[IscpStop, str, float | None]], set[str]]:
    positions = {s.id: stop_position(s) for s in stops}
    name_vars = {s.id: stop_name_variants(s) for s in stops}

    proposals: list[tuple[int, float, str, IscpStop, str, float | None]] = []
    with_candidates: set[str] = set()
    for stop in tqdm(stops, desc="Matching ISCP ids", unit="stop"):
        pos = positions[stop.id]
        candidates = index.candidates(stop, pos)
        if candidates:
            with_candidates.add(stop.id)
        for rec in candidates:
            matched = match_quality(
                name_vars[stop.id], index.norm_names[rec.iscp_id], pos, rec
            )
            if matched is not None:
                quality, dist = matched
                proposals.append(
                    (
                        QUALITY_RANK[quality],
                        dist if dist is not None else float("inf"),
                        stop.id,
                        rec,
                        quality,
                        dist,
                    )
                )

    proposals.sort(key=lambda p: (p[0], p[1]))
    taken: set[str] = set()
    assigned: dict[str, tuple[IscpStop, str, float | None]] = {}

    for _, _, stop_id, rec, quality, dist in proposals:
        if stop_id in assigned or rec.iscp_id in taken:
            continue
        assigned[stop_id] = (rec, quality, dist)
        taken.add(rec.iscp_id)

    # fallback: exact name match without geography
    for stop in stops:
        if stop.id in assigned:
            continue
        matches = index.exact_name_match(name_vars[stop.id])
        if len(matches) == 1 and matches[0].iscp_id not in taken:
            assigned[stop.id] = (matches[0], "high", None)
            taken.add(matches[0].iscp_id)

    return assigned, with_candidates


def write_unmatched_csv(
    path: Path,
    stops: list[Stop],
    positions: dict[str, tuple[float, float] | None],
    assigned: dict[str, tuple[IscpStop, str, float | None]],
    with_candidates: set[str],
) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    unmatched = [s for s in stops if s.id not in assigned]

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "stop_id",
                "kind",
                "name",
                "municipality",
                "district",
                "lat",
                "lon",
                "reason",
            ]
        )
        for stop in unmatched:
            pos = positions[stop.id]
            writer.writerow(
                [
                    stop.id,
                    str(stop.kind),
                    stop.name,
                    stop.municipality or "",
                    stop.district or "",
                    f"{pos[0]:.6f}" if pos else "",
                    f"{pos[1]:.6f}" if pos else "",
                    "no registry candidates nearby"
                    if stop.id not in with_candidates
                    else "below match threshold",
                ]
            )

    return len(unmatched)
