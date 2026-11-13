# stops

**stops** is an open dataset of public transport stops in Slovakia — stops,
stations and their individual platforms, with coordinates, administrative
location (region, district, municipality), transport modes, amenities and,
where a match was found, the stop's identifier in the official ISCP
registry. It is built by the pipeline in this repository from open sources
and published in CSV, GeoJSON, GTFS and SQLite formats as part of the
[gtfs.sk](https://github.com/gtfs-sk) project.

## Download

You don't need to run anything — the dataset is published as
[GitHub Releases](https://github.com/gtfs-sk/stops/releases).

- **[Latest release](https://github.com/gtfs-sk/stops/releases/latest)** —
  all export files, a combined `stops-<version>.zip` bundle and
  `SHA256SUMS` with checksums for every asset.
- Releases are **monthly snapshots** of the source data, versioned with
  CalVer (see [Releases and versioning](#releases-and-versioning)).

## Exports

A build produces seven files in `data/export/`:

| File | Contents |
| --- | --- |
| `stops.csv` | Stops only, curated columns (12) |
| `platforms.csv` | Platforms only, curated columns (14) |
| `stops-and-platforms.csv` | Stops and platforms in one table, curated columns |
| `stops.geojson` | Stops and platforms as GeoJSON features |
| `stops.gtfs.zip` | ZIP containing a GTFS `stops.txt` |
| `stops.sqlite` | Dedicated SQLite export: stops, platforms, the ISCP registry tables and the match links |
| `iscp-register.zip` | The ISCP registry on its own: `iscp-stops.csv`, `iscp-platforms.csv`, `iscp-stops.geojson`, `iscp.sqlite` |

Use `stops.csv` for a list of stops, `platforms.csv` for boarding
locations, `stops-and-platforms.csv` when you want both as one table, and
`stops.sqlite` when you need all tables and relationships. The SQLite
export is built with an explicit, versioned schema — it is not a copy of
the pipeline's working database.

> [!NOTE]
> `stops.gtfs.zip` contains **only `stops.txt`** — it is not a complete
> GTFS Schedule feed (there are no routes, trips or stop times).

The full column-by-column reference for every file is in
**[docs/exports.md](docs/exports.md)**.

## Data model

The dataset has two record types:

- A **stop** is a stop or station. Whether a record is a station is a
  property of the record itself (its kind), not a separate flag.
- A **platform** is an individual boarding location belonging to a stop.
  A stop can have zero or more platforms.

Identifiers:

- `id` — the project's own stable identifier of a stop or platform row.
- `iscp_id` — the identifier of the record in the ISCP registry, where a
  match was found. It is distinct from `id`. Matches belong to stops;
  platforms are linked through their parent stop.
- In every public export, a platform points to its parent stop through
  `platform_parent_stop_id` (the internal database model calls the same
  relationship `stop_id`, and GTFS `stops.txt` uses `parent_station`,
  as the GTFS specification requires).

The ISCP registry itself is stored in its own tables (`iscp_stops`,
`iscp_platforms`) and published separately as `iscp-register.zip`; the
links between the registry and the canonical stops, including match
quality and distance, are in `iscp_matches`.

See [docs/exports.md](docs/exports.md#data-model-and-identifiers) for
the details.

## Sources and pipeline

```text
pipeline/crawl  →  pipeline/clean  →  pipeline/patch  →  pipeline/export
 (OSM, ISCP)       (cluster, dedup)    (ISCP matches,    (7 files)
                                        names)
```

- **OpenStreetMap** — the base source. The pipeline downloads the Slovakia
  extract from Geofabrik, filters it down to public transport features
  with `osmium`, and maps them to stops and platforms.
- **ISCP registry** ([iscp.sk](https://www.iscp.sk)) — the official
  Slovak register of bus stops. The crawler stores it faithfully in its
  own tables (all source fields, every GPS coordinate pair); a later
  patch step matches canonical stops against it to attach `iscp_id`s,
  and stop names are normalised using the official registry names.
- **GKÚ ZBGIS administrative boundaries** — used to assign each record
  its country, region, district and municipality.

After crawling, the pipeline clusters nearby records and deduplicates
platforms (`pipeline/clean/`), then applies the patches
(`pipeline/patch/`), and finally writes the export files
(`pipeline/export/`).

> The main exports contain the **processed** dataset. The ISCP registry
> is published separately and unchanged (as a normalized register) in
> `iscp-register.zip`.

## Building locally

Prerequisites:

- **Python 3.12**
- **`osmium-tool`** — the build shells out to the `osmium` command-line
  tool to filter the OSM extract. The Python `osmium` package in
  `requirements.txt` only provides bindings, not this binary.
  - macOS: `brew install osmium-tool`
  - Debian/Ubuntu: `sudo apt-get install osmium-tool`

```bash
git clone https://github.com/gtfs-sk/stops.git
cd stops

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

.venv/bin/python build.py
```

The build downloads the OSM extract, the ZBGIS boundaries and the ISCP
registry, runs the whole pipeline, and writes the results to
`data/export/` (the working database is `data/stops.sqlite`). Generated
data lives in `data/`, which is gitignored — published data only ever
ships in GitHub Releases.

## Development

Check dependencies are pinned in `requirements-dev.txt`:

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt

.venv/bin/ruff check .
.venv/bin/ruff format --check build.py models pipeline scripts
.venv/bin/pyright
.venv/bin/python -m compileall -q build.py models pipeline scripts
.venv/bin/python scripts/smoke_test.py
```

The smoke test runs every exporter against an empty database and
validates the output — no source data is downloaded. The same checks run
in CI on every push to `main` and every pull request
(`.github/workflows/check.yml`), and the release workflow runs them again
before building, so a release is never published from a red tree.

## Releases and versioning

Releases are built by `.github/workflows/release.yml`:

- automatically **once a month** (on the 1st at 03:00 UTC), or manually
  via `workflow_dispatch`,
- versioned with **CalVer**: the tag is the snapshot date of the data in
  the Europe/Bratislava timezone, `vYYYY.MM.DD` (for example
  `v2026.10.23`). A re-run on a day that already has a release gets a
  numeric suffix (`.2`, `.3`, …). There is deliberately no SemVer — the
  version describes the data snapshot, not the code.
- Before anything is published, the exports are validated
  (`scripts/validate_exports.py`): all seven files must exist, the CSV
  headers must match the documented schemas, the SQLite export must use
  the public schema, and row counts must agree across the CSV, GeoJSON,
  GTFS, SQLite and ISCP register exports.

Release notes list the stop and platform counts, the share of stops with
an ISCP id, the number of ISCP registry records, and the commit the
release was built from.

## Repository layout

```text
├── build.py            # orchestrator: crawl → clean → patch → export
├── models/             # SQLAlchemy models (stops, platforms, ISCP registry)
├── pipeline/
│   ├── crawl/          # data collectors (OSM, ISCP registry)
│   ├── clean/          # clustering and platform deduplication
│   ├── patch/          # ISCP matching, stop-name cleanup
│   └── export/         # CSV, GeoJSON, GTFS, SQLite and ISCP exporters
├── scripts/            # smoke test and export validator
├── www/                # static web explorer for the dataset
├── docs/               # exports reference (docs/exports.md)
└── data/               # generated (gitignored): cache, working DB, exports
```

## License

One file, two parts — see [LICENSE](LICENSE):

- **Code** (Part 1): MIT
- **Data** (Part 2): Open Database License (ODbL) 1.0

When you use the data, credit the project, for example:

> Contains data from gtfs.sk, licensed under ODbL 1.0.

The data itself comes from sources with their own attribution
requirements, in particular © OpenStreetMap contributors (ODbL).
