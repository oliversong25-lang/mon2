"""네트워크 없이 실행 가능한 로컬 CSV 입력."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .availability import validate_observations


def load_local(data_dir: Path) -> pd.DataFrame:
    """통합 observations.csv 또는 지표별 CSV 파일을 읽는다."""

    combined = data_dir / "observations.csv"
    if combined.exists():
        return validate_observations(pd.read_csv(combined))
    frames: list[pd.DataFrame] = []
    for path in sorted(data_dir.glob("*.csv")):
        frame = pd.read_csv(path)
        if "indicator_id" not in frame:
            frame["indicator_id"] = path.stem.upper()
        if "observation_period" not in frame and "date" in frame:
            frame = frame.rename(columns={"date": "observation_period"})
        if "value" not in frame and frame.shape[1] == 3:
            candidates = [
                c for c in frame.columns if c not in {"indicator_id", "observation_period"}
            ]
            if len(candidates) == 1:
                frame = frame.rename(columns={candidates[0]: "value"})
        frames.append(frame)
    if not frames:
        raise FileNotFoundError(f"CSV 파일을 찾지 못했습니다: {data_dir}")
    return validate_observations(pd.concat(frames, ignore_index=True))
