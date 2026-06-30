"""Put the project root (and scripts/) on sys.path so tests can import
``config`` / ``utils`` and the pipeline scripts (``build_heat``, …)."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
