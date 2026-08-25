"""국면마다 **따로** 정의한 후반부 신호.

네 국면을 하나의 점수로 합치지 않는다. 확장기 후반에서 작동하고 침체기 후반에서 실패하는
신호는 부분적 성공이며, 그렇게 적어야 한다.

각 신호는 세 개의 이름 붙은 조건으로 이루어진다. 점수는 충족한 조건의 비율이라 0, 1/3,
2/3, 1 중 하나다. `LATE_THRESHOLD` 이상을 "후반"으로 부른다 — 미리 정해 두고 결과를 보고
바꾸지 않는다.
"""

from __future__ import annotations

from typing import Any, Final

import pandas as pd

#: 세 조건 중 둘 이상. 미리 정한 값이며 결과를 보고 조정하지 않는다.
LATE_THRESHOLD: Final[float] = 2.0 / 3.0

#: 국면 순서. Track 18이 전제하는 시계다. 이 모델의 실제 경로가 이 순서를 따르는지는
#: 검증에서 따로 잰다 — 전제로 두지 않는다.
SUCCESSOR: Final[dict[str, str]] = {
    "recovery": "expansion",
    "expansion": "slowdown",
    "slowdown": "contraction",
    "contraction": "recovery",
}

#: 각 신호가 쓰는 입력. 보고서에 그대로 싣는다.
INPUTS: Final[dict[str, tuple[str, ...]]] = {
    "expansion": ("level", "d_momentum", "d_breadth"),
    "slowdown": ("d_level", "momentum", "d_negative_momentum_domains"),
    "contraction": ("level", "d_momentum", "d_breadth"),
    "recovery": ("level", "momentum", "d_momentum"),
}

#: 격차 변환으로 **새로 들어오는** 입력. 후퇴기·회복기에는 없다 — Track 18이 제안하는
#: 새 자료가 그 두 국면을 겨냥하지 않기 때문이며, 그 빈칸을 채운 척하지 않는다.
GAP_INPUTS: Final[dict[str, tuple[str, ...]]] = {
    "expansion": ("capacity_gap", "unemployment_gap"),
    "slowdown": (),
    "contraction": ("claims_off_peak",),
    "recovery": (),
}

#: 청구건수가 연중 고점에서 이만큼 내려왔으면 저점을 지나는 중으로 본다.
CLAIMS_OFF_PEAK: Final[float] = 0.10


def _gap_conditions(gaps: pd.DataFrame, phase: str) -> dict[str, pd.Series]:
    """격차 변환이 더하는 조건. 값이 없는 주는 **거짓**으로 둔다 — 모르는 것을 근거로
    후반이라고 말하지 않기 위해서다."""

    if phase == "expansion":
        tight = (gaps["capacity_gap"] > 0) | (gaps["unemployment_gap"] < 0)
        return {"capacity_or_labour_tight": tight.fillna(False)}
    if phase == "contraction":
        passing = gaps["claims_off_peak"] > CLAIMS_OFF_PEAK
        return {"claims_off_their_peak": passing.fillna(False)}
    return {}


def _conditions(frame: pd.DataFrame, phase: str) -> dict[str, pd.Series]:
    """국면별 후반부 조건. 부호의 뜻을 각각 주석으로 남긴다."""

    if phase == "expansion":
        return {
            # 수준은 아직 높다. 후반이지 이미 꺾인 것이 아니다.
            "level_still_positive": frame["level"] > 0,
            # 모멘텀이 둔화된다. 방향이 아니라 **변화**를 본다.
            "momentum_decelerating": frame["d_momentum"] < 0,
            # 폭이 좁아진다. 소수 도메인만 확장을 떠받친다.
            "breadth_narrowing": frame["d_breadth"] < 0,
        }
    if phase == "slowdown":
        return {
            # 수준이 내려가고 있다.
            "level_falling": frame["d_level"] < 0,
            # 모멘텀이 이미 음이다.
            "momentum_negative": frame["momentum"] < 0,
            # 악화가 번진다. 음의 모멘텀 도메인이 늘고 있다.
            "deterioration_spreading": frame["d_negative_momentum_domains"] > 0,
        }
    if phase == "contraction":
        return {
            # 수준은 아직 음이다. 저점 **통과**지 회복 완료가 아니다.
            "level_still_negative": frame["level"] < 0,
            # 악화 속도가 줄어든다. 모멘텀이 음이어도 덜 음이면 후반이다.
            "deterioration_shrinking": frame["d_momentum"] > 0,
            # 확인 도메인이 늘기 시작한다.
            "breadth_widening": frame["d_breadth"] > 0,
        }
    if phase == "recovery":
        return {
            # 수준이 정상으로 돌아왔다. 회복의 일이 끝나 간다.
            "level_back_to_normal": frame["level"] > 0,
            # 모멘텀은 아직 양이다.
            "momentum_still_positive": frame["momentum"] > 0,
            # 그러나 둔화되고 있다. 반등의 기울기가 꺾인다.
            "momentum_decelerating": frame["d_momentum"] < 0,
        }
    raise KeyError(phase)


def score(frame: pd.DataFrame, gaps: pd.DataFrame | None = None) -> pd.DataFrame:
    """주마다 **현재 국면에 해당하는** 후반부 점수만 계산한다.

    다른 국면의 후반부 점수는 뜻이 없다. 확장기가 아닌 주의 "확장기 후반 점수"는 해석할
    자리가 없기 때문이다.
    """

    out = pd.DataFrame(index=frame.index)
    out["phase"] = frame["phase"]
    out["maturity"] = float("nan")
    for name in SUCCESSOR:
        mask = frame["phase"].eq(name)
        if not bool(mask.any()):
            continue
        checks = _conditions(frame, name)
        if gaps is not None:
            checks.update(_gap_conditions(gaps.reindex(frame.index), name))
        total = pd.Series(0.0, index=frame.index)
        for check in checks.values():
            total = total + check.astype(float)
        out.loc[mask, "maturity"] = (total / len(checks))[mask]
        for label, check in checks.items():
            column = f"{name}__{label}"
            out[column] = float("nan")
            out.loc[mask, column] = check[mask].astype(float)
    out["late"] = out["maturity"] >= LATE_THRESHOLD
    # 2차 읽기가 아직 채워지지 않은 앞부분은 후반이라고 말하지 않는다.
    out.loc[frame["d_momentum"].isna(), "late"] = False
    out.loc[frame["d_momentum"].isna(), "maturity"] = float("nan")
    return out


def describe(phase: str, maturity: float) -> dict[str, Any]:
    """상태 서술. **예측형 문장을 만들지 않는다.**

    "곧 후퇴기가 옵니다"는 예측이고, "확장기 후반의 특징이 나타납니다"는 현재 상태의
    서술이다. 정보는 같지만 뒤의 것만 이 프로젝트의 원칙과 규제 경계에 맞는다.
    """

    stage = "후반" if maturity >= LATE_THRESHOLD else ("중반" if maturity >= 1 / 3 else "초반")
    wording = {
        ("expansion", "후반"): (
            "확장기 후반의 특징이 나타납니다. 활동 수준은 여전히 높은 편이지만 "
            "모멘텀이 둔화되고 있고, 확장을 확인해 주는 지표의 수가 줄고 있습니다."
        ),
        ("expansion", "중반"): (
            "확장기의 특징이 이어지고 있습니다. 수준과 모멘텀이 함께 유지되고 있습니다."
        ),
        ("expansion", "초반"): ("확장기 초반의 특징이 나타납니다. 모멘텀이 아직 붙는 중입니다."),
        ("slowdown", "후반"): (
            "후퇴기 후반의 특징이 나타납니다. 활동 수준이 내려가는 중이고 "
            "약화가 여러 부문으로 번지고 있습니다."
        ),
        ("slowdown", "중반"): ("후퇴기의 특징이 이어지고 있습니다. 모멘텀이 둔화된 상태입니다."),
        ("slowdown", "초반"): (
            "후퇴기 초반의 특징이 나타납니다. 둔화가 아직 일부 부문에 머물러 있습니다."
        ),
        ("contraction", "후반"): (
            "침체기 후반의 특징이 나타납니다. 활동 수준은 아직 낮지만 "
            "악화 속도가 줄고 있고, 개선을 보이는 부문이 늘고 있습니다."
        ),
        ("contraction", "중반"): (
            "침체기의 특징이 이어지고 있습니다. 활동 수준이 낮은 상태입니다."
        ),
        ("contraction", "초반"): ("침체기 초반의 특징이 나타납니다. 악화가 계속되고 있습니다."),
        ("recovery", "후반"): (
            "회복기 후반의 특징이 나타납니다. 활동 수준이 정상 범위로 돌아왔고 "
            "반등의 기울기가 완만해지고 있습니다."
        ),
        ("recovery", "중반"): ("회복기의 특징이 이어지고 있습니다. 반등이 진행 중입니다."),
        ("recovery", "초반"): (
            "회복기 초반의 특징이 나타납니다. 저점을 지난 신호가 나타나기 시작했습니다."
        ),
    }
    return {
        "phase": phase,
        "maturity": round(float(maturity), 3),
        "stage": stage,
        "wording": wording[(phase, stage)],
        "form": "state_description",
    }
