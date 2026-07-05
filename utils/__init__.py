"""Shared helpers for the heat pipeline.

Importing this package also puts the project root on sys.path, so `import config`
works regardless of the caller's working directory.
"""

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
