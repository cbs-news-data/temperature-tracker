"""
Project configuration — paths, plot defaults, and runtime settings.
Environment variable overrides live in .env (see .env.example).
"""

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Paths
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DATA_DIR = DATA_DIR / "raw"
PROCESSED_DATA_DIR = PROJECT_ROOT / os.getenv('OUTPUT_DIR', 'data/processed')
DOCUMENTATION_DIR = DATA_DIR / "documentation"
NOTEBOOKS_DIR = PROJECT_ROOT / "notebooks"
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
UTILS_DIR = PROJECT_ROOT / "utils"

# Heat pipeline (see README). The pipeline is decoupled in three stages:
#   fetch (data/raw) -> reformat (data/processed) -> present (viewer/, reads processed).
# build_heat.py writes only to PROCESSED_DATA_DIR; the viewer is the sole consumer,
# so the presentation layer can be relocated without touching fetch/reformat.
REFERENCE_DIR = DATA_DIR / "reference"      # places.csv + counties.geojson (yearly refresh)
VIEWER_DIR = PROJECT_ROOT / "viewer"        # presentation layer (Job 3), reads data/processed

# Ensure data dirs exist on first import
for _dir in [RAW_DATA_DIR, PROCESSED_DATA_DIR, DOCUMENTATION_DIR, REFERENCE_DIR]:
    _dir.mkdir(parents=True, exist_ok=True)

# Reproducibility
RANDOM_SEED = int(os.getenv('RANDOM_SEED', 42))

# Plot defaults
FIGURE_SIZE = (12, 8)
DPI = 300
PLOT_STYLE = 'seaborn-v0_8'

# File format defaults
DEFAULT_CSV_ENCODING = 'utf-8'
DEFAULT_DATE_FORMAT = '%Y-%m-%d'

# HTTP / API
API_TIMEOUT = 30
MAX_RETRIES = 3

# Data quality thresholds used by utils
MISSING_THRESHOLD = 0.5   # flag columns with more than 50% missing
OUTLIER_THRESHOLD = 3     # Z-score cutoff for outlier detection

# Pandas display defaults
import pandas as pd
pd.set_option('display.max_columns', 50)
pd.set_option('display.max_rows', 100)
pd.set_option('display.float_format', '{:,.2f}'.format)
pd.set_option('display.max_colwidth', 100)
