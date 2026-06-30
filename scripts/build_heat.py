#!/usr/bin/env python3
"""
build_heat.py — Job 2 (reformat): turn NDFD temperature GRIB2 into map-ready data.

This is the only place GRIB is decoded and shaped. It writes nothing the viewer
needs to know about beyond plain files in data/processed/, so the presentation
layer (Job 3) can be swapped out without touching this step.

Three products (pick with --product), each writing the same file family:

  temp       maxt daytime MAXIMUM temperature (one grid/day, as NWS ships it)
             -> prefix heat_*
  feelslike  apt DAILY-MAX apparent / "feels-like" temp (apt is hourly; we take
             the per-cell max over each local day)              -> prefix feelslike_*
  warmnight  apt OVERNIGHT-MIN apparent temp — the warm-night low that drives
             heat-wave mortality (per-cell min over the local 8pm–8am window)
             -> prefix warmnight_*

apt = NWS apparent temperature: heat index when hot, wind chill when cold, plain
air temp in between — the versatile year-round "feels-like," not heat-index-only.

Two geographies per product (--mode): points (dots) and counties (geometry).

Outputs (in data/processed/, prefixed):
  <prefix>_long.csv          tidy: place_id, …, seq, fcst_date, valid_utc, value_f
  <prefix>_points.csv        wide: one row per place, columns day1..dayN
  <prefix>_points.geojson    point features (dots layer)
  <prefix>_counties.csv      wide: one row per county GEOID
  <prefix>_counties.geojson  county polygons w/ per-day value (geometry layer)

Run: ``uv run python scripts/build_heat.py --product temp`` (needs the ``heat`` extra).
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import timezone
from pathlib import Path

# Project root on the path so ``import config`` / ``utils`` resolve under any invocation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import config  # noqa: E402
from utils.heat import (  # noqa: E402
    as_apt_daymax,
    as_apt_nightmin,
    as_daily_maxt,
    day_meta,
    guard_range,
    k_to_f,
    to_int_f,
)


# ---------------------------------------------------------------- decode (pygrib)
def decode_raw(grib_paths: list[Path]):
    """Decode every GRIB message -> (lats1d, lons1d, [(valid_utc, vals_f_guarded)]).

    This is the one function that touches pygrib. Everything downstream operates
    on the plain arrays it returns, which is what makes the transform core in
    utils/heat.py testable without a live GRIB file.
    """
    import pygrib
    lats1d = lons1d = None
    records = []
    for p in grib_paths:
        with pygrib.open(str(p)) as grbs:
            for g in grbs:
                if lats1d is None:
                    lats, lons = g.latlons()
                    lats1d = lats.ravel().astype("float64")
                    lons1d = np.where(lons.ravel() > 180, lons.ravel() - 360, lons.ravel())
                vals, n_bad = guard_range(k_to_f(np.ma.filled(g.values, np.nan).ravel()))
                if n_bad:
                    print(f"  guard: {n_bad:,} out-of-range cell(s) -> NaN "
                          f"(valid {g.validDate:%Y-%m-%d %HZ}); check GRIB masking")
                records.append((g.validDate.replace(tzinfo=timezone.utc), vals))
    if not records:
        raise SystemExit("no GRIB messages decoded — check the .bin files")
    records.sort(key=lambda r: r[0])
    return lats1d, lons1d, records


# ---------------------------------------------------------------- points (dots)
def load_places(places_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(places_csv, dtype={"place_id": "string"})
    missing = {"place_id", "name", "state", "lat", "lon"} - set(df.columns)
    if missing:
        raise SystemExit(f"places file missing columns: {missing}")
    return df.dropna(subset=["lat", "lon"]).reset_index(drop=True)


def sample_points(lats1d, lons1d, days, places: pd.DataFrame) -> pd.DataFrame:
    """Nearest-grid-cell value for each community point (approx. equirectangular)."""
    from scipy.spatial import cKDTree
    coslat = np.cos(np.deg2rad(np.clip(lats1d, -89, 89)))
    tree = cKDTree(np.column_stack([lons1d * coslat, lats1d]))
    q = np.cos(np.deg2rad(places["lat"].to_numpy()))
    _, idx = tree.query(np.column_stack([places["lon"].to_numpy() * q,
                                         places["lat"].to_numpy()]))
    out = places.copy()
    for d in days:
        out[f"day{d['seq']}"] = to_int_f(d["vals_f"][idx])
    return out


def write_points(out, days, outdir: Path, prefix, element_label) -> None:
    day_cols = [f"day{d['seq']}" for d in days]
    id_cols = [c for c in ("place_id", "name", "name_display", "name_state",
                           "state", "lat", "lon") if c in out.columns]
    out.to_csv(outdir / f"{prefix}_points.csv", index=False)

    meta = {f"day{d['seq']}": d for d in days}
    long = out.melt(id_vars=id_cols, value_vars=day_cols,
                    var_name="day", value_name="value_f")
    long["seq"] = long["day"].map(lambda x: meta[x]["seq"])
    long["fcst_date"] = long["day"].map(lambda x: meta[x]["fcst_date"])
    long["valid_utc"] = long["day"].map(lambda x: meta[x]["valid_utc"])
    long.drop(columns=["day"]).to_csv(outdir / f"{prefix}_long.csv", index=False)

    prop_cols = [c for c in ("place_id", "name", "name_display", "name_state", "state")
                 if c in out.columns]
    feats = []
    for _, r in out.iterrows():
        props = {c: r[c] for c in prop_cols}
        for c in day_cols:
            props[c] = None if pd.isna(r[c]) else int(r[c])
        feats.append({"type": "Feature",
                      "geometry": {"type": "Point", "coordinates":
                                   [round(float(r["lon"]), 5), round(float(r["lat"]), 5)]},
                      "properties": props})
    gj = {"type": "FeatureCollection",
          "metadata": {"element": element_label, "days": [day_meta(d) for d in days]},
          "features": feats}
    (outdir / f"{prefix}_points.geojson").write_text(json.dumps(gj))
    print(f"points: {len(out):,} communities -> {prefix}_points.[csv|geojson], {prefix}_long.csv")


# ---------------------------------------------------------------- counties (geometry)
def build_counties(lats1d, lons1d, days, counties_geojson: Path, outdir: Path,
                   decimate, prefix, county_agg) -> None:
    """Zonal value per county. county_agg is 'max' (hottest cell, for temp/feelslike)
    or 'min' (coolest cell, the right 'how warm does it stay overnight' reading for
    warmnight). Keyed off the product, never the output prefix."""
    import geopandas as gpd
    counties = gpd.read_file(counties_geojson)
    geoid_col = next((c for c in ("GEOID", "GEOID20", "geoid") if c in counties.columns), None)
    name_col = next((c for c in ("NAME", "NAMELSAD", "name") if c in counties.columns), None)
    if geoid_col is None:
        raise SystemExit(f"no GEOID column in {counties_geojson} (have {list(counties.columns)})")
    counties = counties.to_crs(4326)

    sl = slice(None, None, decimate)
    grid = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lons1d[sl], lats1d[sl]), crs=4326)
    joined = gpd.sjoin(grid, counties[[geoid_col, "geometry"]], how="inner", predicate="within")
    base = joined[[geoid_col]].copy()

    result = counties[[geoid_col] + ([name_col] if name_col else [])].copy()
    for d in days:
        base["v"] = d["vals_f"][sl][joined.index]
        grp = base.groupby(geoid_col)["v"]
        agg = grp.min() if county_agg == "min" else grp.max()
        result[f"day{d['seq']}"] = to_int_f(result[geoid_col].map(agg))

    result.drop(columns="geometry", errors="ignore").to_csv(
        outdir / f"{prefix}_counties.csv", index=False)
    geo = counties[[geoid_col, "geometry"]].merge(
        result.drop(columns="geometry", errors="ignore"), on=geoid_col, how="left")
    geo.to_file(outdir / f"{prefix}_counties.geojson", driver="GeoJSON")
    print(f"counties: {len(result):,} polygons (decimate={decimate}, agg={county_agg}) "
          f"-> {prefix}_counties.[csv|geojson]")


# ---------------------------------------------------------------- main
PRODUCTS = {
    "temp":      {"element": "maxt", "prefix": "heat", "county_agg": "max",
                  "label": "NDFD maxt, daytime max (°F)"},
    "feelslike": {"element": "apt",  "prefix": "feelslike", "county_agg": "max",
                  "label": "NDFD apt, daily-max apparent/feels-like (°F)"},
    "warmnight": {"element": "apt",  "prefix": "warmnight", "county_agg": "min",
                  "label": "NDFD apt, overnight-min apparent/feels-like (°F); "
                           "fcst_date = evening the night begins"},
}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", choices=list(PRODUCTS), default="temp")
    ap.add_argument("--grib", nargs="+", default=None,
                    help="default: data/raw/<element>_conus_001-003.bin + 004-007.bin")
    ap.add_argument("--mode", choices=["points", "counties", "both"], default="both")
    ap.add_argument("--places", default=str(config.REFERENCE_DIR / "places.csv"))
    ap.add_argument("--counties", default=str(config.REFERENCE_DIR / "counties.geojson"))
    ap.add_argument("--tz", default="America/Chicago",
                    help="apt products: tz whose calendar day/night defines each bucket")
    ap.add_argument("--decimate", type=int, default=2,
                    help="county zonal value samples every Nth grid cell (2 ≈ 5km)")
    ap.add_argument("--outdir", default=str(config.PROCESSED_DATA_DIR))
    ap.add_argument("--outprefix", default=None)
    args = ap.parse_args()

    prod = PRODUCTS[args.product]
    prefix = args.outprefix or prod["prefix"]
    grib = args.grib or [str(config.RAW_DATA_DIR / f"{prod['element']}_conus_001-003.bin"),
                         str(config.RAW_DATA_DIR / f"{prod['element']}_conus_004-007.bin")]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    paths = [Path(p) for p in grib if Path(p).exists()]
    if not paths:
        raise SystemExit(f"no GRIB inputs found among {grib}")

    lats1d, lons1d, records = decode_raw(paths)
    if args.product == "temp":
        days = as_daily_maxt(records)
        print(f"temp(maxt): {len(days)} daily grids")
    elif args.product == "feelslike":
        days = as_apt_daymax(records, args.tz)
        print(f"feelslike(apt day-max): {len(days)} days (tz={args.tz}); "
              f"hours/day: {[d['n_hours'] for d in days]}")
    else:
        days = as_apt_nightmin(records, args.tz)
        print(f"warmnight(apt overnight-min): {len(days)} nights (tz={args.tz}); "
              f"hours/night: {[d['n_hours'] for d in days]}")

    if args.mode in ("points", "both"):
        write_points(sample_points(lats1d, lons1d, days, load_places(Path(args.places))),
                     days, outdir, prefix, prod["label"])
    if args.mode in ("counties", "both"):
        build_counties(lats1d, lons1d, days, Path(args.counties), outdir,
                       args.decimate, prefix, prod["county_agg"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
