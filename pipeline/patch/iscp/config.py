from pathlib import Path

INDEX_URL = "http://www.iscp.sk/pages/glob_z/work/OBCE_SKK.HTM"
BASE_URL = "http://www.iscp.sk/pages/glob_z/work/"
CACHE_PATH = Path("data/cache/iscp_registry.json")
CACHE_TTL_DAYS = 30
UNMATCHED_CSV = Path("data/iscp_unmatched.csv")
ENCODING = "windows-1250"

WORKERS = 4
RATE_LIMIT_SECONDS = 0.5  # min delay between requests, shared across threads
MAX_RETRIES = 5
USER_AGENT = "gtfs.sk iscp matcher"

EXACT_MAX_M = 500.0  # normalized name equal, <= this apart -> "exact"
HIGH_MAX_M = 2000.0  # normalized name equal, <= this apart -> "high"
MEDIUM_MAX_M = 500.0  # fuzzy name, <= this apart -> "medium"
MEDIUM_MIN_SIMILARITY = 0.88
FALLBACK_RADIUS_M = 3000.0  # spatial fallback for municipality misses
GRID_CELL_DEG = 0.05  # spatial index cell size (~5 km)
