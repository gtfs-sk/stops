from pathlib import Path

# Registry acquisition lives in pipeline/crawl/iscp; this config only
# covers the matching step.
UNMATCHED_CSV = Path("data/iscp_unmatched.csv")

EXACT_MAX_M = 500.0  # normalized name equal, <= this apart -> "exact"
HIGH_MAX_M = 2000.0  # normalized name equal, <= this apart -> "high"
MEDIUM_MAX_M = 500.0  # fuzzy name, <= this apart -> "medium"
MEDIUM_MIN_SIMILARITY = 0.88
FALLBACK_RADIUS_M = 3000.0  # spatial fallback for municipality misses
GRID_CELL_DEG = 0.05  # spatial index cell size (~5 km)
