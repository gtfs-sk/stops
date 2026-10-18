from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from functools import partial
from html.parser import HTMLParser
from typing import final, override

import requests
from tqdm.auto import tqdm

from .config import (
    BASE_URL,
    CACHE_PATH,
    CACHE_TTL_DAYS,
    ENCODING,
    INDEX_URL,
    MAX_RETRIES,
    RATE_LIMIT_SECONDS,
    USER_AGENT,
    WORKERS,
)
from .models import IscpRecord
from .text import normalize

logger = logging.getLogger(__name__)

HEADER_ROLES = {
    "cislo": "number",
    "por c": None,
    "obec mesto": "municipality",
    "nazov 1": "municipality",
    "miestna cast": "local_part",
    "nazov 2": "local_part",
    "lokalita": "locality",
    "nazov 3": "locality",
    "nazov mhd": "name_mhd",
    "nazov ids": "name_ids",
    "okres": None,
    "stat": None,
    "okres cr": None,
    "dopr sys": "transport",
    "gps x e": "gps_x",
    "gps x": "gps_x",
    "gps y n": "gps_y",
    "gps y": "gps_y",
    "suradnice gps": "gps_combined",
    "platnost": None,
    "na mape": None,
}


class TableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    @override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []

    @override
    def handle_endtag(self, tag: str) -> None:
        if self.row is None:
            return

        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell))
            self.cell = None
        elif tag == "tr":
            self.rows.append(self.row)
            self.row = None

    @override
    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)


def clean_cell(text: str) -> str:
    return text.replace("\xa0", "").strip()


def first_float(text: str) -> float | None:
    for part in text.split():
        try:
            return float(part)
        except ValueError:
            pass
    return None


def build_name_variants(fields: dict[str, str]) -> list[str]:
    municipality = fields.get("municipality", "")
    local_part = fields.get("local_part", "")
    locality = fields.get("locality", "")

    return [
        v
        for v in [
            fields.get("name_ids", ""),
            f"{municipality}, {locality}" if municipality and locality else "",
            f"{municipality}, {local_part}" if municipality and local_part else "",
            fields.get("name_mhd", ""),
            locality,
            local_part,
        ]
        if v
    ]


def build_display_name(fields: dict[str, str]) -> str:
    if name_ids := fields.get("name_ids", ""):
        return name_ids
    if name_mhd := fields.get("name_mhd", ""):
        return name_mhd
    municipality = fields.get("municipality", "")
    locality = fields.get("locality", "") or fields.get("local_part", "")
    if municipality and locality:
        return f"{municipality}, {locality}"
    return municipality or locality


def parse_combined_gps(text: str) -> tuple[float | None, float | None]:
    lat = lon = None
    for part in text.replace(",", " ").split():
        try:
            value = float(part)
        except ValueError:
            continue
        if 47.0 <= value <= 50.0 and lat is None:
            lat = value
        elif 16.0 <= value <= 23.0 and lon is None:
            lon = value
    return lat, lon


def parse_coords(fields: dict[str, str]) -> tuple[float | None, float | None]:
    lon = first_float(fields.get("gps_x", ""))
    lat = first_float(fields.get("gps_y", ""))
    if lat is None or lon is None:
        combined_lat, combined_lon = parse_combined_gps(fields.get("gps_combined", ""))
        lat = lat if lat is not None else combined_lat
        lon = lon if lon is not None else combined_lon
    if lat is None or lon is None:
        return None, None
    return lat, lon


def parse_registry_file(html_text: str, filename: str = "") -> list[IscpRecord]:
    parser = TableParser()
    parser.feed(html_text)
    roles: list[str | None] | None = None
    records: list[IscpRecord] = []
    for row in parser.rows:
        cells = [clean_cell(c) for c in row]
        if roles is None:
            if any("íslo" in c for c in cells):
                roles = [HEADER_ROLES.get(normalize(c)) for c in cells]
                unknown = sorted(
                    {c for c in cells if c.strip() and normalize(c) not in HEADER_ROLES}
                )
                if unknown:
                    logger.debug("%s: unmapped columns: %s", filename, unknown)
                if "number" not in roles:
                    logger.warning(
                        "%s: header without Cislo column, skipping", filename
                    )
                    return []
            continue
        fields: dict[str, str] = {}
        for role, cell in zip(roles, cells):
            if role is not None:
                fields[role] = cell
        if not fields.get("number", "").isdigit():
            continue
        lat, lon = parse_coords(fields)
        rec = IscpRecord(
            cislo=fields["number"],
            municipality=fields.get("municipality", ""),
            names=build_name_variants(fields),
            lat=lat,
            lon=lon,
            cancelled="Zrušen" in fields.get("transport", ""),
            display_name=build_display_name(fields),
        )
        rec.reindex()
        records.append(rec)
    if roles is None:
        logger.warning("%s: no header row found", filename)
    return records


@final
class RateLimiter:
    def __init__(self, min_interval: float) -> None:
        self.min_interval = min_interval
        self.lock = threading.Lock()
        self.last = 0.0

    def wait(self) -> None:
        with self.lock:
            delay = self.last + self.min_interval - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.last = time.monotonic()


def http_get(url: str, limiter: RateLimiter) -> requests.Response:
    last_exc: Exception | None = None
    for attempt in range(MAX_RETRIES):
        limiter.wait()
        try:
            resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
            if resp.status_code in (429, 503):
                retry_after = resp.headers.get("Retry-After", "")
                wait = float(retry_after) if retry_after.isdigit() else 2.0**attempt
                logger.warning(
                    "%s -> HTTP %s, backing off %.0fs (attempt %d/%d)",
                    url,
                    resp.status_code,
                    wait,
                    attempt + 1,
                    MAX_RETRIES,
                )
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            wait = 2.0**attempt
            logger.warning(
                "%s -> %s, retrying in %.0fs (attempt %d/%d)",
                url,
                exc,
                wait,
                attempt + 1,
                MAX_RETRIES,
            )
            time.sleep(wait)
    raise RuntimeError(f"GET {url} failed after {MAX_RETRIES} attempts: {last_exc}")


def download_file(filename: str, limiter: RateLimiter) -> list[IscpRecord]:
    try:
        resp = http_get(BASE_URL + filename, limiter)
        return parse_registry_file(
            resp.content.decode(ENCODING, errors="replace"), filename
        )
    except Exception as exc:
        logger.error("Giving up on %s: %s", filename, exc)
        return []


def download_registry() -> list[IscpRecord]:
    limiter = RateLimiter(RATE_LIMIT_SECONDS)
    index_html = http_get(INDEX_URL, limiter).text
    files = sorted(
        {
            m.group(1).upper()
            for m in re.finditer(
                r'href="([A-Za-z]{2}\d+_SK\.HTM)"', index_html, re.IGNORECASE
            )
        }
    )

    logger.info("ISCP index lists %d municipality files", len(files))
    records: list[IscpRecord] = []
    download = partial(download_file, limiter=limiter)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        for result in tqdm(
            pool.map(download, files),
            total=len(files),
            desc="Downloading ISCP registry",
            unit="file",
        ):
            records.extend(result)

    return records


def load_registry() -> tuple[list[IscpRecord], str]:
    if CACHE_PATH.exists():
        mtime = datetime.fromtimestamp(CACHE_PATH.stat().st_mtime, tz=UTC)
        if datetime.now(UTC) - mtime < timedelta(days=CACHE_TTL_DAYS):
            try:
                payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                records = [IscpRecord.from_json(item) for item in payload["records"]]
                return records, f"cache from {payload['downloaded_at']}"
            except (json.JSONDecodeError, KeyError) as exc:
                logger.warning("Corrupt ISCP cache, re-downloading: %s", exc)

    records = download_registry()
    payload = {
        "downloaded_at": datetime.now(UTC).isoformat(),
        "records": [r.to_json() for r in records],
    }

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    return records, "fresh download"
