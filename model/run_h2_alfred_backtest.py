r"""후보 H2의 ALFRED 주간 백테스트. 중단해도 다시 실행하면 이어서 돕니다.

후보 H의 산출물(outputs/robustness_validation/phase7)은 건드리지 않는다. 실패한 검증
기록으로 그대로 보존해야 하므로 H2는 별도 디렉터리에 쓴다.

사용법 (PowerShell):
    $env:FRED_API_KEY = "<키>"
    cd C:\dev\mon2-bc\model
    .\.venv\Scripts\python.exe run_h2_alfred_backtest.py
"""

from __future__ import annotations

import sys

import pandas as pd

from business_cycle.config import load_settings
from business_cycle.validation.phase7_runner import run_weekly_backtest

CANDIDATE = "candidate_h2_systemic_override"
STRICT_START = pd.Timestamp("2013-06-14")
STRICT_END = pd.Timestamp("2026-08-14")


def main() -> int:
    # 사용법: python run_h2_alfred_backtest.py [샤드번호] [샤드수]
    shard = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    shards = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    settings = load_settings()
    output = settings.root / "outputs" / "robustness_validation" / "phase8" / "alfred"
    state = run_weekly_backtest(
        settings,
        output,
        STRICT_START,
        STRICT_END,
        candidate=CANDIDATE,
        shard=shard,
        shards=shards,
    )
    print(f"완료 {state.completed}주 · 남은 {state.remaining}주")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
