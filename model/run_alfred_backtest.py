r"""ALFRED 주간 백테스트. 중단해도 다시 실행하면 이어서 돕니다.

사용법 (PowerShell):
    $env:FRED_API_KEY = "<키>"
    cd C:\dev\mon2-bc\model
    .\.venv\Scripts\python.exe run_alfred_backtest.py
"""

from __future__ import annotations

import pandas as pd

from business_cycle.config import load_settings
from business_cycle.validation.phase7_runner import run_weekly_backtest

STRICT_START = pd.Timestamp("2013-06-14")
STRICT_END = pd.Timestamp("2026-08-14")


def main() -> int:
    settings = load_settings()
    output = settings.root / "outputs" / "robustness_validation" / "phase7"
    state = run_weekly_backtest(settings, output, STRICT_START, STRICT_END)
    print(f"완료 {state.completed}주 · 남은 {state.remaining}주")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
