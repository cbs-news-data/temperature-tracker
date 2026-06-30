# HANDOFF — NDFD heat-forecast pipeline

Context for whoever picks this up next. The code documents *how*; this note
documents *why*, what's proven vs. not, and what's left. Read this, then the
README.

## What this is
A pipeline that turns NOAA/NWS **NDFD** GRIB2 forecasts into map-ready data for an
interactive extreme-heat map: per-community **dots** and per-county **geometry**,
for today + the forecast week. Built for a CBS News data-journalism use case.

## Layout (reconciled to the repo)
The pipeline is three decoupled jobs that talk only through files on disk:
```
scripts/fetch_ndfd.py      Job 1 — download NDFD GRIB2 (maxt + apt) -> data/raw/
scripts/build_heat.py      Job 2 — decode GRIB -> data/processed/ (csv + geojson)
viewer/index.html          Job 3 — MapLibre viewer; reads data/processed only
scripts/make_reference.py  one-time: places.csv (dots) + counties.geojson (geometry)
scripts/validate_live.py   live GRIB smoke test (run before trusting output)
utils/heat.py              pure transform core (K->F, guard, bucketing) — unit-tested
tests/test_heat.py         synthetic-grid unit tests (uv run pytest)
.github/workflows/heat-data.yml     Jobs 1+2: fetch, reformat, commit latest data
.github/workflows/heat-publish.yml  Job 3: deploy viewer to Pages (deletable)
config.py                  shared paths (RAW/REFERENCE/PROCESSED/VIEWER dirs)
```
Job 3 is the only consumer of Job 2's output, so presentation can move elsewhere
(Datawrapper, CMS, separate repo) without touching fetch/reformat/commit.

## ⚠️ Still true: the live GRIB decode has not been verified here
The transform core is unit-tested on synthetic grids only (10 tests, all passing).
The GRIB decode and county spatial join still need one real run — the build
environment has no eccodes/pygrib. **`scripts/validate_live.py` now exists** for
exactly this (it was the top open item). Run it before publishing; it checks:
1. pygrib masks NDFD's fill value (guard flags a few cells, not a block).
2. `apt` arrives as separate sub-daily messages (the bucketing assumes this).
3. maxt/apt day & night `fcst_date` attribution line up with each message's valid time.

## Decisions made (please don't silently revert these)
- **Element = NDFD `maxt` for temperature, `apt` for feels-like.** Labeled
  **"apparent / feels-like," NOT "heat index"** (heat index is only defined ≥80°F;
  feels-like is accurate year-round and reusable for cold snaps).
- **Three products:** `temp` (maxt daily max), `feelslike` (apt daily max),
  `warmnight` (apt overnight min). Warm nights drive heat-wave mortality.
- **apt is hourly → rolled up.** feels-like = per-cell MAX over the local day;
  warm-night = per-cell MIN over the local 8pm–8am window.
- **Warm-night date = the evening the night begins.**
- **County aggregation flips by PRODUCT** (not the output prefix — that was a bug,
  now fixed): temp/feelslike report the county max; warmnight reports the min.
- **One fixed timezone nationally** (`--tz`, default `America/Chicago`).
- **Whole °F only**; **guard band −80…145°F** at decode.
- **Place identity:** `place_id` = Census GEOID (string join key); `name` (raw),
  `name_display` (stripped), `name_state` (popup label).
- **Persistence:** commit `data/processed` + `data/reference`; raw GRIB stays a
  within-run artifact (binary, reproducible). Viewer default = counties (national
  overview), dots for drill-down.

## Resolved since the first handoff
- ✅ `validate_live.py` smoke test built.
- ✅ Layout reconciled to repo conventions; scripts use `config.py`; deps moved to
  the `heat` extra in `pyproject.toml` (no more `requirements.txt`).
- ✅ Product/agg bug fixed (warmnight county min no longer keyed off a renamable prefix).
- ✅ Viewer: counties-first default; no-data places hidden (were painted coldest).
- ✅ Unit tests added for the transform core.
- ✅ CI split into a durable data pipeline + a separable publish job.

## Open items (named, not built)
1. **Run `validate_live.py` against live NDFD** — still the gate before publishing.
2. **Population join** for the dots — size/filter by community size; also thins the
   32k. Needs a population source keyed to GEOID (Census ACS/PEP).
3. **Per-cell-local-time bucketing mode** (`--tz local`) for a coast-to-coast map.
4. **Spot-check `name_display`** against the live Gazetteer:
   `places[places.name != places.name_display]` on a sample — it's a heuristic.
5. **Heat-danger framing option:** NWS **experimental HeatRisk** grid (0–4 health
   category) under `ST.expr` — alternative if the story leans on health impact.
6. **Optional zoom-reveal** for dots (auto-show above a zoom threshold) — left as a
   manual toggle for now to keep behavior predictable.

## Data source notes
- Operational GRIB tree: `https://tgftp.nws.noaa.gov/SL.us008001/ST.opnl/DF.gr2/DC.ndfd/AR.conus/VP.{001-003,004-007}/ds.{maxt,apt}.bin`
- Reference geographies: Census 2024 Gazetteer places + 2023 cartographic-boundary
  counties (built by `make_reference.py`).
- pygrib chosen over cfgrib: NDFD packs each step as its own message with variable
  time ranges; pygrib's message-by-message loop is more predictable.
