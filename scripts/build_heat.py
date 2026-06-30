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

Sectors: NDFD ships separate grids for CONUS, Alaska, and Hawaii. Each place and
county is routed to its own sector (AK -> alaska, HI -> hawaii, else conus) and
sampled against that sector's grid, so the three merge into one set of outputs.
Sectors with no GRIB in data/raw/ are skipped (those places come out blank).

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

# Sector routing. AK/HI have their own NDFD grids; everything else is CONUS.
PLACE_SECTOR = {"AK": "alaska", "HI": "hawaii"}        # by USPS state
COUNTY_FIPS_SECTOR = {"02": "alaska", "15": "hawaii"}  # by GEOID state prefix


def place_sector(state) -> str:
    return PLACE_SECTOR.get(state, "conus")


def county_sector(geoid) -> str:
    return COUNTY_FIPS_SECTOR.get(str(geoid)[:2], "conus")


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


def days_for(product: str, records, tz: str) -> list[dict]:
    if product == "temp":
        return as_daily_maxt(records)
    if product == "feelslike":
        return as_apt_daymax(records, tz)
    return as_apt_nightmin(records, tz)


# ---------------------------------------------------------------- points (dots)
def load_places(places_csv: Path) -> pd.DataFrame:
    df = pd.read_csv(places_csv, dtype={"place_id": "string"})
    missing = {"place_id", "name", "state", "lat", "lon"} - set(df.columns)
    if missing:
        raise SystemExit(f"places file missing columns: {missing}")
    return df.dropna(subset=["lat", "lon"]).reset_index(drop=True)


def nearest_idx(lats1d, lons1d, lat, lon):
    """Nearest grid-cell index per point (approx. equirectangular metric)."""
    from scipy.spatial import cKDTree
    coslat = np.cos(np.deg2rad(np.clip(lats1d, -89, 89)))
    tree = cKDTree(np.column_stack([lons1d * coslat, lats1d]))
    q = np.cos(np.deg2rad(lat))
    _, idx = tree.query(np.column_stack([lon * q, lat]))
    return idx


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
def accumulate_counties(county_vals, csub, geoid_col, lats1d, lons1d, days, decimate, county_agg):
    """Add this sector's per-county values (keyed by fcst_date -> {geoid: value})."""
    import geopandas as gpd
    sl = slice(None, None, decimate)
    grid = gpd.GeoDataFrame(geometry=gpd.points_from_xy(lons1d[sl], lats1d[sl]), crs=4326)
    joined = gpd.sjoin(grid, csub[[geoid_col, "geometry"]], how="inner", predicate="within")
    if joined.empty:
        return
    base = joined[[geoid_col]].copy()
    for d in days:
        base["v"] = d["vals_f"][sl][joined.index]
        grp = base.groupby(geoid_col)["v"]
        agg = grp.min() if county_agg == "min" else grp.max()
        county_vals.setdefault(d["fcst_date"], {}).update(agg.to_dict())


def write_counties(counties, geoid_col, name_col, county_vals, days, outdir: Path, prefix, county_agg) -> None:
    result = counties[[geoid_col] + ([name_col] if name_col else [])].copy()
    for d in days:
        vals = county_vals.get(d["fcst_date"], {})
        result[f"day{d['seq']}"] = to_int_f(result[geoid_col].map(lambda g: vals.get(g, np.nan)))
    result.to_csv(outdir / f"{prefix}_counties.csv", index=False)
    geo = counties[[geoid_col, "geometry"]].merge(result, on=geoid_col, how="left")
    geo.to_file(outdir / f"{prefix}_counties.geojson", driver="GeoJSON")
    print(f"counties: {len(result):,} polygons (agg={county_agg}) -> {prefix}_counties.[csv|geojson]")


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


def global_days(day_meta_by_date: dict) -> list[dict]:
    """One ordered day list across all sectors, keyed by forecast date. Sectors
    align on fcst_date, not the dayN index, so a sector missing 'today' still
    lands on the right column. CONUS metadata wins (it's processed first)."""
    days = []
    for i, fd in enumerate(sorted(day_meta_by_date), start=1):
        m = day_meta_by_date[fd]
        g = {"seq": i, "fcst_date": fd, "valid_utc": m["valid_utc"]}
        if "n_hours" in m:
            g["n_hours"] = m["n_hours"]
            g["valid_start_utc"] = m["valid_start_utc"]
        days.append(g)
    return days


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--product", choices=list(PRODUCTS), default="temp")
    ap.add_argument("--areas", default="conus,alaska,hawaii",
                    help="NDFD sectors to merge (default: conus,alaska,hawaii); missing ones are skipped")
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
    areas = [a.strip() for a in args.areas.split(",") if a.strip()]
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    want_points = args.mode in ("points", "both")
    want_counties = args.mode in ("counties", "both")

    if want_points:
        places = load_places(Path(args.places))
        psec = places["state"].map(place_sector).to_numpy()
        plat, plon = places["lat"].to_numpy(), places["lon"].to_numpy()
        place_vals: dict = {}   # fcst_date -> float array (per place)
    if want_counties:
        import geopandas as gpd
        counties = gpd.read_file(args.counties).to_crs(4326)
        geoid_col = next((c for c in ("GEOID", "GEOID20", "geoid") if c in counties.columns), None)
        name_col = next((c for c in ("NAME", "NAMELSAD", "name") if c in counties.columns), None)
        if geoid_col is None:
            raise SystemExit(f"no GEOID column in {args.counties} (have {list(counties.columns)})")
        csec = counties[geoid_col].astype(str).map(county_sector).to_numpy()
        county_vals: dict = {}  # fcst_date -> {geoid: value}

    day_meta_by_date: dict = {}  # fcst_date -> day dict (no vals), CONUS wins
    decoded = []
    for area in areas:   # CONUS first (as listed) so its day metadata wins
        paths = [config.RAW_DATA_DIR / f"{prod['element']}_{area}_{p}.bin"
                 for p in ("001-003", "004-007")]
        paths = [p for p in paths if p.exists()]
        if not paths:
            print(f"  skip {area}: no GRIB in {config.RAW_DATA_DIR}")
            continue
        lats1d, lons1d, records = decode_raw(paths)
        days = days_for(args.product, records, args.tz)
        del records
        print(f"{area}/{prod['element']}: {len(days)} day(s) "
              f"{'hours: ' + str([d.get('n_hours') for d in days]) if 'n_hours' in days[0] else ''}")
        for d in days:
            day_meta_by_date.setdefault(d["fcst_date"], {k: v for k, v in d.items() if k != "vals_f"})

        if want_points:
            pos = np.where(psec == area)[0]
            if pos.size:
                idx = nearest_idx(lats1d, lons1d, plat[pos], plon[pos])
                for d in days:
                    arr = place_vals.setdefault(d["fcst_date"], np.full(len(places), np.nan))
                    arr[pos] = d["vals_f"][idx]
        if want_counties:
            csub = counties[csec == area]
            if not csub.empty:
                accumulate_counties(county_vals, csub, geoid_col, lats1d, lons1d,
                                    days, args.decimate, prod["county_agg"])
        del lats1d, lons1d, days
        decoded.append(area)

    if not decoded:
        raise SystemExit(f"no GRIB inputs found for any sector in {areas} "
                         f"(looked in {config.RAW_DATA_DIR})")
    print(f"sectors merged: {', '.join(decoded)}")
    gdays = global_days(day_meta_by_date)

    if want_points:
        out = places.copy()
        for g in gdays:
            out[f"day{g['seq']}"] = to_int_f(place_vals.get(g["fcst_date"], np.full(len(places), np.nan)))
        write_points(out, gdays, outdir, prefix, prod["label"])
    if want_counties:
        write_counties(counties, geoid_col, name_col, county_vals, gdays, outdir, prefix, prod["county_agg"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
