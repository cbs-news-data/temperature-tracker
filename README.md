# temperature-tracker — NDFD heat forecast → map-ready data

Turns NOAA/NWS **NDFD** GRIB2 forecasts into map-ready data for an interactive
extreme-heat map: per-community **dots** and per-county **geometry**, for today
plus the forecast week. Built for a CBS News data-journalism use case.

## Pipeline (three decoupled jobs)

The stages talk to each other only through files on disk, so any one can be
replaced without breaking the others.

| Job | Does | Code | Writes |
|---|---|---|---|
| **1 — Fetch** | download NDFD GRIB on a schedule | `scripts/fetch_ndfd.py` | `data/raw/*.bin` |
| **2 — Reformat** | decode GRIB → map/chart-ready tables | `scripts/build_heat.py` (+ `utils/heat.py`) | `data/processed/*.csv`, `*.geojson` |
| **3 — Present** | render & deploy the interactive map | `viewer/index.html` + the `deploy` job in `heat-data.yml` | a deployed site |

Job 3 is the **only** consumer of Job 2's files. Move presentation elsewhere
(Datawrapper, a CMS, a separate web repo) and Jobs 1–2 keep fetching, cleaning,
and committing the latest data untouched.

## Products

| `--product` | element | what | prefix |
|---|---|---|---|
| `temp` | `maxt` | daytime **maximum temperature** (one grid/day, as NWS ships it) | `heat_*` |
| `feelslike` | `apt` | **daily-max apparent / "feels-like"** temp (apt is hourly → per-cell daily max) | `feelslike_*` |
| `warmnight` | `apt` | **overnight-min** apparent temp — the warm-night low that drives heat-wave mortality | `warmnight_*` |

`apt` = NWS apparent temperature: heat index when hot, wind chill when cold,
plain air temp in between — versatile year-round, **not** heat-index-only.

## Setup

Environment is managed by `uv`. The heat pipeline needs the `heat` extra, and
`pygrib` needs the system **eccodes** C library:

```bash
uv sync --extra heat            # pygrib, scipy, geopandas, shapely, pyproj, tzdata
# eccodes:  macOS -> brew install eccodes   |   Debian/Ubuntu -> apt-get install libeccodes-dev
```

## Run order

```bash
uv run python scripts/make_reference.py                       # once (refresh ~yearly)
uv run python scripts/validate_live.py                        # FIRST live pull — see Validation
uv run python scripts/fetch_ndfd.py --elements maxt,apt       # days 1–7, CONUS + Alaska + Hawaii
uv run python scripts/build_heat.py --product temp            # -> data/processed/heat_*
uv run python scripts/build_heat.py --product feelslike
uv run python scripts/build_heat.py --product warmnight

# view locally: serve the repo root, then point the viewer at the processed data
uv run python -m http.server 8000
# open  http://localhost:8000/viewer/index.html?data=../data/processed
```

## Outputs (per product; `<p>` = `heat` / `feelslike` / `warmnight`, in `data/processed/`)

| file | shape | use |
|---|---|---|
| `<p>_points.geojson` | one feature per community: `name_state` + `day1…dayN`, 4-dec coords | **dots** layer (committed, published) |
| `<p>_counties.geojson` | one polygon per county: `GEOID`, `NAME`, `day1…dayN` | **geometry** layer (committed, published) |
| `<p>_long.csv` · `<p>_points.csv` · `<p>_counties.csv` | tidy / wide analyst tables with full identifiers (`place_id` etc.) | **on demand only** — `build_heat.py --csv`; not committed (pure reformats of the GeoJSON values) |

The GeoJSONs are deliberately minimal — only what the map displays. GeoJSON
metadata carries `days[]` (`seq`, `fcst_date`, `valid_utc`; apt products add
`n_hours`, `valid_start_utc`) and `issued_utc` (NWS forecast issuance). Whole °F only.

## Data contract (for downstream consumers, e.g. the graphics-rig embed)

External renderers fetch these six files at runtime — treat this as a frozen
interface; breaking changes require coordinating with every consumer:

- **URLs:** `https://cbs-news-data.github.io/temperature-tracker/data/{heat,feelslike,warmnight}_{points,counties}.geojson`
- **Cadence:** refreshed 3× daily (9:23 / 15:23 / 0:23 UTC + Pages deploy + CDN);
  consumers should fetch with `cache: "no-cache"`.
- **Points features:** `properties = { name_state, day1..dayN }` — values are whole
  °F ints or `null` (no data); N varies (typically 6–7). Point coords, 4 decimals.
- **Counties features:** `properties = { GEOID, NAME, day1..dayN }` — same value rules.
- **Metadata (BOTH points and counties files):** `metadata.days[]` maps `dayN`
  keys to `seq`, `fcst_date` (the date the value describes), `valid_utc`; apt
  products add `n_hours` + `valid_start_utc` (thin-bucket flagging).
  `metadata.issued_utc` = when NWS generated the forecast. Present on counties
  too so lazy-loading consumers get day labels from the first (small) fetch.
- **Semantics to preserve in any renderer:** label days off `fcst_date`, never the
  `dayN` index ("today" expires mid-day); warm-night `fcst_date` = the evening the
  night begins; feels-like is NWS apparent temperature (Heat Index categories apply
  to it, not to raw temperature); hide `null`s rather than painting them cold.
- **CORS:** GitHub Pages serves `Access-Control-Allow-Origin: *` (verified).

Place labels (Census Gazetteer) come in three forms in the reference/CSV outputs:
`name` is raw (`"Phoenix city"`); `name_display` strips the descriptor (`"Phoenix"`,
while keeping `"Carson City"`); `name_state` adds the state (`"Phoenix, AZ"` — the
popup label, and the only label shipped in the points GeoJSON). Join/dedupe on
`place_id` (Census GEOID, a string; in `data/reference/places.csv` and the `--csv`
outputs), **never** on `name`. New York
City is one consolidated place in the Census files (a single dot); `make_reference.py`
replaces it with its five boroughs (`place_id` = borough-county GEOID) for detail.

## Viewer

`viewer/index.html` is a self-contained MapLibre map: a **Find a place** search
box (type 2+ chars → flies to the community and shows its full forecast), **Measure**
buttons (High temp / Feels like / Overnight low), a **Counties/Places** toggle
(counties are the national overview; **Places auto-reveal on zoom-in** past z5.5,
counties return on zoom-out; tapping a Layer button takes manual control), and a
**Day** selector. Alaska and Hawaii are in the data — reach them via search or by panning.
The layout is responsive: a left panel on desktop, a collapsible bottom sheet on
phones. Temperature uses a heat-emphasis ramp (yellow at 80°F, orange in the low
90s, red in the upper 90s, deepening past 100). Feels-like is colored by the **NWS
Heat Index categories** (Caution 80–90 / Extreme Caution 90–103 / Danger 103–125 /
Extreme Danger 125+ °F) — appropriate because feels-like is NWS apparent temperature;
those categories are *not* applied to raw air temp or overnight lows. Warm nights uses
the same 10° temperature bands as the daytime scale. Thin buckets (`n_hours` < 6) are flagged ⚠ in the day list; no-data
places are hidden, not painted the coldest color; place dots have no outline and
overlap into a field.

It loads GeoJSON from `DATA_BASE`, set via the `?data=` query param (default
`data`). Local: `?data=../data/processed`. Deployed: the publish workflow lays the
site out as `index.html` + `data/`, so the default works.

## Automation

One workflow, `.github/workflows/heat-data.yml`, runs three times daily —
9:23 / 15:23 / 0:23 UTC (5:23a / 11:23a / 8:23p ET in summer; an hour earlier in
winter; odd minutes dodge GitHub's top-of-hour cron rush):

- **`build-data` job** — fetch `maxt,apt` (CONUS + AK + HI), build all three
  products, commit `data/processed` + `data/reference`, stage the site artifact.
  Raw GRIB is not committed (binary, reproducible).
- **`deploy` job** — deploys the staged site to GitHub Pages, with an automatic
  retry: the Pages backend intermittently rejects deployments with a transient
  "Deployment failed, try again later," so a failed attempt waits 3 minutes and
  tries again. *One-time repo setting:* Settings → Pages → Source = "GitHub Actions".

Presentation stays detachable: delete the `deploy` job and the fetch/clean/commit
pipeline is untouched.

## Validation (do this before publishing)

The transform core (`utils/heat.py`) is unit-tested on synthetic grids
(`uv sync --extra heat --extra dev` once, then `uv run pytest`). The live GRIB
**decode** is not something tests can cover, so run the smoke test against a real
pull once before trusting output, and again after any NDFD/pygrib change:

```bash
uv run python scripts/validate_live.py
```

It checks that (1) pygrib masks the fill value (guard flags only a few cells, not
a block), (2) `apt` arrives as many sub-daily messages, and (3) day/night
`fcst_date` attribution lines up with each message's valid time.

## Knobs

- `build_heat.py --product temp|feelslike|warmnight`
- `build_heat.py --tz America/New_York` — apt products: the timezone whose calendar
  day (feels-like) / night (warm nights) defines each bucket. Default `America/Chicago`.
- `build_heat.py --mode points|counties|both`
- `build_heat.py --decimate N` — county value samples every Nth grid cell
  (2 ≈ 5 km; barely moves a county extreme, ~4× faster join).
- `make_reference.py --incorporated --min-sqmi 0.5` — trim ~32k places to a
  lighter cities-only dots universe.
- `fetch_ndfd.py --area conus,alaska,hawaii` — sectors to download (default all
  three; add `puertorico` if needed). `build_heat.py --areas …` selects which to
  merge; each place/county is routed to its own sector grid (AK/HI/CONUS).

## Caveats (read before publishing)

- **"Today" expires.** Once the current window passes, NDFD drops it, so `day1`
  can be *tomorrow*. Always label off `fcst_date` / `valid_utc`.
- **Call it "apparent"/"feels-like," not "heat index."** Heat index is defined
  only ≥80 °F; below that apt is air temp (or wind chill). "Feels-like" is accurate
  across the whole range.
- **Warm-night date = the evening the night begins.** `fcst_date` 2026-07-04 means
  the night of the 4th into the morning of the 5th. Re-label if your story frames
  it as "the low on the 5th."
- **County aggregation differs by product.** `temp`/`feelslike` report the county
  **max** (hottest cell); `warmnight` reports the **min** (coolest cell). Both are
  extremes, not population-weighted — say so.
- **Thin buckets** at the near end ("today," partial) and far end (apt coarsens to
  3/6-hourly by day 7) rest on fewer hours — watch `n_hours` / the ⚠ flag.
- **Day boundary is one fixed tz** (`--tz`), not per-cell solar time. Robust for the
  afternoon high; slightly loose for overnight-low date attribution out East/West.
- **Whole degrees only**; tenths are false precision.
- **Fill-value guard.** Temps outside −80…145 °F → NaN at decode. A *large* flagged
  count means masking is wrong — stop (that's what `validate_live.py` catches).
- **Nearest-cell sampling** for dots; coastal/mountain points can sit on a gradient.

## Data sources

- Operational GRIB tree: `https://tgftp.nws.noaa.gov/SL.us008001/ST.opnl/DF.gr2/DC.ndfd/AR.conus/VP.{001-003,004-007}/ds.{maxt,apt}.bin`
- Reference geographies: Census 2024 Gazetteer places + 2023 cartographic-boundary
  counties (built by `make_reference.py`).
- `pygrib` over `cfgrib`: NDFD packs each step as its own message with variable
  time ranges; pygrib's message-by-message loop is more predictable.

## Project conventions

See `CLAUDE.md` for repo conventions (uv, data flow, helpers). General template
setup and package extras are unchanged from the datascience-template baseline.
```
