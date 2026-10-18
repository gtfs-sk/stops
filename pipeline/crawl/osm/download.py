from __future__ import annotations

import logging
import os
import shutil
import tempfile
import time
import urllib.request
import zipfile
from http.client import HTTPResponse
from pathlib import Path
from typing import cast

from tqdm.auto import tqdm

logger = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = ROOT / "data/cache/osm"
PBF_URL = "https://download.geofabrik.de/europe/slovakia-latest.osm.pbf"
PBF_PATH = CACHE_DIR / "slovakia-latest.osm.pbf"
MAX_AGE_SECONDS = 24 * 60 * 60
USER_AGENT = "gtfs-sk-stops/1.0 (https://github.com/gtfs-sk/stops)"


def download_file(url: str, destination: Path, *, description: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    temp_path: Path | None = None

    try:
        with urllib.request.urlopen(request, timeout=180) as opened:  # pyright: ignore[reportAny]
            response: HTTPResponse = cast(HTTPResponse, opened)
            total = int(response.headers.get("Content-Length") or 0) or None
            with (
                tempfile.NamedTemporaryFile(
                    dir=destination.parent, suffix=".part", delete=False
                ) as temp,
                tqdm(
                    total=total,
                    unit="B",
                    unit_scale=True,
                    unit_divisor=1024,
                    desc=description,
                ) as progress,
            ):
                temp_path = Path(temp.name)
                while chunk := response.read(1024 * 1024):
                    _ = temp.write(chunk)
                    _ = progress.update(len(chunk))

        if temp_path.stat().st_size == 0:
            raise ValueError(f"Empty download: {url}")

        os.replace(temp_path, destination)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def ensure_pbf() -> Path:
    override = os.environ.get("OSM_PBF_PATH")
    if override:
        path = Path(override).expanduser().resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        return path

    refresh = os.environ.get("OSM_REFRESH") == "1"
    cached = PBF_PATH.is_file() and PBF_PATH.stat().st_size > 0
    if (
        cached
        and not refresh
        and time.time() - PBF_PATH.stat().st_mtime < MAX_AGE_SECONDS
    ):
        return PBF_PATH

    try:
        logger.info("Downloading OSM PBF: %s", PBF_URL)
        download_file(PBF_URL, PBF_PATH, description="OSM PBF")
    except Exception:
        if cached:
            logger.warning("OSM download failed; using cached PBF", exc_info=True)
            return PBF_PATH
        raise

    return PBF_PATH


# Official GKÚ ZBGIS, base-resolution administrative boundaries in S-JTSK (JTSK03).
# Source: https://www.gku.sk/gku/produkty-sluzby/na-stiahnutie/zbgis.html
GKU_URL = "https://opendata.skgeodesy.sk/static/ZBGIS/usj/ah_gpkg_0_sjtsk03.zip"
ADMIN_DIR = CACHE_DIR / "admin" / "gku-zbgis"
ARCHIVE_PATH = ADMIN_DIR / "ah_gpkg_0_sjtsk03.zip"
EXTRACT_DIR = ADMIN_DIR / "extracted"


def _valid_cached_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []

    return sorted(
        path
        for path in directory.rglob("*.gpkg")
        if path.is_file() and path.stat().st_size
    )


def ensure_gpkg_files() -> list[Path]:
    custom_file = os.environ.get("GKU_ADMIN_GPKG")
    if custom_file:
        path = Path(custom_file).expanduser().resolve()
        if not path.is_file() or path.suffix.lower() != ".gpkg":
            raise FileNotFoundError(f"Invalid GKU_ADMIN_GPKG: {path}")
        return [path]

    custom_dir = os.environ.get("GKU_ADMIN_DIR")
    if custom_dir:
        paths = _valid_cached_files(Path(custom_dir).expanduser().resolve())
        if not paths:
            raise FileNotFoundError(f"No .gpkg files in GKU_ADMIN_DIR={custom_dir}")
        return paths

    ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    refresh = os.environ.get("GKU_ADMIN_REFRESH") == "1"
    if refresh or not ARCHIVE_PATH.is_file() or ARCHIVE_PATH.stat().st_size == 0:
        logger.info("Downloading GKÚ ZBGIS administrative boundaries: %s", GKU_URL)
        download_file(GKU_URL, ARCHIVE_PATH, description="GKÚ ZBGIS boundaries")

    existing = _valid_cached_files(EXTRACT_DIR)
    newest_extracted = max((p.stat().st_mtime for p in existing), default=0)
    if existing and newest_extracted >= ARCHIVE_PATH.stat().st_mtime:
        return existing

    with tempfile.TemporaryDirectory(prefix="gku-extract-", dir=ADMIN_DIR) as temporary:
        work = Path(temporary) / "files"
        work.mkdir()
        with zipfile.ZipFile(ARCHIVE_PATH) as archive:
            members = [
                m
                for m in archive.infolist()
                if not m.is_dir() and m.filename.lower().endswith(".gpkg")
            ]
            if not members:
                raise ValueError(f"GKÚ archive contains no .gpkg files: {ARCHIVE_PATH}")

            for member in tqdm(members, desc="Extracting GKÚ GeoPackages", unit="file"):
                filename = Path(member.filename.replace("\\", "/")).name
                target = work / filename
                if target.exists():
                    raise ValueError(
                        f"Duplicate GeoPackage file name in archive: {filename}"
                    )
                with archive.open(member) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination, length=1024 * 1024)
                if target.stat().st_size == 0:
                    raise ValueError(f"Empty GeoPackage extracted: {member.filename}")

        old = ADMIN_DIR / "extracted-old"
        if old.exists():
            shutil.rmtree(old)
        if EXTRACT_DIR.exists():
            _ = EXTRACT_DIR.rename(old)

        try:
            _ = work.rename(EXTRACT_DIR)
        except Exception:
            if old.exists():
                _ = old.rename(EXTRACT_DIR)
            raise

        if old.exists():
            shutil.rmtree(old)

    return _valid_cached_files(EXTRACT_DIR)
