"""
Tests for the shaping layer in build_heat.write_points — the tidy/long, wide, and
GeoJSON outputs. This is pandas-only logic (no GRIB/geo deps), so it runs in CI.
The GRIB decode and the scipy/geopandas paths are covered by validate_live.py.
"""
import json
from datetime import datetime, timezone

import numpy as np
import pandas as pd

import build_heat


def _places():
    return pd.DataFrame({
        "place_id": ["0455000", "3651000"],
        "name": ["Phoenix city", "New York city"],
        "name_display": ["Phoenix", "New York"],
        "name_state": ["Phoenix, AZ", "New York, NY"],
        "state": ["AZ", "NY"],
        "lat": [33.45, 40.71], "lon": [-112.07, -74.01],
    })


def _days():
    return [
        {"seq": 1, "fcst_date": "2026-07-04", "valid_utc": "2026-07-05T02:00:00+00:00"},
        {"seq": 2, "fcst_date": "2026-07-05", "valid_utc": "2026-07-06T02:00:00+00:00"},
    ]


def test_write_points_outputs(tmp_path):
    out = _places()
    out["day1"] = build_heat.to_int_f([110.0, np.nan])   # NaN exercises the no-data path
    out["day2"] = build_heat.to_int_f([108.4, 95.6])     # rounding to whole °F
    days = _days()

    issued = datetime(2026, 7, 4, 13, 30, tzinfo=timezone.utc)
    build_heat.write_points(out, days, tmp_path, "heat", "test label", issued)

    # long.csv: one tidy row per place × day, NaN written as empty value_f
    long = pd.read_csv(tmp_path / "heat_long.csv")
    assert len(long) == 4
    assert set(long.columns) >= {"place_id", "value_f", "seq", "fcst_date", "valid_utc"}
    ny_day1 = long[(long.place_id == 3651000) & (long.seq == 1)].iloc[0]
    assert pd.isna(ny_day1["value_f"])

    # GeoJSON: NaN -> null, whole-degree rounding, metadata preserved
    gj = json.loads((tmp_path / "heat_points.geojson").read_text())
    assert [d["key"] for d in gj["metadata"]["days"]] == ["day1", "day2"]
    assert gj["metadata"]["issued_utc"] == "2026-07-04T13:30:00+00:00"
    phx, nyc = gj["features"][0]["properties"], gj["features"][1]["properties"]
    assert phx["day1"] == 110 and phx["day2"] == 108   # 108.4 -> 108
    assert nyc["day1"] is None and nyc["day2"] == 96    # NaN -> null; 95.6 -> 96
    assert gj["features"][0]["geometry"]["type"] == "Point"


def test_county_agg_decoupled_from_prefix():
    # The bug we fixed: aggregation must follow the PRODUCT, not the output prefix.
    assert build_heat.PRODUCTS["temp"]["county_agg"] == "max"
    assert build_heat.PRODUCTS["feelslike"]["county_agg"] == "max"
    assert build_heat.PRODUCTS["warmnight"]["county_agg"] == "min"
