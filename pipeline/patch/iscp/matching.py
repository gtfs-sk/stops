from __future__ import annotations

import csv
import logging
import math
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

from tqdm.auto import tqdm

from models import Stop, StopKind
from pipeline.clean import distance_meters

from .config import (
    EXACT_MAX_M,
    FALLBACK_RADIUS_M,
    GRID_CELL_DEG,
    HIGH_MAX_M,
    MEDIUM_MAX_M,
    MEDIUM_MIN_SIMILARITY,
)
from .models import IscpRecord
from .text import normalize

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


def stop_name_variants(stop: Stop) -> set[str]:
    out = {normalize(stop.name)}
    municipality = normalize(stop.municipality)
    full = normalize(stop.name)

    if municipality and full.startswith(municipality):
        if rest := full[len(municipality) :].strip(" ,"):
            out.add(rest)

    return {v for v in out if v}


def match_quality(
    name_vars: set[str], pos: tuple[float, float] | None, rec: IscpRecord
) -> tuple[str, float] | None:
    dist = (
        distance_meters(pos, (rec.lat, rec.lon))
        if pos is not None and rec.lat is not None and rec.lon is not None
        else None
    )

    if name_vars & rec.norm_names:
        if dist is None:
            return ("high", float("inf"))
        if dist <= EXACT_MAX_M:
            return ("exact", dist)
        if dist <= HIGH_MAX_M:
            return ("high", dist)
        return None

    if dist is not None and dist <= MEDIUM_MAX_M and rec.norm_names:
        best = max(
            SequenceMatcher(None, v, rn).ratio()
            for v in name_vars
            for rn in rec.norm_names
        )
        if best >= MEDIUM_MIN_SIMILARITY:
            return ("medium", dist)

    return None


class RegistryIndex:
    def __init__(self, records: list[IscpRecord]) -> None:
        self.by_municipality: dict[str, list[IscpRecord]] = defaultdict(list)
        self.grid: dict[tuple[int, int], list[IscpRecord]] = defaultdict(list)
        for rec in records:
            self.by_municipality[rec.norm_municipality].append(rec)
            if rec.lat is not None and rec.lon is not None:
                key = (
                    math.floor(rec.lat / GRID_CELL_DEG),
                    math.floor(rec.lon / GRID_CELL_DEG),
                )
                self.grid[key].append(rec)

    def candidates(
        self, stop: Stop, pos: tuple[float, float] | None
    ) -> list[IscpRecord]:
        seen: set[str] = set()
        out: list[IscpRecord] = []
        for rec in self.by_municipality.get(normalize(stop.municipality), []):
            out.append(rec)
            seen.add(rec.cislo)
        if pos is not None:
            for rec in self.nearby(pos):
                if rec.cislo not in seen:
                    out.append(rec)
        return out

    def nearby(
        self, pos: tuple[float, float], radius_m: float = FALLBACK_RADIUS_M
    ) -> list[IscpRecord]:
        lat, lon = pos
        center = (math.floor(lat / GRID_CELL_DEG), math.floor(lon / GRID_CELL_DEG))
        out: list[IscpRecord] = []
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


def match_stops(
    stops: list[Stop], index: RegistryIndex
) -> tuple[dict[str, tuple[IscpRecord, str]], set[str]]:
    positions = {s.id: stop_position(s) for s in stops}
    name_vars = {s.id: stop_name_variants(s) for s in stops}

    proposals: list[tuple[int, float, str, IscpRecord, str]] = []
    with_candidates: set[str] = set()
    for stop in tqdm(stops, desc="Matching ISCP ids", unit="stop"):
        pos = positions[stop.id]
        candidates = index.candidates(stop, pos)
        if candidates:
            with_candidates.add(stop.id)
        for rec in candidates:
            matched = match_quality(name_vars[stop.id], pos, rec)
            if matched is not None:
                quality, dist = matched
                proposals.append((QUALITY_RANK[quality], dist, stop.id, rec, quality))

    proposals.sort(key=lambda p: (p[0], p[1]))
    taken: set[str] = set()
    assigned: dict[str, tuple[IscpRecord, str]] = {}

    for _, _, stop_id, rec, quality in proposals:
        if stop_id in assigned or rec.cislo in taken:
            continue
        assigned[stop_id] = (rec, quality)
        taken.add(rec.cislo)

    return assigned, with_candidates


def write_unmatched_csv(
    path: Path,
    stops: list[Stop],
    positions: dict[str, tuple[float, float] | None],
    assigned: dict[str, tuple[IscpRecord, str]],
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
