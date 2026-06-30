#!/usr/bin/env python3
"""
validate_live.py — smoke-test the GRIB decode against a REAL NDFD pull.

Everything else in this pipeline is unit-tested on synthetic grids. The one thing
those tests cannot cover is whether live NDFD GRIB2 actually decodes the way we
assume. Run this once before trusting any published output, and again whenever the
NDFD format or pygrib/eccodes versions change.

It checks the three things the handoff flagged as unverified:

  1. MASKING   — pygrib masks NDFD's fill value, so guard_range flags only a few
                 cells (coastal NaNs), not a huge block (which would mean the fill
                 is leaking through as ~17,500°F and poisoning aggregates).
  2. HOURLY    — apt arrives as many sub-daily messages, not one grid per day
                 (the feels-like / warm-night bucketing depends on this).
  3. LABELS    — maxt day labels and apt day/night fcst_date attribution line up
                 with each message's valid time (printed for a human eyeball).

Run: ``uv run python scripts/validate_live.py``  (needs the ``heat`` extra +
system eccodes; downloads the day-1–3 files if data/raw/ is empty).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import numpy as np  # noqa: E402

import config  # noqa: E402
from utils.heat import (  # noqa: E402
    PLAUSIBLE_F,
    as_apt_daymax,
    as_apt_nightmin,
    as_daily_maxt,
    guard_range,
    k_to_f,
)

# A handful of coastal/edge NaNs is normal; anything above this fraction means the
# fill value is not being masked and is leaking into the temperature field.
MASK_FAIL_FRACTION = 0.02

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


def _ensure_raw() -> dict[str, Path]:
    """Return {element: day-1–3 .bin}, downloading them if data/raw/ is empty."""
    wanted = {e: config.RAW_DATA_DIR / f"{e}_conus_001-003.bin" for e in ("maxt", "apt")}
    if all(p.exists() for p in wanted.values()):
        return wanted
    print("data/raw/ missing day-1–3 files — fetching maxt + apt (period 001-003)…")
    from fetch_ndfd import fetch_one  # local import; same scripts/ dir
    for element, path in wanted.items():
        if not path.exists():
            fetch_one("ST.opnl", "conus", "001-003", element, config.RAW_DATA_DIR)
    missing = [e for e, p in wanted.items() if not p.exists()]
    if missing:
        raise SystemExit(f"could not fetch: {missing}")
    return wanted


def _inspect(path: Path) -> list[dict]:
    """Per-message stats from a live GRIB file."""
    import pygrib
    msgs = []
    with pygrib.open(str(path)) as grbs:
        for g in grbs:
            raw = np.ma.filled(g.values, np.nan).ravel()
            f = k_to_f(raw)
            _, n_bad = guard_range(f)
            finite = f[np.isfinite(f)]
            msgs.append({
                "valid": g.validDate.replace(tzinfo=__import__("datetime").timezone.utc),
                "units": getattr(g, "units", "?"),
                "n_cells": raw.size,
                "n_masked": int(np.isnan(raw).sum()),   # masked by pygrib up front
                "n_guarded": n_bad,                       # extra cells the guard caught
                "fmin": float(finite.min()) if finite.size else float("nan"),
                "fmax": float(finite.max()) if finite.size else float("nan"),
            })
    return msgs


def check_masking(maxt_msgs) -> str:
    print("\n[1] MASKING — guard should catch only a few cells, not a block")
    worst = FAIL if not maxt_msgs else PASS
    for m in maxt_msgs:
        frac = m["n_guarded"] / m["n_cells"]
        status = FAIL if frac > MASK_FAIL_FRACTION else PASS
        worst = FAIL if status == FAIL else worst
        print(f"  {m['valid']:%Y-%m-%d %HZ}  units={m['units']:>6}  "
              f"masked={m['n_masked']:>7,}  guarded={m['n_guarded']:>6,} "
              f"({frac:6.3%})  range {m['fmin']:.0f}…{m['fmax']:.0f}°F  [{status}]")
    if worst == FAIL:
        print(f"  -> FAIL: guard exceeded {MASK_FAIL_FRACTION:.0%} — fill value is leaking, "
              f"not being masked. Do not publish.")
    return worst


def check_hourly(apt_msgs) -> str:
    print("\n[2] HOURLY — apt should be many sub-daily messages, not one per day")
    n = len(apt_msgs)
    deltas = [round((b["valid"] - a["valid"]).total_seconds() / 3600)
              for a, b in zip(apt_msgs, apt_msgs[1:])]
    print(f"  {n} apt messages in day-1–3 file; step hours between messages: {deltas}")
    # 3 days of genuinely hourly data is ~72 steps; even 3/6-hourly is well above 3.
    status = PASS if n >= 12 else (WARN if n > 3 else FAIL)
    if status != PASS:
        print(f"  -> {status}: only {n} messages — apt may be shipping daily grids; "
              f"the bucketing assumption is wrong, recheck before using feels-like/warm-night.")
    return status


def check_labels(maxt_msgs, apt_msgs) -> str:
    print("\n[3] LABELS — fcst_date attribution vs message valid times (eyeball these)")
    maxt_records = [(m["valid"], np.zeros(1)) for m in maxt_msgs]
    apt_records = [(m["valid"], np.zeros(1)) for m in apt_msgs]

    print("  maxt → daily:")
    for d in as_daily_maxt(maxt_records):
        print(f"    day{d['seq']}  fcst_date={d['fcst_date']}  valid_utc={d['valid_utc']}")
    print("  apt → feels-like (day max):")
    for d in as_apt_daymax(apt_records, "America/Chicago"):
        print(f"    day{d['seq']}  fcst_date={d['fcst_date']}  n_hours={d['n_hours']:>2}  "
              f"{d['valid_start_utc']} → {d['valid_utc']}")
    print("  apt → warm nights (overnight min, date = evening night begins):")
    for d in as_apt_nightmin(apt_records, "America/Chicago"):
        print(f"    day{d['seq']}  fcst_date={d['fcst_date']}  n_hours={d['n_hours']:>2}  "
              f"{d['valid_start_utc']} → {d['valid_utc']}")
    print("  -> PASS if each fcst_date sits on the day/evening its valid times describe.")
    return PASS  # human judgment; printed for review


def main() -> int:
    print(f"NDFD live decode smoke test — guard band {PLAUSIBLE_F[0]:.0f}…{PLAUSIBLE_F[1]:.0f}°F")
    raw = _ensure_raw()
    maxt_msgs = _inspect(raw["maxt"])
    apt_msgs = _inspect(raw["apt"])

    results = {
        "masking": check_masking(maxt_msgs),
        "hourly": check_hourly(apt_msgs),
        "labels": check_labels(maxt_msgs, apt_msgs),
    }
    print("\n" + "=" * 48)
    for name, status in results.items():
        print(f"  {name:8} {status}")
    failed = FAIL in results.values()
    print("=" * 48)
    print("OVERALL:", "FAIL — do not publish" if failed else "ok (review label section by eye)")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
