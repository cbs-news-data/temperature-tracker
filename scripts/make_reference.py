#!/usr/bin/env python3
"""
make_reference.py — build the two reference geographies the pipeline joins
against. Run once, refresh ~yearly. Both sources are free Census files.

  data/reference/places.csv        every U.S. place (incorporated + CDP) as a
                                   point — the "dots" universe (~32k communities)
  data/reference/counties.geojson  county polygons — the "geometry" universe

Filter knobs:
  --min-sqmi      drop micro-CDPs below a land-area floor (0 = keep all)
  --incorporated  keep only incorporated places (drop Census Designated Places)

Run: ``uv run python scripts/make_reference.py``  (needs the ``geo`` or ``heat``
extra for the county build; ``--skip-counties`` builds places only.)
"""
from __future__ import annotations

import argparse
import io
import re
import sys
import zipfile
from pathlib import Path

# Project root on the path so ``import config`` resolves under any invocation.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import pandas as pd  # noqa: E402
import requests  # noqa: E402

import config  # noqa: E402

GAZ = "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip"
CB_COUNTY = "https://www2.census.gov/geo/tiger/GENZ2023/shp/cb_2023_us_county_20m.zip"
UA = {"User-Agent": "cbs-news-heat-map/1.0 (data journalism; johnl.kelly@cbsnews.com)"}

# LSAD codes that denote incorporated places (cities/towns/boroughs/villages),
# i.e. not Census Designated Places (LSAD 57) — see the Census LSAD reference.
INCORP_LSAD = {"25", "43", "47", "53", "55", "62", "21", "37", "39", "41"}

# The Gazetteer NAME carries the LSAD descriptor: appended lowercase (city/town/…),
# or as "CDP" / "(balance)". Strip only those so capitalized name parts survive
# ("Carson City" stays "Carson City"; "Oklahoma City city" becomes "Oklahoma City").
_BALANCE = re.compile(r"\s*\(balance\)\s*$", re.I)
_MULTI = re.compile(r"\s+(?:city and borough|"
                    r"(?:metropolitan|consolidated|unified|metro) government|"
                    r"zona urbana)$", re.I)
_DESC = re.compile(r"\s+(?:CDP|city|town|village|borough|municipality|comunidad|pueblo)$")


def clean_place_name(name: str) -> str:
    n = _BALANCE.sub("", str(name).strip())
    n = _MULTI.sub("", n)        # strip multi-word forms before the single-word pass
    return _DESC.sub("", n).strip()


def build_places(outdir: Path, min_sqmi: float, incorporated: bool) -> None:
    print(f"GET {GAZ}")
    z = zipfile.ZipFile(io.BytesIO(requests.get(GAZ, headers=UA, timeout=180).content))
    name = next(n for n in z.namelist() if n.lower().endswith(".txt"))
    df = pd.read_csv(z.open(name), sep="\t", dtype=str, encoding="latin-1")
    df.columns = [c.strip() for c in df.columns]  # the Gazetteer pads some headers
    for c in ("INTPTLAT", "INTPTLONG", "ALAND_SQMI"):
        df[c] = pd.to_numeric(df[c].str.strip(), errors="coerce")
    if incorporated and "LSAD" in df.columns:
        df = df[df["LSAD"].isin(INCORP_LSAD)]
    if min_sqmi > 0:
        df = df[df["ALAND_SQMI"] >= min_sqmi]

    out = pd.DataFrame({
        "place_id": df["GEOID"], "name": df["NAME"], "state": df["USPS"],
        "lat": df["INTPTLAT"], "lon": df["INTPTLONG"],
    }).dropna(subset=["lat", "lon"])
    out["name_display"] = out["name"].map(clean_place_name)        # descriptor stripped
    out["name_state"] = out["name_display"] + ", " + out["state"]  # "Phoenix, AZ" popup label
    out = out[["place_id", "name", "name_display", "name_state", "state", "lat", "lon"]]

    outdir.mkdir(parents=True, exist_ok=True)
    out.to_csv(outdir / "places.csv", index=False)
    print(f"  places.csv: {len(out):,} communities")


def build_counties(outdir: Path) -> None:
    import geopandas as gpd
    print(f"GET {CB_COUNTY}")
    outdir.mkdir(parents=True, exist_ok=True)
    zpath = outdir / "_county.zip"
    zpath.write_bytes(requests.get(CB_COUNTY, headers=UA, timeout=180).content)
    gdf = gpd.read_file(f"zip://{zpath}").to_crs(4326)
    keep = [c for c in ("GEOID", "NAME", "NAMELSAD", "STUSPS", "STATE_NAME", "geometry")
            if c in gdf.columns]
    gdf[keep].to_file(outdir / "counties.geojson", driver="GeoJSON")
    zpath.unlink(missing_ok=True)
    print(f"  counties.geojson: {len(gdf):,} polygons")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--outdir", default=str(config.REFERENCE_DIR))
    ap.add_argument("--min-sqmi", type=float, default=0.0)
    ap.add_argument("--incorporated", action="store_true")
    ap.add_argument("--skip-counties", action="store_true")
    args = ap.parse_args()

    outdir = Path(args.outdir)
    build_places(outdir, args.min_sqmi, args.incorporated)
    if not args.skip_counties:
        build_counties(outdir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
