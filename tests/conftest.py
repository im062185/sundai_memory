"""Put the repo root on sys.path so `bench.*` and `engram.*` import under pytest.

Added by lane D (step 10) because tests/ has no __init__.py and pytest's
prepend import mode inserts tests/, not the repo root. Integrator: if you
prefer this as a root conftest.py or a pytest.ini `pythonpath`, move it —
nothing here is lane-specific.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
