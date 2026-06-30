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
| **3 — Present** | render & deploy the interactive map | `viewer/index.html` + `heat-publish.yml` | a deployed site |

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
| `<p>_long.csv` | place × day (tidy) | source of truth; joins cleanly to ACS in R/pandas |
| `<p>_points.csv` / `.geojson` | one row/feature per community, cols `day1…dayN` | **dots** layer |
| `<p>_counties.csv` / `.geojson` | one row/polygon per county | **geometry** layer |

Each day carries `seq`, `fcst_date`, and `valid_utc`. The apt products also carry
`n_hours` (grids feeding the value) and `valid_start_utc`. Whole °F only.

Place labels (Census Gazetteer) come in three forms: `name` is raw (`"Phoenix
city"`); `name_display` strips the descriptor (`"Phoenix"`, while keeping
`"Carson City"`); `name_state` adds the state (`"Phoenix, AZ"` — the popup label).
Join/dedupe on `place_id` (Census GEOID, a string), **never** on `name`. New York
City is one consolidated place in the Census files (a single dot); `make_reference.py`
replaces it with its five boroughs (`place_id` = borough-county GEOID) for detail.

## Viewer

`viewer/index.html` is a self-contained MapLibre map: a **Find a place** search
box (type 2+ chars → flies to the community and shows its full forecast), a
**Measure** dropdown (Temperature / Feels-like / Warm nights), a **Counties/Places**
toggle (counties is the default national overview; places are for drill-down), a
**Region** switcher (CONUS / Alaska / Hawaii), and a **Day** selector. Temperature and feels-like share a heat-emphasis ramp (yellow at
80°F, orange in the low 90s, red in the upper 90s, deepening past 100); warm nights
has its own. Thin buckets (`n_hours` < 6) are flagged ⚠ in the day list; no-data
places are hidden, not painted the coldest color; place dots have no outline and
overlap into a field.

It loads GeoJSON from `DATA_BASE`, set via the `?data=` query param (default
`data`). Local: `?data=../data/processed`. Deployed: the publish workflow lays the
site out as `index.html` + `data/`, so the default works.

## Automation

Two workflows keep the jobs separable:

- **`.github/workflows/heat-data.yml`** — runs twice daily (UTC): fetch `maxt,apt`,
  build all three products, commit `data/processed` + `data/reference`. Raw GRIB
  is not committed (binary, reproducible).
- **`.github/workflows/heat-publish.yml`** — after the data pipeline succeeds (or
  on demand), assembles `viewer/` + the committed GeoJSON and deploys to GitHub
  Pages. *One-time repo setting:* Settings → Pages → Source = "GitHub Actions".

Delete `heat-publish.yml` and the data pipeline is unaffected.

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
