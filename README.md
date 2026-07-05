# temperature-tracker — NDFD heat-forecast data pipeline

Fetches NOAA/National Weather Service **NDFD** forecasts three times a day,
reformats them into map-ready GeoJSON, and publishes them via GitHub Pages.

**This repo is the data engine.** The public-facing map lives in the graphics-rig
(`cbs-news-data-projects/graphics-rig` → `projects/2026/heat-tracker`, deployed at
`projects.cbsnews.com/projects/2026/heat-tracker/`) and consumes this repo's
published GeoJSON at runtime. A reference viewer is kept here (`viewer/`) as a
development/rollback view.

## Architecture

Three decoupled stages that talk only through files:

| Stage | Does | Code | Writes |
|---|---|---|---|
| **Fetch** | download NDFD GRIB2 (CONUS + AK + HI) | `scripts/fetch_ndfd.py` | `data/raw/*.bin` (not committed) |
| **Reformat** | decode → slim, map-ready GeoJSON | `scripts/build_heat.py` + `utils/heat.py` | `data/processed/*.geojson` (committed) |
| **Publish** | deploy data (+ reference viewer) to Pages | `deploy` job in `heat-data.yml` | `cbs-news-data.github.io/temperature-tracker/data/…` |

Consumers only ever read the published URLs — presentation can change anywhere
without touching this pipeline.

## Data contract (for downstream consumers — the graphics-rig embed)

Treat this as a **frozen interface**; breaking changes require coordinating with
every consumer:

- **URLs:** `https://cbs-news-data.github.io/temperature-tracker/data/{heat,feelslike,warmnight}_{points,counties}.geojson`
- **Cadence:** refreshed 3× daily (9:23 / 15:23 / 0:23 UTC + deploy + CDN; GitHub
  cron runs 15–75 min late). Consumers should fetch with `cache: "no-cache"`.
- **Points features:** `properties = { name_state, day1..dayN }` — whole-°F ints
  or `null` (no data); N varies (typically 6–7). Coords, 4 decimals.
- **Counties features:** `properties = { GEOID, NAMELSAD, day1..dayN }` —
  `NAMELSAD` is the full legal name ("Cook County", "Orleans Parish",
  "Anchorage Municipality").
- **Metadata (both points and counties files):** `metadata.days[]` maps `dayN` to
  `seq`, `fcst_date` (the date the value describes), `valid_utc`; apt products add
  `n_hours` + `valid_start_utc` (thin-bucket flagging). `metadata.issued_utc` =
  when NWS generated the forecast.
- **Semantics to preserve in any renderer:** label days off `fcst_date`, never the
  `dayN` index ("today" expires mid-day); warm-night `fcst_date` = the evening the
  night begins; feels-like is NWS apparent temperature (Heat Index categories apply
  to it, not to raw temperature); hide `null`s rather than painting them cold.
- **CORS:** GitHub Pages serves `Access-Control-Allow-Origin: *`.

## Products

| `--product` | element | what | prefix |
|---|---|---|---|
| `temp` | `maxt` | daytime **maximum temperature** (one grid/day, as NWS ships it) | `heat_*` |
| `feelslike` | `apt` | **daily-max apparent / "feels-like"** temp (hourly → per-cell daily max) | `feelslike_*` |
| `warmnight` | `apt` | **overnight-min** apparent temp (local 8pm–8am; drives heat-wave mortality) | `warmnight_*` |

`apt` = NWS apparent temperature: heat index when hot, wind chill when cold,
plain air temp in between.

## Automation

`.github/workflows/heat-data.yml` — the whole pipeline, three times daily:

- **`build-data` job** — fetch, build all three products (each place/county routed
  to its own sector grid: AK→alaska, HI→hawaii, else CONUS), rebase-and-commit
  `data/processed` + `data/reference`, stage the site artifact.
- **`deploy` job** — publish to GitHub Pages with an automatic retry (the Pages
  backend intermittently answers "Deployment failed, try again later"; a failed
  attempt waits 3 minutes and retries). Delete this job and the fetch/commit
  pipeline is untouched.

## Local development

```bash
uv sync --extra heat --extra dev   # pygrib needs system eccodes: brew install eccodes
uv run pytest                      # 13 unit tests (transform core + shaping)

uv run python scripts/make_reference.py                 # once (~yearly): places + counties
uv run python scripts/validate_live.py                  # live-GRIB smoke test (run before trusting changes)
uv run python scripts/fetch_ndfd.py --elements maxt,apt # days 1–7, CONUS + AK + HI
uv run python scripts/build_heat.py --product temp      # + feelslike, warmnight

# reference viewer against local outputs:
uv run python -m http.server 8000
# open http://localhost:8000/viewer/index.html?data=../data/processed
```

`validate_live.py` checks the three live-decode assumptions unit tests can't:
fill-value masking, hourly `apt` message structure, and day/night date attribution.

## Knobs

- `build_heat.py --product temp|feelslike|warmnight` · `--mode points|counties|both`
- `build_heat.py --csv` — also write analyst tables (tidy long + wide CSVs with
  full identifiers like `place_id`); on demand only, never committed.
- `build_heat.py --tz America/New_York` — apt products: the timezone whose
  calendar day/night defines each bucket (default `America/Chicago`).
- `build_heat.py --decimate N` — county zonal sampling stride (2 ≈ 5 km).
- `make_reference.py --incorporated --min-sqmi 0.5` — thin the places universe.
- `fetch_ndfd.py --area conus,alaska,hawaii` — sectors (add `puertorico` if needed).

## Editorial caveats (read before publishing anything from this data)

- **"Today" expires.** Once the window passes, NDFD drops it and `day1` becomes
  tomorrow. Always label from `fcst_date`.
- **Call it "apparent"/"feels-like," not "heat index"** below 80°F — heat index is
  only defined ≥80°F; the NWS Heat Index *categories* apply to feels-like only.
- **Warm-night date = the evening the night begins** (night of the 4th → labeled
  the 4th).
- **County values are extremes, not averages** — hottest cell for temp/feelslike,
  coolest for warm nights; not population-weighted.
- **Thin buckets** at the near/far ends rest on fewer hours (`n_hours` flags them).
- **One fixed timezone per build** (`--tz`), not per-cell solar time.
- **Whole °F only**; guard band −80…145°F kills unmasked GRIB fills at decode.
- **Nearest-cell sampling** for dots; coastal/mountain points can sit on a gradient.

## Data sources

- NDFD GRIB2: `https://tgftp.nws.noaa.gov/SL.us008001/ST.opnl/DF.gr2/DC.ndfd/AR.{conus,alaska,hawaii}/VP.{001-003,004-007}/ds.{maxt,apt}.bin`
  (files are WMO-wrapped concatenated GRIB2 — they don't start with `GRIB`; pygrib
  reads them as-is)
- Reference geographies: Census 2024 Gazetteer places + 2023 cartographic-boundary
  counties (`make_reference.py`; NYC is split into its five boroughs, consolidated
  city-county names cleaned, UTF-8 place names)
