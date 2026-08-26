"""persist17w 라벨을 두 경로에서 만든다.

트랙 17은 `outputs/four_phase_v1_1/`의 확정 경로를 읽었다. 그 경로는 v1.1 라벨이고
지금 다시 재려는 것은 persist17w 라벨이므로, 같은 자리에서 읽을 수 없다.

## 무엇을 바꾸고 무엇을 그대로 두는가

바꾸는 것은 관측층의 후퇴기 게이트 하나뿐이다. 임계값, 설정 해시, `decide`, 신선도
정책은 동결 v1.1 그대로다. 그래서 이 라벨과 v1.1 라벨의 차이는 **경계 하나**이고,
트랙 17과의 전후 비교가 다른 것을 섞지 않는다.

## 실시간 경로는 다시 돌린다

앞 단계가 남긴 `outputs/boundary_verification/realtime/boundary_only.csv`를 그대로
쓰면 그 파일이 어느 게이트로 만들어졌는지 파일만 보고는 알 수 없다. 게이트 이름을
파일에 적어 다시 만든다 — 재실행의 방어는 "확인할 수 있는가"에 걸려 있다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pandas as pd

from ..config import Settings
from ..phase_returns.labels import PHASES, WITHHELD, Labelling
from ..slowdown_boundary import scoring as SC
from ..slowdown_boundary import variants as V

#: 트랙 22가 권고한 게이트. 여기서 다시 고르지 않는다.
GATE: Final[SC.SlowdownGate] = SC.SlowdownGate(persistence_weeks=17)

LABEL_DIR: Final[str] = "outputs/rotation_rerun/labels"
REVISED_FILE: Final[str] = "revised_persist17w.csv"
REAL_TIME_FILE: Final[str] = "real_time_persist17w.csv"


def _labelling(name: str, phase: pd.Series) -> Labelling:
    """국면 하나만 담은 최소 라벨링. 트랙 17 기계가 쓰는 것이 이것뿐이다."""

    values = phase.fillna("").astype(str)
    frame = pd.DataFrame(index=pd.Index([str(week) for week in phase.index], name="week"))
    frame["phase"] = values.where(values.isin(PHASES), WITHHELD).to_numpy()
    return Labelling(name, frame)


def build_revised(settings: Settings) -> tuple[Labelling, pd.DataFrame]:
    """최종 수정치 경로. 트랙 22의 확장 역사가 아니라 **동결 창**을 쓴다.

    확장 역사(1976~)는 표준화 창이 달라 v1.1과 96.5%만 일치한다. 트랙 17과 전후를
    견주려면 같은 창이어야 하므로 동결 창 그대로 간다.
    """

    prepared, config = V.build(settings)
    frame = V.path(prepared, config, V.Variant("persist17w", GATE, False))
    return _labelling("revised", frame["official_phase"]), frame


def load_real_time(settings: Settings) -> Labelling:
    """실시간 경로. `write_real_time`이 먼저 만들어 둔 파일을 읽는다."""

    path = Path(settings.root) / LABEL_DIR / REAL_TIME_FILE
    frame = pd.read_csv(path, index_col=0)
    frame.index = pd.Index([str(week) for week in frame.index], name="week")
    # 게이트 이름은 파일 안에 있다. 없으면 어느 설정으로 만든 것인지 확인할 수 없으므로
    # 읽지 않는다 — 재실행의 방어가 "확인할 수 있는가"에 걸려 있다.
    if "gate" not in frame.columns:
        raise ValueError(f"{path}에 게이트 이름이 없다. 다시 만들어야 한다.")
    recorded = str(frame["gate"].iloc[0])
    if recorded != GATE.name:
        raise ValueError(f"실시간 경로가 {recorded}로 만들어졌다. {GATE.name}가 필요하다.")
    phase = frame["official_phase"].fillna("").astype(str)
    # 보류는 국면 칸에 넣지 않는다. 지연 비용의 일부라 따로 센다.
    withheld = frame["phase_status"].astype(str).eq("withheld")
    return _labelling("real_time", phase.mask(withheld, WITHHELD))


def write(settings: Settings, name: str, frame: pd.DataFrame) -> Path:
    """라벨을 그대로 남긴다. 어느 게이트인지 파일 안에 적는다."""

    folder = Path(settings.root) / LABEL_DIR
    folder.mkdir(parents=True, exist_ok=True)
    out = frame.copy()
    out["gate"] = GATE.name
    path = folder / name
    out.to_csv(path)
    return path
