"""
Pure transform core for the NDFD heat-forecast pipeline.

Everything here is a pure function over plain arrays and datetimes — no GRIB,
no geopandas, no network. That keeps the temperature logic (Kelvin to °F, the
fill-value guard, day/night bucketing, date attribution) unit-testable on
synthetic grids, separate from the I/O layer in ``scripts/build_heat.py``.

A "record" is one decoded GRIB message: ``(valid_utc, vals_f)`` where
``valid_utc`` is a timezone-aware UTC datetime and ``vals_f`` is a 1-D NumPy
array of guarded °F values, one per grid cell.

A "day" is the rolled-up result for one forecast period, a dict with:
``seq`` (1-based), ``fcst_date`` (ISO date the value describes), ``valid_utc``,
``vals_f`` (per-cell array), and — for the hourly ``apt`` products —
``n_hours`` and ``valid_start_utc``.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

# Plausible CONUS band (°F). Spans the U.S. heat record (134°F) and deep-winter
# wind-chill apparent temps, while killing unmasked GRIB fills (9999 K ≈ 17,500°F)
# that would otherwise poison a place value or a county aggregate.
PLAUSIBLE_F = (-80.0, 145.0)

# Warm-night window: local 8pm–8am. A reading before 8am belongs to the night
# that began the *previous* evening (so "the night of the 4th" runs into the 5th).
NIGHT_HOURS = set(range(20, 24)) | set(range(0, 8))
NIGHT_MORNING_END = 8

# NDFD maxt is stamped at the period end, which lands in the small hours UTC.
# Shift back so the date label sits on the daytime the value actually describes.
LABEL_SHIFT = timedelta(hours=8)


def k_to_f(kelvin) -> np.ndarray:
    """Convert Kelvin to °F as float64 (NDFD encodes temperatures in Kelvin)."""
    return (np.asarray(kelvin, dtype="float64") - 273.15) * 9.0 / 5.0 + 32.0


def guard_range(f) -> tuple[np.ndarray, int]:
    """Force values outside ``PLAUSIBLE_F`` to NaN. Returns (values, n_flagged).

    A *large* flagged count means GRIB fill masking is wrong — not a few coastal
    NaNs. Callers should surface the count so that shows up loudly.
    """
    f = np.asarray(f, dtype="float64").copy()
    bad = ~np.isnan(f) & ((f < PLAUSIBLE_F[0]) | (f > PLAUSIBLE_F[1]))
    f[bad] = np.nan
    return f, int(bad.sum())


def to_int_f(values) -> "pd.array":
    """Round to whole °F as nullable Int64. NDFD is populated in whole degrees;
    tenths are false precision. NaN survives as pandas NA."""
    return pd.array(np.round(np.asarray(values, dtype="float64")), dtype="Int64")


def as_daily_maxt(records: list[tuple[datetime, np.ndarray]]) -> list[dict]:
    """maxt ships one daytime-max grid per message — each record is its own day."""
    ordered = sorted(records, key=lambda r: r[0])
    return [
        {
            "seq": i,
            "valid_utc": valid.isoformat(),
            "fcst_date": (valid - LABEL_SHIFT).date().isoformat(),
            "vals_f": vals,
        }
        for i, (valid, vals) in enumerate(ordered, start=1)
    ]


def bucket_by_local_day(
    records: list[tuple[datetime, np.ndarray]],
    tz_name: str,
    agg: str,
    night: bool = False,
) -> list[dict]:
    """Bucket sub-daily grids by local calendar day and reduce per cell.

    ``agg`` is ``"max"`` (daytime high) or ``"min"`` (overnight low). With
    ``night=True`` only the 8pm–8am window counts and pre-8am readings roll into
    the prior evening's date. Both reducers ignore NaN, so a single bad cell in
    one hour does not erase a real value from another.
    """
    tz = ZoneInfo(tz_name)
    combine = np.fmax if agg == "max" else np.fmin
    buckets: dict = {}
    for valid, vals in sorted(records, key=lambda r: r[0]):
        local = valid.astimezone(tz)
        if night and local.hour not in NIGHT_HOURS:
            continue
        day = local.date()
        if night and local.hour < NIGHT_MORNING_END:
            day = day - timedelta(days=1)
        b = buckets.get(day)
        if b is None:
            buckets[day] = {"acc": vals.copy(), "n": 1, "start": valid, "end": valid}
        else:
            b["acc"] = combine(b["acc"], vals)
            b["n"] += 1
            b["start"] = min(b["start"], valid)
            b["end"] = max(b["end"], valid)

    days = []
    for i, day in enumerate(sorted(buckets), start=1):
        b = buckets[day]
        days.append(
            {
                "seq": i,
                "fcst_date": day.isoformat(),
                "valid_utc": b["end"].isoformat(),
                "valid_start_utc": b["start"].isoformat(),
                "n_hours": b["n"],
                "vals_f": b["acc"],
            }
        )
    return days


def as_apt_daymax(records, tz_name: str) -> list[dict]:
    """Feels-like daily high: per-cell MAX of hourly apt over each local day."""
    return bucket_by_local_day(records, tz_name, "max", night=False)


def as_apt_nightmin(records, tz_name: str) -> list[dict]:
    """Warm-night low: per-cell MIN of hourly apt over each local 8pm–8am window."""
    return bucket_by_local_day(records, tz_name, "min", night=True)


def day_meta(day: dict) -> dict:
    """Lightweight per-day metadata for GeoJSON headers (drops the value array)."""
    meta = {"key": f"day{day['seq']}", "fcst_date": day["fcst_date"], "valid_utc": day["valid_utc"]}
    if "n_hours" in day:
        meta["n_hours"] = day["n_hours"]
        meta["valid_start_utc"] = day["valid_start_utc"]
    return meta
