"""
Normalize stop and platform names to 'Municipality, Name' form.

The pre-normalization name is kept in `original_name`. When the ISCP
registry is available, matched stops (iscp_id) get their official
registry name (cosmetic cleanup only); everything else is normalized
heuristically.

Heuristic: only bare single-segment stubs get the municipality
prepended ('Dukelská' -> 'Modra, Dukelská'); already structured names
are trusted as is ('Oravská priehrada, koruna' stays).

Train stations (TransportMode.TRAIN in modes) are never renamed —
their railway names are already official. This is the only exception.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from typing import final, override

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import Stop
from models.transport_mode import TransportMode
from pipeline.patch import BasePatcher
from pipeline.patch.iscp import load_registry
from pipeline.patch.iscp.text import normalize

logger = logging.getLogger(__name__)


def prettify_name(name: str) -> str:
    """
    Clean up legacy 'Obec,,časť' formatting.

    - 'Alekšince,,Lahne II' -> 'Alekšince, Lahne II'
    - 'Abrahámovce [KK],,OcÚ' -> 'Abrahámovce, OcÚ'
    - 'Aňal.Záhrady/Nesvady,rázc.' -> 'Aňal. Záhrady / Nesvady, rázc.'
    - 'Bajč,Kotelnica,ŠM rázc.' -> 'Bajč, Kotelnica, ŠM rázc.'
    """

    name = re.sub(r"\s*\[[^\]]*\]", "", name)
    cleaned = []
    for segment in name.split(","):
        segment = segment.strip()
        if not segment:
            continue
        segment = re.sub(r"\s*/\s*", " / ", segment)
        segment = re.sub(r"\.([A-ZÁČĎÉÍĹĽŇÓŔŠŤÚÝŽ])", r". \1", segment)
        segment = re.sub(r"\s+", " ", segment).strip()
        if segment:
            cleaned.append(segment)

    return ", ".join(cleaned)


def build_parent_cities(municipalities: Iterable[str | None]) -> dict[str, str]:
    """
    Map hyphenated district names to their parent city.

    'Bratislava-Vajnory' -> 'Bratislava' (many sibling districts share
    the prefix); 'Belá-Dulice' stays (prefix 'Belá' has no other
    hyphenated child, so it's a standalone municipality).
    Returns {normalized child name: parent city spelling}.
    """

    originals: dict[str, str] = {}
    for municipality in municipalities:
        municipality = (municipality or "").strip()
        if municipality:
            originals.setdefault(normalize(municipality), municipality)

    children: dict[str, set[str]] = {}
    for norm in originals:
        if "-" in norm:
            prefix = norm.split("-", 1)[0].strip()
            if prefix and prefix != norm:
                children.setdefault(prefix, set()).add(norm)

    parents: dict[str, str] = {}
    for prefix, kids in children.items():
        if len(kids) >= 2 and prefix in originals:
            for kid in kids:
                parents[kid] = originals[prefix]

    return parents


def parent_city_from_district(
    municipality: str | None, district: str | None
) -> str | None:
    """
    Derive the parent city from the district name.

    'Bratislava-Vajnory' -> 'Bratislava'.
    Returns None when the pattern doesn't apply.
    """

    municipality = (municipality or "").strip()
    district = (district or "").strip()

    if not municipality or not district:
        return None

    match = re.match(r"^(.+?)\s+[IVXLCDM]+$", district)
    if not match:
        return None

    parent = match.group(1).strip()
    if parent and municipality.startswith(parent + "-"):
        return parent
    return None


def display_municipality(stop: Stop, parents: dict[str, str]) -> str:
    """
    Municipality name used for display.

    City districts collapse to their parent city ('Bratislava-Vajnory'
    -> 'Bratislava'): via the district name first ('Bratislava III'),
    sibling districts as fallback.
    """

    municipality = (stop.municipality or "").strip()
    if parent := parent_city_from_district(municipality, stop.district):
        return parent

    return parents.get(normalize(municipality), municipality)


def canonical_name(
    name: str | None,
    municipality: str | None,
    district: str | None,
    parents: dict[str, str],
) -> str:
    """
    Normalize the name.

    Only bare single-segment stubs get the municipality prepended;
    already structured names are trusted as is.

    - 'Dukelská' (Modra) -> 'Modra, Dukelská'
    - 'Oravská priehrada, koruna' -> unchanged
    - 'Bratislava-Vajnory, X' -> 'Bratislava, X'
    """

    name = (name or "").strip()
    municipality = (municipality or "").strip()
    if not name or not municipality:
        return name or municipality

    segments = [s for s in (s.strip() for s in name.split(",")) if s]
    if not segments:
        return municipality

    first_norm = normalize(segments[0])
    if first_norm in parents:
        segments[0] = parents[first_norm]
    elif parent := parent_city_from_district(segments[0], district):
        segments[0] = parent
    elif first_norm == normalize(municipality):
        segments[0] = municipality

    if len(segments) > 1:
        return ", ".join(segments)

    rest = re.sub(
        rf"^{re.escape(municipality)}(?=$|[\s,.\-])\s*[,.\-]?\s*",
        "",
        segments[0],
        count=1,
        flags=re.IGNORECASE,
    )

    trailing = re.search(
        rf"\s*[,.\-]?\s*{re.escape(municipality)}$", rest, flags=re.IGNORECASE
    )
    if trailing and trailing.start() > 0:
        rest = rest[: trailing.start()].rstrip()

    rest = rest.strip()
    if not rest or rest.lower() == municipality.lower():
        return municipality
    return f"{municipality}, {rest}"


def load_official_names() -> dict[str, str]:
    try:
        records, _ = load_registry()
    except Exception as exc:
        logger.warning("ISCP registry unavailable, heuristics only: %s", exc)
        return {}
    return {
        r.cislo: r.display_name for r in records if not r.cancelled and r.display_name
    }


@final
class StopNamePatcher(BasePatcher):
    name = "stop_names"

    @override
    def patch(self, session: Session) -> None:
        official = load_official_names()
        if official:
            logger.info("Official ISCP names available for %d stops", len(official))
        else:
            logger.info("No official names, normalizing heuristically")

        stops = list(session.scalars(select(Stop)).all())
        parent_cities = build_parent_cities(s.municipality for s in stops)
        if parent_cities:
            detected = sorted(set(parent_cities.values()))
            logger.info("Parent cities detected: %s", ", ".join(detected))

        renamed = 0
        skipped = 0
        collapsed = 0
        for stop in stops:
            if TransportMode.TRAIN in (stop.modes or []):
                skipped += 1
                continue

            naming_municipality = display_municipality(stop, parent_cities)
            old_municipality = (stop.municipality or "").strip()

            if naming_municipality != old_municipality:
                collapsed += 1

            if stop.iscp_id and stop.iscp_id in official:
                new_name = prettify_name(official[stop.iscp_id])
                if old_municipality and old_municipality != naming_municipality:
                    new_name = re.sub(
                        rf"^{re.escape(old_municipality)}(?=$|[\s,.\-])",
                        naming_municipality,
                        new_name,
                        count=1,
                    )
            else:
                new_name = canonical_name(
                    prettify_name(stop.name),
                    naming_municipality,
                    stop.district,
                    parent_cities,
                )
            if new_name != stop.name:
                stop.original_name = stop.name
                stop.name = new_name
                renamed += 1
            for platform in stop.platforms:
                if platform.name != new_name:
                    platform.original_name = platform.name
                    platform.name = new_name

        logger.info(
            "Renamed %d/%d stops (%d train skipped, %d district-collapsed)",
            renamed,
            len(stops),
            skipped,
            collapsed,
        )
