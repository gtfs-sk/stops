from pathlib import Path

INDEX_URL = "http://www.iscp.sk/pages/glob_z/work/OBCE_SKK.HTM"
BASE_URL = "http://www.iscp.sk/pages/glob_z/work/"
CACHE_PATH = Path("data/cache/iscp_registry.json")
CACHE_SCHEMA_VERSION = 2
CACHE_TTL_DAYS = 30
ENCODING = "windows-1250"

WORKERS = 4
RATE_LIMIT_SECONDS = 0.15  # min delay between requests, shared across threads
MAX_RETRIES = 5
USER_AGENT = "gtfs.sk iscp crawler"
