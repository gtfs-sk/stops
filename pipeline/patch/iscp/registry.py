from __future__ import annotations

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone
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

logger = logging.getLogger(__name__)

# Column positions in the registry tables.
COL_NUMBER = 1  # Cislo - the official stop ID
COL_MUNICIPALITY = 2  # Obec/mesto
COL_LOCAL_PART = 3  # Miestna cast
COL_LOCALITY = 4  # Lokalita
COL_NAME_MHD = 5  # Nazov MHD
COL_NAME_IDS = 6  # Nazov IDS
COL_TRANSPORT = 10  # Dopr.sys. ("Zrusena" marks cancelled stops)
COL_GPS_X = 11  # GPS-X(E) = longitude
COL_GPS_Y = 12  # GPS-Y(N) = latitude


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


def name_variants(cells: list[str]) -> list[str]:
    municipality = cells[COL_MUNICIPALITY]
    local_part = cells[COL_LOCAL_PART]
    locality = cells[COL_LOCALITY]

    return [
        v
        for v in [
            cells[COL_NAME_IDS],
            f"{municipality}, {locality}" if municipality and locality else "",
            f"{municipality}, {local_part}" if municipality and local_part else "",
            cells[COL_NAME_MHD],
            locality,
            local_part,
        ]
        if v
    ]


def parse_registry_file(html_text: str) -> list[IscpRecord]:
    parser = TableParser()
    parser.feed(html_text)
    records: list[IscpRecord] = []
    for row in parser.rows:
        cells = [clean_cell(c) for c in row]
        if (
            len(cells) < 15
            or cells[COL_NUMBER] == "Číslo"
            or not cells[COL_NUMBER].isdigit()
        ):
            continue

        rec = IscpRecord(
            cislo=cells[COL_NUMBER],
            municipality=cells[COL_MUNICIPALITY],
            names=name_variants(cells),
            lon=first_float(cells[COL_GPS_X]),
            lat=first_float(cells[COL_GPS_Y]),
            cancelled="Zrušen" in cells[COL_TRANSPORT],
        )

        rec.reindex()
        records.append(rec)
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
        return parse_registry_file(resp.content.decode(ENCODING, errors="replace"))
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
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "records": [r.to_json() for r in records],
    }

    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    return records, "fresh download"
