#!/usr/bin/env python3
"""
fetch_ndfd.py — download NDFD temperature GRIB2 from the NWS tgftp server.

NDFD publishes gridded forecasts on a 2.5km CONUS grid. We pull two elements:

  maxt  daytime MAXIMUM temperature (one grid per forecast day, as NWS ships it)
  apt   apparent / "feels-like" temperature (hourly; rolled up in build_heat.py)

Each element is split across two period files — VP.001-003 (days 1–3) and
VP.004-007 (days 4–7) — so a full pull is 2 files per element.

Operational GRIB tree:
  https://tgftp.nws.noaa.gov/SL.us008001/ST.<status>/DF.gr2/DC.ndfd/AR.<area>/VP.<period>/ds.<element>.bin

The NWS *experimental* HeatRisk grid (0–4 health category) lives under ST.expr,
not ST.opnl — reach it with ``--status expr``.

Run: ``uv run python scripts/fetch_ndfd.py --elements maxt,apt``
Writes raw, untouched GRIB2 to data/raw/ (immutable source — never edited here).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Put the project root on the path so ``import config`` works no matter how this
# script is invoked (``uv run python scripts/fetch_ndfd.py`` or ``-m``).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import requests  # noqa: E402

import config  # noqa: E402

BASE = "https://tgftp.nws.noaa.gov/SL.us008001/{status}/DF.gr2/DC.ndfd/AR.{area}/VP.{period}/ds.{element}.bin"
PERIODS = ["001-003", "004-007"]

# NWS asks automated clients to identify themselves with a contact address.
HEADERS = {"User-Agent": "cbs-news-heat-map/1.0 (data journalism; johnl.kelly@cbsnews.com)"}


def fetch_one(status: str, area: str, period: str, element: str,
              outdir: Path, tries: int = 4) -> Path | None:
    url = BASE.format(status=status, area=area, period=period, element=element)
    dest = outdir / f"{element}_{area}_{period}.bin"
    for attempt in range(1, tries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=120)
            r.raise_for_status()
            # TGFTP serves NDFD as WMO-wrapped, concatenated GRIB2 messages: each
            # message is framed by a ****<bytecount>**** separator and a WMO heading,
            # so the file does NOT start with 'GRIB' (the first message sits ~80 bytes
            # in). pygrib/eccodes scans past that framing fine. Accept any payload
            # that contains a GRIB indicator; reject HTML/error pages (which won't).
            if b"GRIB" not in r.content:
                raise ValueError(f"payload has no GRIB2 message (starts {r.content[:16]!r})")
            dest.write_bytes(r.content)
            print(f"  ok  {url}  ({len(r.content):,} bytes)")
            return dest
        except Exception as e:  # noqa: BLE001 — retry on anything transient
            print(f"  try {attempt}/{tries} failed: {e}", file=sys.stderr)
            time.sleep(2 * attempt)
    print(f"  GIVE UP {url}", file=sys.stderr)
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--area", default="conus,alaska,hawaii",
                    help="comma-separated NDFD sectors (default: conus,alaska,hawaii; also e.g. puertorico)")
    ap.add_argument("--elements", default="maxt,apt",
                    help="comma-separated NDFD elements (default: maxt,apt)")
    ap.add_argument("--status", default="opnl", choices=["opnl", "expr"],
                    help="opnl=operational (default), expr=experimental (HeatRisk lives here)")
    ap.add_argument("--outdir", default=str(config.RAW_DATA_DIR))
    args = ap.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    status = f"ST.{args.status}"

    ok = True
    for area in [a.strip() for a in args.area.split(",") if a.strip()]:
        for element in [e.strip() for e in args.elements.split(",") if e.strip()]:
            print(f"{area} / {element}")
            for period in PERIODS:
                if fetch_one(status, area, period, element, outdir) is None:
                    ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
