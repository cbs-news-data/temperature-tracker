"""
Project configuration — paths and runtime settings for the heat data pipeline.
Environment variable overrides live in .env (optional).
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Paths. The pipeline is decoupled in three stages:
#   fetch (data/raw) -> reformat (data/processed) -> present (external consumers).
# build_heat.py writes only to PROCESSED_DATA_DIR; the graphics-rig embed (and the
# reference viewer/) consume the published GeoJSON, so presentation can change
# without touching fetch/reformat.
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"                 # NDFD GRIB2 (never committed)
PROCESSED_DATA_DIR = PROJECT_ROOT / os.getenv("OUTPUT_DIR", "data/processed")
REFERENCE_DIR = DATA_DIR / "reference"          # places.csv + counties.geojson (~yearly refresh)
DOCUMENTATION_DIR = DATA_DIR / "documentation"
VIEWER_DIR = PROJECT_ROOT / "viewer"            # reference viewer (production map lives in graphics-rig)

# Ensure data dirs exist on first import
for _dir in [RAW_DATA_DIR, PROCESSED_DATA_DIR, REFERENCE_DIR, DOCUMENTATION_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)
