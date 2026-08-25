"""보고서와 산출물 페이로드. 계산은 하지 않고 이미 잰 것을 엮는다."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from ..config import Settings
from . import maturity as MT
from . import metrics as M
from . import natural as N
from . import scoring as SC
from . import select as SEL
from . import variants as V
from .__main__ import (
    CANDIDATES,
    FRENCH_CACHE,
    FROZEN_ALFRED,
    FROZEN_PATH,
    PHASE_LABEL,
    RECOMMENDED,
    _decisive_rows,
    _evaluate,
    _matrix_rows,
    _row,
)

REALTIME_DIR = "outputs/slowdown_boundary/realtime"


def _load_realtime(root: Path) -> dict[str, pd.DataFrame]:
    out: dict[str, pd.DataFrame] = {}
    for key in ("baseline", "gate_only", "boundary_only", "boundary_and_gate"):
        path = root / REALTIME_DIR / f"{key}.csv"
        if not path.exists():
            return {}
        frame = pd.read_csv(path, index_col=0)
        frame.index = pd.Index([str(week) for week in frame.index], name="week")
        frame["official_phase"] = frame["official_phase"].fillna("").astype(str)
        out[key] = frame
    return out


def build_payload(settings: Settings) -> dict[str, Any]:
    root = settings.root
    prepared, config = V.build(settings)

    frozen = pd.read_csv(root / FROZEN_PATH, index_col=0)
    frozen.index = pd.Index([str(week) for week in frozen.index], name="week")
    reproduction = V.reproduces_v1_1(prepared, config, frozen["official_phase"])

    baseline_frame = V.path(prepared, config, V.Variant("baseline", SC.SlowdownGate(), False))
    baseline_phase = baseline_frame["official_phase"]
    panel = M.industry_panel(list(baseline_frame.index), str(root / FRENCH_CACHE))

    natural_rows = N.by_phase(frozen["official_phase"])
    natural_reading = N.read(natural_rows)

    sweep: list[dict[str, Any]] = []
    for gate in CANDIDATES:
        frame = V.path(prepared, config, V.Variant("candidate", gate, False))
        sweep.append(_evaluate(frame, panel, baseline_phase, gate.name))
    ranked = SEL.rank(sweep)

    cells: dict[str, dict[str, Any]] = {}
    frames: dict[str, pd.DataFrame] = {}
    for variant in V.matrix(RECOMMENDED):
        frame = V.path(prepared, config, variant)
        frames[variant.key] = frame
        cells[variant.key] = _evaluate(frame, panel, baseline_phase, variant.label)

    realtime = _load_realtime(root)
    realtime_cells: dict[str, dict[str, Any]] = {}
    if realtime:
        realtime_baseline = realtime["baseline"]["official_phase"]
        frozen_alfred = pd.read_csv(root / FROZEN_ALFRED, index_col=0)
        frozen_alfred.index = pd.Index([str(week) for week in frozen_alfred.index], name="week")
        alfred_phase = frozen_alfred["official_phase"].fillna("").astype(str)
        agree = sum(
            1
            for week in realtime_baseline.index
            if str(realtime_baseline[week]) == str(alfred_phase.get(week, ""))
        )
        realtime_cells["_reproduction"] = {
            "weeks_compared": int(len(realtime_baseline)),
            "weeks_agreeing": agree,
            "reproduces": bool(agree == len(realtime_baseline)),
        }
        for key, frame in realtime.items():
            realtime_cells[key] = {
                "gate": key,
                "shape": M.shape(frame["official_phase"]),
                "progression": M.progression(frame["official_phase"]),
                "recognition": M.recognition(frame["official_phase"], realtime_baseline),
                "nber": M.nber(frame["official_phase"]),
                "breadth_gate_holds": M.breadth_gate_holds(frame),
                "current_call": str(frame["official_phase"].iloc[-1]),
            }

    maturity = {
        "before_boundary": MT.compare(
            baseline_frame["activity_level"].astype(float), baseline_phase
        ),
        "after_boundary": MT.compare(
            frames["boundary_only"]["activity_level"].astype(float),
            frames["boundary_only"]["official_phase"],
        ),
        "relative_condition_was_tried_and_rejected": {
            "why": (
                "상대 수준 조건으로 갈아 끼우면 서술은 고쳐지지만 트랙 18의 검증이 "
                "무너진다 — 확장기 후반 적중률이 0.641에서 0.581로 내려가 경과 기간 "
                "대조군(0.610)에 진다. `level > 0`이 실제로 예측 일을 하고 있었다."
            ),
            "chosen_instead": (
                "점수는 그대로 두고 서술 층에서 '정상 이하 확장'을 따로 표시한다. "
                "결함이 애초에 서술 문제였으므로 그 층에서 고치는 것이 정확하다."
            ),
        },
        "current_week_reading": MT.stage_with_strength(
            0.25, float(baseline_frame["activity_level"].iloc[-1]), 2.0 / 3.0
        ),
    }

    verdict = {
        "reproduces_v1_1": reproduction["reproduces"],
        "slowdown_became_a_real_state": bool(
            (cells["boundary_and_gate"]["discrimination"]["slowdown"]["ratio_to_chance"] or 0)
            >= SEL.DISCRIMINATION_TARGET
        ),
        "gate_alone_moves_the_long_path": bool(
            cells["gate_only"]["shape"]["transitions"]
            < cells["baseline"]["shape"]["transitions"] - 5
        ),
        "recommended": RECOMMENDED.name,
        "rule_mechanical_winner": ranked[0]["gate"] if ranked else None,
        "gate_is_redundant_after_the_boundary_fix": bool(
            (cells["boundary_and_gate"]["discrimination"]["slowdown"]["ratio_to_chance"] or 0)
            <= (cells["boundary_only"]["discrimination"]["slowdown"]["ratio_to_chance"] or 0)
        ),
        "statement": (
            f"후퇴기 경계가 결함이었다. {RECOMMENDED.name} 요건을 걸면 후퇴기 판별력이 "
            f"{cells['baseline']['discrimination']['slowdown']['ratio_to_chance']}배에서 "
            f"{cells['boundary_only']['discrimination']['slowdown']['ratio_to_chance']}배로 "
            f"오르고 비중이 "
            f"{cells['baseline']['shape']['phase_shares']['slowdown']:.1%}에서 "
            f"{cells['boundary_only']['shape']['phase_shares']['slowdown']:.1%}로 내려가며, "
            "침체·회복 인식은 한 주도 늦어지지 않는다. 전이 게이트는 경계를 고친 뒤에는 "
            "더할 것이 없다."
        ),
    }

    return {
        "stage": "slowdown_boundary",
        "frozen_model_modified": False,
        "frozen_config_sha256": config.sha256,
        "reproduction": reproduction,
        "natural_experiment": {"by_phase": natural_rows, "reading": natural_reading},
        "selection_rule": SEL.rule(),
        "sweep": sweep,
        "ranked": [row["gate"] for row in ranked],
        "matrix": cells,
        "realtime": realtime_cells,
        "maturity": maturity,
        "verdict": verdict,
    }


def build_report(payload: dict[str, Any]) -> str:
    natural = payload["natural_experiment"]
    rule = payload["selection_rule"]
    cells = payload["matrix"]
    verdict = payload["verdict"]
    maturity = payload["maturity"]

    only_slowdown = cells["boundary_only"]["discrimination"]["slowdown"]

    lines = [
        "# 후퇴기 경계 — 애매한 주를 전부 흡수하던 라벨",
        "",
        "## 결론",
        "",
        f"**{verdict['statement']}**",
        "",
        f"동결 v1.1 재현: **{payload['reproduction']['weeks_agreeing']}/"
        f"{payload['reproduction']['weeks_compared']}주 일치**. `four_phase` 아래 파일은 "
        "하나도 건드리지 않았고, `prepare`와 `decide`는 동결 코드를 그대로 쓴다.",
        "",
        "## 게이트 종류를 고르기 전에 — 자연 실험",
        "",
        "모델은 이미 세 처방을 한 몸에 갖고 있다. 무엇을 후퇴기에 붙일지 정하기 전에 "
        "셋이 각각 무엇을 샀는지 잰다.",
        "",
        "| 국면 | 게이트 | 에피소드 | 중앙 지속 | 4주 미만 | 되돌림 |",
        "|---|---|---|---|---|---|",
    ]
    for row in natural["by_phase"]:
        lines.append(
            f"| {PHASE_LABEL[row['phase']]} | {row['gate']} | {row['episodes']} | "
            f"{row['median_episode_weeks']}주 | {row['short_rate']} | "
            f"**{row['reversion_rate']}** |"
        )
    lines += [
        "",
        f"{natural['reading']['why_not_short_rate']}",
        "",
        f"{natural['reading']['reading']}",
        "",
        f"{natural['reading']['what_this_does_not_say']}",
        "",
        "## 선택 규칙 — 스윕 전에 적었다",
        "",
        f"- 1순위: **{rule['primary_metric']}**",
        f"- 왜: {rule['why_primary']}",
        f"- 동률: {rule['tie_break']}",
        f"- 목표: {rule['target']}",
        "",
        "깨지면 안 되는 것:",
        "",
    ]
    for item in rule["must_not_break"]:
        lines.append(f"- {item}")

    lines += [
        "",
        "## 경계 후보 스윕",
        "",
        "| 게이트 | 전이 | 4주 미만 | 판별력 | 비중 | 진행 | 침체 | 회복 | 오탐 |",
        "|---|---|---|---|---|---|---|---|---|",
        _row(cells["baseline"]).replace(cells["baseline"]["gate"], "v1.1 기준선"),
    ]
    for entry in payload["sweep"]:
        lines.append(_row(entry))
    lines += [
        "",
        f"규칙의 기계적 1위는 **`{verdict['rule_mechanical_winner']}`**다.",
        "",
        "## 2x2 — 어느 축이 일을 했는가",
        "",
    ]
    lines += _matrix_rows(cells)
    lines += ["", "결정적 지표로 다시 보면:", ""]
    lines += _decisive_rows(cells)
    lines += [
        "",
        "**게이트만 걸면 장기 경로에서 거의 아무 일도 일어나지 않는다** — 전이 97에서 96, "
        "후퇴기 판별력은 0.369에서 0.338로 오히려 내려간다. 트랙 16이 게이트를 검증한 곳은 "
        "688주 실시간 경로였고, 거기서는 전이를 72에서 63으로 줄였다. 1675주 최신 빈티지 "
        "경로에서는 그 효과가 거의 없다.",
        "",
        "**일을 한 것은 경계다.** 그리고 경계를 고치고 나면 게이트가 더할 것이 없어진다 — "
        "아래 '게이트는 켜지 않는다'에서 수치로 본다.",
        "",
        "## 무엇이 실제로 움직였는가",
        "",
        "경계 수정이 옮긴 주는 578주이고, 그중 **567주가 후퇴기에서 확장기로** 간다. "
        "확장기 685주는 **한 주도 움직이지 않는다.** 진단한 기전이 그대로 확인된다 — "
        "후퇴기가 확장기의 애매한 주를 흡수하고 있었다.",
        "",
        "침체기는 162주에서 164주로 거의 그대로고(161주 유지 + 후퇴기에서 3주), 폭 "
        "게이트는 계속 성립하며 인식은 한 주도 늦어지지 않는다. 회복기는 151주에서 "
        "144주로 조금 준다.",
        "",
        "### 깎은 몫을 어디로 보내는가 — 처음에 틀렸던 곳",
        "",
        "처음에는 회복기와 똑같이 나머지 셋에 비례 배분했다. 그랬더니 실시간 경로에서 "
        "**2020년 1~3월에 회복기가 9주 연속으로 켜졌다** — 코로나 폭락이 시작되던 "
        "구간이다.",
        "",
        "원인은 순서였다. 회복 감쇠가 이 단계보다 **먼저** 끝나므로, 여기서 회복에 넘긴 "
        "몫은 회복 자신의 폭·지속 게이트를 통과하지 않고 들어간다. 침체도 마찬가지다.",
        "",
        "그래서 깎은 몫을 **확장기로만** 보낸다. 임시방편이 아니라 진단과 같은 말이다 — "
        "후퇴기가 흡수하던 것은 확장기의 애매한 주였고, 확장기는 자기 게이트가 없어 "
        "우회할 게이트도 없다.",
        "",
        "장기 경로 지표는 이 결함이 있을 때도 전부 정상으로 보였다(지연 0, 오탐 5). "
        "실시간 경로에서만 드러났다.",
        "",
        "## 권고와 규칙",
        "",
        f"규칙의 기계적 1위와 권고가 **일치한다** — 둘 다 `{verdict['recommended']}`다.",
        "",
        "재분배 결함이 있던 판에서는 둘이 갈렸다. 그때 1위는 `breadth3`였는데 그것은 "
        "진행률을 기준선 아래로 떨어뜨렸고(0.091 → 0.083), 내 규칙이 진행률을 동률 "
        "판정용으로만 둬서 그 실패를 값매기지 못했다. 결함을 고치자 그 어긋남이 "
        "사라졌다. **규칙을 결과에 맞춰 바꾸지 않았고, 바꿀 필요도 없어졌다.**",
        "",
        f"`{verdict['recommended']}`는 결정적 지표 셋을 모두 개선한다 — 판별력 "
        f"{cells['baseline']['discrimination']['slowdown']['ratio_to_chance']}→"
        f"{cells['boundary_only']['discrimination']['slowdown']['ratio_to_chance']}, "
        f"비중 {cells['baseline']['shape']['phase_shares']['slowdown']:.1%}→"
        f"{cells['boundary_only']['shape']['phase_shares']['slowdown']:.1%}, "
        f"진행률 {cells['baseline']['progression']['progression_rate']}→"
        f"{cells['boundary_only']['progression']['progression_rate']}. "
        "그리고 자연 실험이 스윕 전에 가리킨 '지속이 1순위'와 같은 모양이다.",
        "",
        "### 게이트는 켜지 않는다",
        "",
        "경계를 고친 뒤 게이트를 더 걸면 판별력이 "
        f"{cells['boundary_only']['discrimination']['slowdown']['ratio_to_chance']}에서 "
        f"{cells['boundary_and_gate']['discrimination']['slowdown']['ratio_to_chance']}로, "
        f"진행률이 {cells['boundary_only']['progression']['progression_rate']}에서 "
        f"{cells['boundary_and_gate']['progression']['progression_rate']}로 **내려간다.** "
        "전이는 38에서 37로 하나 줄 뿐이다.",
        "",
        "**근본 원인을 고치니 후처리가 필요 없어졌다.** 2x2를 돌리지 않고 둘을 한꺼번에 "
        "걸었다면 이것이 보이지 않았을 것이다.",
        "",
        f"다만 **판별력 {only_slowdown['ratio_to_chance']}의 "
        f"p는 {only_slowdown['p_value']}다.** 비율이 1을 "
        "넘었지만 우연과 통계적으로 구분되지는 않는다. 후퇴기 블록이 12건뿐이라 그 이상 "
        "말할 수 없다.",
        "",
        "## 실시간(ALFRED) 경로",
        "",
    ]
    realtime = payload["realtime"]
    if realtime:
        reproduction = realtime["_reproduction"]
        lines += [
            "기준선이 동결 ALFRED 경로를 재현한다: "
            f"**{reproduction['weeks_agreeing']}/{reproduction['weeks_compared']}주**.",
            "",
            "| 칸 | 전이 | 4주 미만 | 후퇴기 주 | 비중 | 진행 | 침체 | 회복 | 오탐 | 현재 |",
            "|---|---|---|---|---|---|---|---|---|---|",
        ]
        korean = {
            "baseline": "v1.1 기준선",
            "gate_only": "게이트만",
            "boundary_only": "경계만",
            "boundary_and_gate": "경계+게이트",
        }
        for key, label in korean.items():
            entry = realtime[key]
            shape = entry["shape"]
            progression = entry["progression"]
            lines.append(
                f"| {label} | {shape['transitions']} | "
                f"{shape['phases_shorter_than_four_weeks']} | "
                f"{shape['phase_weeks']['slowdown']} | "
                f"{shape['phase_shares']['slowdown']:.1%} | "
                f"{progression['progressed_to_contraction']}/"
                f"{progression['closed_slowdown_blocks']} | "
                f"{entry['recognition']['contraction']['max_delay_weeks']}주 | "
                f"{entry['recognition']['recovery']['max_delay_weeks']}주 | "
                f"{entry['nber']['false_positive_episodes']} | "
                f"**{entry['current_call'] or 'withheld'}** |"
            )
        current = realtime["boundary_and_gate"]["current_call"] or "withheld"
        lines += [
            "",
            "실시간 창에서 후퇴기 비중은 49%였다. 경계를 고치면 위 표대로 내려간다.",
            "",
            "### 현재 판정 (2026-08-14)",
            "",
            f"권고 설정에서 **`{current}`**이고, v1.1 기준선은 "
            f"`{realtime['baseline']['current_call'] or 'withheld'}`다.",
            "",
        ]
    else:
        lines += ["실시간 산출물이 아직 없어 이 절은 비어 있다.", ""]

    before = maturity["before_boundary"]
    after = maturity["after_boundary"]
    rejected = maturity["relative_condition_was_tried_and_rejected"]
    reading = maturity["current_week_reading"]
    lines += [
        "## 성숙도 결함 — 약한 확장이 초반으로 읽힌다",
        "",
        "경계 수정이 이 결함을 **키운다.** 후퇴기로 흡수되던 애매한 주가 확장기로 "
        "돌아오면서 수준이 음인 확장기 주가 늘기 때문이다.",
        "",
        "| | 확장기 주 | 수준이 음인 주 |",
        "|---|---|---|",
        f"| 경계 수정 전 | {before['expansion_weeks']} | "
        f"**{before['negative_level_expansion_weeks']}** |",
        f"| 경계 수정 후 | {after['expansion_weeks']} | "
        f"**{after['negative_level_expansion_weeks']}** |",
        "",
        "### 상대 수준 조건을 먼저 시험했고, 버렸다",
        "",
        f"{rejected['why']}",
        "",
        f"{rejected['chosen_instead']}",
        "",
        "예측력을 깎아 서술을 고치는 것은 교환이 나쁘다. 결함이 애초에 '약한 확장이 "
        "초반으로 읽힌다'는 **서술** 문제였으므로, 서술 층에서 둘을 갈라 주면 정확히 "
        "해결된다.",
        "",
        "### 현재 주가 그 경우다",
        "",
        f"단계 {reading.get('stage')} · 세기 **{reading.get('strength')}** · "
        "초반으로 오해될 상태인가: "
        f"{'예' if reading.get('reads_as_early_but_is_weak') else '아니오'}",
        "",
        f"> {reading.get('wording_prefix', '')}확장기 초반의 특징이 나타납니다.",
        "",
        "## 한계",
        "",
        "- 후퇴기 판별력 2.421의 p는 0.121이다. 1을 넘었지만 우연과 구분되지 않는다.",
        "  후퇴기 블록이 12건뿐이라 그 이상 말할 수 없다.",
        "- 지속 13주는 스윕에서 고른 값이다. 자연 실험이 '지속이 1순위'라는 모양을",
        "  가리켰지만 숫자까지 가리키지는 않았다.",
        "- 확장기 비중이 41%에서 75%로 오른다. 후퇴기가 흡수하던 주가 확장기로 돌아온",
        "  결과이며, 그 자체가 옳은지는 이 단계가 검정하지 않는다.",
        "- 트랙 17과 트랙 19의 검정 B는 이 라벨을 썼으므로 다시 돌려야 한다. 트랙 19의",
        "  검정 A와 트랙 20은 시장에 관한 것이라 그대로 선다.",
        "- 이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
    return "\n".join(lines)
