#!/usr/bin/env python3
"""미국 4국면 운영 러너의 저장소 최상위 진입점."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "model" / "src"))

from business_cycle.operational import main

if __name__ == "__main__":
    raise SystemExit(main())
