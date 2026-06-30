"""
Unit tests for the pure transform core (utils/heat.py), on synthetic grids.

These cover the temperature logic that cannot be checked against a live GRIB file
in CI: Kelvin→°F, the fill-value guard, whole-degree rounding, and the day/night
bucketing + date attribution. The live GRIB decode itself is covered separately by
scripts/validate_live.py.

Timezone note: tests bucket in America/Chicago, which is UTC−5 (CDT) in July, so
local hour = UTC hour − 5.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from utils.heat import (
    as_apt_daymax,
    as_apt_nightmin,
    as_daily_maxt,
    guard_range,
    k_to_f,
    to_int_f,
)


def utc(y, m, d, h):
    return datetime(y, m, d, h, tzinfo=timezone.utc)


# ----------------------------------------------------------------- k_to_f
def test_k_to_f_known_points():
    out = k_to_f([273.15, 310.927778, 9999.0])
    assert out[0] == 32.0
    assert abs(out[1] - 100.0) < 1e-6
    assert out[2] > 17000  # an unmasked fill blows way past any real temp


# ----------------------------------------------------------------- guard_range
def test_guard_flags_out_of_band_to_nan():
    vals, n_bad = guard_range([95.0, 17500.0, -200.0, 40.0])
    assert n_bad == 2
    assert np.isnan(vals[1]) and np.isnan(vals[2])
    assert vals[0] == 95.0 and vals[3] == 40.0


def test_guard_preserves_existing_nan_without_counting_it():
    vals, n_bad = guard_range([np.nan, 70.0])
    assert n_bad == 0
    assert np.isnan(vals[0]) and vals[1] == 70.0


def test_guard_does_not_mutate_input():
    src = np.array([200.0, 50.0])  # 200°F is out of band
    guard_range(src)
    assert src[0] == 200.0  # original untouched


# ----------------------------------------------------------------- to_int_f
def test_to_int_f_rounds_and_keeps_na():
    out = to_int_f([89.4, 89.6, np.nan])
    assert out[0] == 89 and out[1] == 90
    assert pd.isna(out[2])
    assert out.dtype == "Int64"


# ----------------------------------------------------------------- as_daily_maxt
def test_daily_maxt_one_day_per_message_and_sorted():
    # Out-of-order input; period-end stamps in the small hours UTC.
    recs = [
        (utc(2026, 7, 6, 2), np.array([100.0])),
        (utc(2026, 7, 5, 2), np.array([95.0])),
    ]
    days = as_daily_maxt(recs)
    assert [d["seq"] for d in days] == [1, 2]
    # LABEL_SHIFT (−8h) pulls the 02Z stamp back onto the daytime it describes.
    assert days[0]["fcst_date"] == "2026-07-04"
    assert days[1]["fcst_date"] == "2026-07-05"
    assert np.array_equal(days[0]["vals_f"], [95.0])


# ----------------------------------------------------------------- apt day-max
def test_apt_daymax_takes_per_cell_max_over_local_day():
    recs = [
        (utc(2026, 7, 4, 18), np.array([80.0, 60.0])),  # local 13:00, 07-04
        (utc(2026, 7, 4, 20), np.array([95.0, 55.0])),  # local 15:00, 07-04
        (utc(2026, 7, 4, 22), np.array([85.0, 70.0])),  # local 17:00, 07-04
        (utc(2026, 7, 5, 19), np.array([100.0, 90.0])),  # local 14:00, 07-05
    ]
    days = as_apt_daymax(recs, "America/Chicago")
    assert [d["fcst_date"] for d in days] == ["2026-07-04", "2026-07-05"]
    assert days[0]["n_hours"] == 3
    assert np.array_equal(days[0]["vals_f"], [95.0, 70.0])  # per-cell max
    assert np.array_equal(days[1]["vals_f"], [100.0, 90.0])


def test_apt_daymax_ignores_nan_cells():
    recs = [
        (utc(2026, 7, 4, 18), np.array([np.nan])),
        (utc(2026, 7, 4, 20), np.array([88.0])),
    ]
    days = as_apt_daymax(recs, "America/Chicago")
    assert np.array_equal(days[0]["vals_f"], [88.0])  # fmax ignores the NaN hour


# ----------------------------------------------------------------- apt night-min
def test_apt_nightmin_window_attribution_and_min():
    recs = [
        (utc(2026, 7, 5, 2), np.array([78.0])),   # local 21:00 07-04 (evening starts)
        (utc(2026, 7, 5, 6), np.array([72.0])),   # local 01:00 07-05 -> prior evening 07-04
        (utc(2026, 7, 5, 11), np.array([70.0])),  # local 06:00 07-05 -> prior evening 07-04
        (utc(2026, 7, 5, 19), np.array([100.0])),  # local 14:00 daytime -> excluded
    ]
    nights = as_apt_nightmin(recs, "America/Chicago")
    assert len(nights) == 1
    assert nights[0]["fcst_date"] == "2026-07-04"  # the evening the night begins
    assert nights[0]["n_hours"] == 3               # daytime reading excluded
    assert np.array_equal(nights[0]["vals_f"], [70.0])  # per-cell min over the night


def test_apt_nightmin_excludes_daytime_only():
    recs = [(utc(2026, 7, 4, 19), np.array([99.0]))]  # local 14:00, not a night hour
    assert as_apt_nightmin(recs, "America/Chicago") == []
