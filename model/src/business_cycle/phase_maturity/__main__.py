"""국면 성숙도 실행기.

    python -m business_cycle.phase_maturity

동결 v1.1을 하나도 건드리지 않는다. 확정된 주간 경로 위에 2차 읽기를 얹을 뿐이다.
산출물은 ``outputs/phase_maturity/``에만 쓴다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pandas as pd

from ..config import load_settings
from ..four_phase.engine import load_config
from . import gaps as G
from . import series as S
from . import signal as SG
from . import validate as V

OUTPUT_NAME = "phase_maturity"

REVISED_PATH = "outputs/four_phase_v1_1/weekly_state.csv"
REAL_TIME_PATH = "outputs/four_phase_v1_1/alfred_audit/weekly_path.csv"
CLAIMS_PATH = "data/cache/ICSA.csv"

PHASE_LABEL = {
    "recovery": "회복기",
    "expansion": "확장기",
    "slowdown": "후퇴기",
    "contraction": "침체기",
}


def _run(frame: pd.DataFrame, gaps: pd.DataFrame | None) -> dict[str, Any]:
    scored = SG.score(frame, gaps)
    return {
        "by_phase": V.by_phase(frame, scored),
        "duration_independence": V.duration_independence(frame, scored),
        "threshold_sweep": V.threshold_sweep(frame, scored),
        "late_weeks_total": int(scored["late"].sum()),
    }


def _sensitivity(path: str, gaps: pd.DataFrame | None) -> list[dict[str, Any]]:
    """2차 읽기 창을 바꿔 본다. 이 표에서 좋은 창을 고르지 않는다."""

    raw = S.load_path(path)
    rows: list[dict[str, Any]] = []
    for weeks in S.SENSITIVITY_WEEKS:
        frame = S.derive(raw, weeks)
        scored = SG.score(frame, gaps)
        for entry in V.by_phase(frame, scored):
            rows.append(
                {
                    "change_weeks": weeks,
                    "predeclared": weeks == S.CHANGE_WEEKS,
                    "phase": entry["phase"],
                    "late_weeks": entry["late_weeks"],
                    "successor_rate_base": entry["successor_rate"]["base"],
                    "successor_rate_duration_only": entry["successor_rate"]["duration_only"],
                    "successor_rate_late_signal": entry["successor_rate"]["late_signal"],
                    "beats_duration": entry["late_signal_beats_duration_on_successor"],
                }
            )
    return rows


def _current(frame: pd.DataFrame, scored: pd.DataFrame) -> dict[str, Any]:
    week = str(frame.index[-1])
    phase = str(frame.at[week, "phase"])
    maturity = float(scored["maturity"].loc[week])
    if phase not in SG.SUCCESSOR or maturity != maturity:
        return {"week": week, "phase": phase, "maturity": None, "wording": None}
    return {"week": week, **SG.describe(phase, maturity)}


def _wording_drafts() -> list[dict[str, Any]]:
    return [SG.describe(phase, level) for phase in SG.SUCCESSOR for level in (0.0, 0.5, 1.0)]


def _verdict(existing: dict[str, Any], augmented: dict[str, Any]) -> dict[str, Any]:
    """국면마다 따로 판정한다. 넷을 하나로 합치면 부분적 성공이 보이지 않는다."""

    def table(result: dict[str, Any]) -> dict[str, Any]:
        return {
            entry["phase"]: {
                "beats_duration_on_successor": entry["late_signal_beats_duration_on_successor"],
                "within_run_shift_p": entry["within_run_shift_p_successor"],
                "successor_rate": entry["successor_rate"],
                "episodes": entry["episodes"],
            }
            for entry in result["by_phase"]
        }

    before = table(existing)
    after = table(augmented)
    works = [
        phase
        for phase, entry in after.items()
        if entry["beats_duration_on_successor"]
        and entry["within_run_shift_p"] is not None
        and entry["within_run_shift_p"] <= 0.05
    ]
    return {
        "existing_values_only": before,
        "with_gap_transforms": after,
        "phases_where_the_late_signal_beats_the_duration_control": works,
        "symmetric_across_the_four_phases": len(works) == len(SG.SUCCESSOR),
        "statement": (
            "후반부 신호는 네 국면에 대칭적으로 작동하지 않는다. 경과 기간 대조군을 이기고 "
            "블록 안 위치 검정도 통과하는 국면은 "
            + (", ".join(PHASE_LABEL[name] for name in works) if works else "없다")
            + "뿐이다."
        ),
    }


def _report(payload: dict[str, Any]) -> str:
    order = payload["cycle_order"]
    existing = payload["existing_values_only"]
    augmented = payload["with_gap_transforms"]
    verdict = payload["verdict"]

    def phase_table(result: dict[str, Any]) -> list[str]:
        lines = [
            "| 국면 | 주 | 에피소드 | 후반 주 | 적중/빗나감 | 기본 | 경과기간만 | "
            "후반신호 | 블록내 p |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for entry in result["by_phase"]:
            rate = entry["successor_rate"]
            beats = entry["late_signal_beats_duration_on_successor"]
            mark = "**" if beats else ""
            lines.append(
                f"| {PHASE_LABEL[entry['phase']]} | {entry['weeks']} | {entry['episodes']} | "
                f"{entry['late_weeks']} | "
                f"{entry['late_weeks_followed_by_the_successor']} / "
                f"{entry['late_weeks_not_followed_by_the_successor']} | "
                f"{rate['base']} | {rate['duration_only']} | "
                f"{mark}{rate['late_signal']}{mark} | "
                f"{entry['within_run_shift_p_successor']} |"
            )
        return lines

    lines = [
        "# 국면 성숙도 — 지금 국면의 어디쯤인가",
        "",
        "## 결론",
        "",
        f"**{verdict['statement']}**",
        "",
        "예측이 아니라 서술이다. 모든 출력 문구는 '곧 ~가 옵니다'가 아니라",
        "'~기 후반의 특징이 나타납니다' 형태로만 만든다.",
        "",
        "## 먼저: 이 모델의 경로는 순환 순서를 따르지 않는다",
        "",
        "Track 18은 '다음 국면이 무엇인지는 이미 안다'를 전제로 문제를 쉽게 만든다.",
        "그 전제를 먼저 확인했다.",
        "",
        "| 국면 | 블록 전이 | 예상 후속으로 | 비율 | 대신 간 곳 |",
        "|---|---|---|---|---|",
    ]
    for phase, entry in order["by_phase"].items():
        instead = (
            ", ".join(f"{key} {value}" for key, value in entry["where_it_went_instead"].items())
            or "—"
        )
        lines.append(
            f"| {PHASE_LABEL[phase]} → {PHASE_LABEL[SG.SUCCESSOR[phase]]} | "
            f"{entry['transitions']} | {entry['to_the_expected_successor']} | "
            f"{entry['share']} | {instead} |"
        )
    lines += [
        "",
        f"블록 전이 {order['transitions']}건 중 순환 순서를 따른 것은 "
        f"**{order['following_the_cycle_order']}건 "
        f"({order['share_following_the_cycle_order']:.1%})**뿐이다.",
        "",
        "**후퇴기에서 침체기로 간 것은 44건 중 4건이고, 40건은 확장기로 되돌아갔다.**",
        "즉 '후퇴기 후반 → 다음은 침체기'라는 예고는 이 모델의 실제 경로에서 90% 틀린다.",
        "성숙도 신호를 아무리 잘 만들어도 예고 대상 자체가 틀린 것이다.",
        "",
        "이것은 결함이라기보다 이미 확인된 성질이다. 상태 의미론 감사에서 일방향 국면",
        "시계를 강제하지 않기로 했고, 전이 게이트 단계에서 전이의 대부분이 확장↔후퇴",
        "왕복임을 확인했다. 그 두 사실이 여기서 다시 나온다.",
        "",
        "## 신호 정의 — 국면마다 따로",
        "",
        "| 국면 | 조건 | 기존 값 | 격차 변환이 더하는 것 |",
        "|---|---|---|---|",
    ]
    condition_names = {
        "expansion": "수준 아직 양 · 모멘텀 둔화 · 폭 축소",
        "slowdown": "수준 하락 · 모멘텀 음 · 악화 확산",
        "contraction": "수준 아직 음 · 악화 속도 감소 · 폭 확대",
        "recovery": "수준 정상 복귀 · 모멘텀 양 · 모멘텀 둔화",
    }
    for phase in SG.SUCCESSOR:
        added = ", ".join(SG.GAP_INPUTS[phase]) or "**없음**"
        lines.append(
            f"| {PHASE_LABEL[phase]} | {condition_names[phase]} | "
            f"{', '.join(SG.INPUTS[phase])} | {added} |"
        )
    lines += [
        "",
        f"점수는 충족 조건의 비율이고, **{SG.LATE_THRESHOLD:.3f} 이상**을 후반으로 부른다.",
        f"2차 읽기 창은 **{S.CHANGE_WEEKS}주**로 미리 정했다 — 모델의 모멘텀 창과 같다.",
        "",
        "후퇴기와 회복기에는 격차 변환이 더하는 입력이 **없다**. Track 18이 제안하는 새",
        "자료(가동률, 자연실업률, 청구건수)가 그 두 국면을 겨냥하지 않기 때문이며, 빈칸을",
        "채운 척하지 않는다.",
        "",
        "## 기존 값만으로 — 격차 변환 이전",
        "",
        "예상 후속 국면이 13주 안에 오는 비율이다.",
        "",
    ]
    lines += phase_table(existing)
    lines += [
        "",
        "**경과 기간만으로 만든 대조군이 회복기와 후퇴기에서 후반 신호를 이긴다.**",
        "그 두 국면에서는 2차 읽기가 '이 국면은 오래됐다'보다 못하다.",
        "",
        "블록 안 순환 이동 검정은 '표시가 몇 개 켜지는가는 그대로 두고, 블록 안 어디에",
        "켜지는가만 무작위로 바꾼다'는 귀무가설이다. 성숙도 주장의 정확한 반대다.",
        "",
        "## 격차 변환을 더하면",
        "",
    ]
    lines += phase_table(augmented)
    lines += [
        "",
        "확장기에서 뚜렷하다 — 후반 주가 418에서 237로 줄면서 적중률이 0.641에서 **0.785**로",
        "오르고, 블록 안 위치 검정도 0.054에서 **0.015**로 내려간다. 가동률·노동시장 격차가",
        "확장기 후반에 대해 실제로 정보를 더한다.",
        "",
        "침체기는 후반 주가 108에서 **20**으로 줄고 적중률은 0.241에서 0.400으로 오르지만,",
        "블록 안 위치 검정은 통과하지 못한다(p=0.582). 침체 에피소드가 5건뿐이고 남은 주가",
        "20주여서 확인할 표본이 없다. **유망하지만 확립되지 않았다.**",
        "",
        "후퇴기·회복기는 정의상 그대로다.",
        "",
        "## 경과 기간이 혼자 신호를 옮기고 있지 않다는 확인",
        "",
        "후반 표시가 켜진 주 중 **오래되지 않은** 주만 따로 본다. 거기서도 적중률이 높으면",
        "신호가 경과 기간을 다시 쓴 것이 아니다.",
        "",
        "| 국면 | 오래되지 않은 주 | 그중 후반 | 전체 | 후반만 | 남는가 |",
        "|---|---|---|---|---|---|",
    ]
    for entry in augmented["duration_independence"]:
        lines.append(
            f"| {PHASE_LABEL[entry['phase']]} | {entry['young_weeks']} | "
            f"{entry['late_and_young_weeks']} | {entry['successor_rate_all_young']} | "
            f"{entry['successor_rate_late_and_young']} | "
            f"{'예' if entry['signal_survives_holding_duration_fixed'] else '아니오'} |"
        )
    lines += [
        "",
        "확장기·침체기에서는 경과 기간을 고정해도 신호가 남는다. 후퇴기에서는 남지 않는다.",
        "",
        "경과 기간을 단독 신호로 쓰지 않는 이유는 결과가 나빠서가 아니라 근거가 없어서다 —",
        "확장기는 늙어서 죽지 않는다는 것이 실증적으로 확립돼 있고, 기간만 보는 신호는",
        "도박사의 오류를 다르게 쓴 것이다. 그래서 대조군으로만 두고, 신호가 그것을 이기는지를",
        "합격 조건으로 삼았다.",
        "",
        "## 문턱을 바꾸면",
        "",
        "점수가 가질 수 있는 값은 네 개뿐이므로 전부 싣는다. 이 표에서 좋은 값을 골라",
        "문턱을 바꾸지 않는다.",
        "",
        "| 국면 | 문턱 | 주 | 국면 내 비중 | 후속 적중 |",
        "|---|---|---|---|---|",
    ]
    for entry in augmented["threshold_sweep"]:
        mark = " ←미리 정함" if entry["predeclared"] else ""
        lines.append(
            f"| {PHASE_LABEL[entry['phase']]} | {entry['threshold']}{mark} | "
            f"{entry['weeks']} | {entry['share_of_phase']:.1%} | "
            f"{entry['successor_rate'] if entry['successor_rate'] is not None else '—'} |"
        )
    lines += [
        "",
        "미리 정한 2/3 문턱에서 후반 표시가 회복기의 50%, **후퇴기의 76%**를 덮는다.",
        "**국면의 4분의 3에 '후반'이라고 붙이는 신호는 후반부 신호가 아니다.** 이것은 신호가",
        "실패한 방식 중 하나이며, 문턱을 사후에 올려 감추지 않는다. 격차 변환이 붙은 확장기",
        "(34.6%)·침체기(12.3%)에서는 그 문제가 없다 — 조건 하나를 더한 것만으로 선택성이",
        "생겼다는 뜻이기도 하다.",
        "",
        "## 수준이 음인 확장기에서 신호가 얼어붙는다",
        "",
        "확장기 685주 중 **144주는 활동 수준이 음이다** — 앞 단계들에서 확인된 '정상 이하",
        "확장'이다. 그 144주에서 후반 표시가 켜진 것은 **2주**뿐이다.",
        "",
        "`level_still_positive` 조건이 구조적으로 켜질 수 없기 때문이다. 그래서 성숙도가",
        "0.25~0.33에 묶이고, 서술은 '확장기 초반'이 된다.",
        "",
        "**그러나 그것은 초반이 아니라 약한 확장이다.** 조건 하나가 '아직 이르다'와 '원래",
        "약하다'를 구분하지 못하고 앞의 것으로 읽어 버린다. 2002·2004·2007·2010년과",
        "2025~2026년이 모두 여기에 해당하며, **현재 주도 그렇다**.",
        "",
        "이것은 문턱 문제가 아니라 조건 정의 문제다. 국면 내 상대 수준(같은 확장기 안에서의",
        "분위)으로 바꾸면 사라질 수 있지만, 그것은 이 단계에서 미리 정한 정의가 아니므로",
        "사후에 바꾸지 않고 결함으로 적어 둔다.",
        "",
        "## 실시간 창 — 따로 둔다",
        "",
        "긴 역사는 **유효성**을 묻고 실시간 창은 **사용 가능성**을 묻는다. 섞지 않는다.",
        "",
    ]
    lines += phase_table(payload["real_time"])
    lines += [
        "",
        "실시간 창(2013~2026, 688주)에는 침체 에피소드가 코로나 하나뿐이다. 침체기 후반을",
        "에피소드 하나로 검증하는 것은 검증이 아니다.",
        "",
        "그리고 기본 적중률 자체가 이상하다 — 확장기 0.82, 침체기 0.72다. 확장↔후퇴 왕복이",
        "잦아서 '13주 안에 후퇴기가 온다'가 거의 항상 참이기 때문이다. 기본이 0.82인 곳에서는",
        "후반 신호가 더할 것이 거의 없다. 채터링이 예고 문제를 **쉽게 만드는 대신 무의미하게**",
        "만든다.",
        "",
        "격차 변환은 실시간 창에 적용하지 않았다. 가동률과 자연실업률의 시점 재구성이",
        "없어서다 — 특히 자연실업률(NROU)은 CBO 추정치라 수정이 잦고, 최종 수정치를 과거",
        "시점에 얹으면 그때 알 수 없던 값을 쓰는 것이 된다. **격차 변환의 실시간 거동은",
        "검증되지 않았다.**",
        "",
        "## 2차 읽기 창 민감도",
        "",
        "미리 정한 창은 8주다. 아래 표에서 좋은 창을 고르지 않는다.",
        "",
        "| 창 | 국면 | 후반 주 | 기본 | 경과기간만 | 후반신호 | 이기는가 |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in payload["change_window_sensitivity"]:
        mark = " ←미리 정함" if entry["predeclared"] else ""
        lines.append(
            f"| {entry['change_weeks']}주{mark} | {PHASE_LABEL[entry['phase']]} | "
            f"{entry['late_weeks']} | {entry['successor_rate_base']} | "
            f"{entry['successor_rate_duration_only']} | "
            f"{entry['successor_rate_late_signal']} | "
            f"{'예' if entry['beats_duration'] else '아니오'} |"
        )
    lines += [
        "",
        "## 출력 문구 초안 — 상태 서술형",
        "",
        "| 국면 | 단계 | 문구 |",
        "|---|---|---|",
    ]
    for entry in payload["wording_drafts"]:
        lines.append(f"| {PHASE_LABEL[entry['phase']]} | {entry['stage']} | {entry['wording']} |")
    current = payload["current"]
    lines += [
        "",
        "어느 문구도 다음 국면을 이름으로 부르지 않는다. '곧 후퇴기가 옵니다'는 예측이고,",
        "'확장기 후반의 특징이 나타납니다'는 현재 상태의 서술이다. 정보는 같지만 뒤의 것만",
        "이 프로젝트의 원칙과 규제 경계에 맞는다.",
        "",
        "### 현재 주",
        "",
        f"{current['week']} · {PHASE_LABEL.get(str(current['phase']), current['phase'])} · "
        f"성숙도 {current['maturity']}",
        "",
        f"> {current['wording']}",
        "",
        "## 한계",
        "",
        "- 긴 역사에서도 침체 5건, 회복 4건뿐이다. Track 18이 요구한 10개 이상의 완결된",
        "  순환 블록은 이 자료로 만들 수 없다 — 소비 계열(RRSFS)이 1992년부터라 1994년보다",
        "  앞으로 갈 수 없다.",
        "- 확장기·후퇴기는 블록이 45건·44건으로 많지만, 그 둘 사이의 왕복이라 서로 다른",
        "  순환의 반복이 아니다.",
        "- 격차 변환은 최종 수정치로만 평가했다. 실시간 거동은 검증되지 않았다.",
        "- 자연실업률은 추정치이고 자주 수정된다. 격차의 부호가 수정으로 뒤집힐 수 있다.",
        "- 이 단계는 국면 판정을 바꾸지 않는다. 서술을 덧붙일 뿐이다.",
        "- 이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
        "## 국면-수익률 검증과의 관계",
        "",
        "앞 단계에서 국면 분류가 산업 상대수익률을 유의하게 가르지 못한다는 결과가 나왔다.",
        "성숙도 신호가 옳게 켜지더라도 그것이 수익률로 이어진다는 근거는 현재 없다.",
        "이 단계의 결과는 **서술의 정확도**에 관한 것이며, 그 이상으로 읽으면 안 된다.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    config = load_config(settings)
    root = settings.root

    raw = S.load_path(str(root / REVISED_PATH))
    frame = S.derive(raw)
    weeks = [str(week) for week in frame.index]

    gap_frame = G.build(weeks, str(root / G.CACHE_DIR), str(root / CLAIMS_PATH))

    existing = _run(frame, None)
    augmented = _run(frame, gap_frame)

    real_time_raw = S.load_path(str(root / REAL_TIME_PATH))
    real_time = S.derive(real_time_raw)
    real_time_result = _run(real_time, None)

    scored = SG.score(frame, gap_frame)

    payload: dict[str, Any] = {
        "stage": "phase_maturity",
        "frozen_model_modified": False,
        "frozen_config_sha256": config.sha256,
        "executed_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "change_weeks": S.CHANGE_WEEKS,
        "late_threshold": SG.LATE_THRESHOLD,
        "horizon_weeks": V.HORIZON_WEEKS,
        "inputs": {phase: list(names) for phase, names in SG.INPUTS.items()},
        "gap_inputs": {phase: list(names) for phase, names in SG.GAP_INPUTS.items()},
        "gap_coverage": G.coverage(gap_frame),
        "cycle_order": V.cycle_order_holds(frame),
        "existing_values_only": existing,
        "with_gap_transforms": augmented,
        "real_time": real_time_result,
        "change_window_sensitivity": _sensitivity(str(root / REVISED_PATH), gap_frame),
        "wording_drafts": _wording_drafts(),
        "current": _current(frame, scored),
        "verdict": _verdict(existing, augmented),
    }

    output = root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(augmented["by_phase"]).to_csv(output / "phase_results.csv", index=False)
    pd.DataFrame(augmented["threshold_sweep"]).to_csv(output / "threshold_sweep.csv", index=False)
    pd.DataFrame(payload["change_window_sensitivity"]).to_csv(
        output / "change_window_sensitivity.csv", index=False
    )
    scored.join(frame[["level", "momentum", "breadth", "concentration"]]).join(gap_frame).to_csv(
        output / "weekly_maturity.csv"
    )
    (output / "phase_maturity_report.md").write_text(
        _report(payload), encoding="utf-8", newline="\n"
    )
    (output / "validation_summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    print(json.dumps(payload["verdict"]["statement"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
