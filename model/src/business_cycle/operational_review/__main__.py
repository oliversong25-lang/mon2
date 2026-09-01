"""운영 수용 심사 실행기.

    python -m business_cycle.operational_review

산출물은 새 격리 경로 ``outputs/operational_review/``에만 쓴다. 이전 단계의 산출물을
덮어쓰지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

import pandas as pd

from ..config import load_settings
from . import revision
from .review import (
    CLASSIFICATIONS,
    FORBIDDEN_CLASSIFICATION,
    OUTPUT_NAME,
    latest_vintage_path,
    load_alfred_path,
    run,
    write_json,
)

COMPARISON = ("2019-07-01", "2020-06-30")


def _flatten_gates(gates: dict[str, dict[str, Any]]) -> pd.DataFrame:
    rows = [
        {
            "group": group,
            "gate": name,
            "value": json.dumps(detail.get("value"), ensure_ascii=False, default=str),
            "limit": json.dumps(detail.get("limit"), ensure_ascii=False, default=str),
            "passes": detail["passes"],
            "reported_only": detail.get("reported_only", False),
        }
        for group, entries in gates.items()
        for name, detail in entries.items()
    ]
    return pd.DataFrame(rows)


def _report(payload: dict[str, Any]) -> str:
    decision = payload["decision"]
    benchmark = payload["benchmark"]
    lines = [
        "# 4국면 v1.1 운영 수용 심사",
        "",
        "동결된 v1.1을 **읽기만** 한 별도 사전 등록 단계다. 기각된 채택 규약의 연장이",
        "아니고, 모수 탐색이 아니며, v1.2를 만들 권한도 아니다.",
        "",
        f"**분류: `{decision['classification']}`**",
        "",
        f"사유: {decision['reason']}",
        "",
        "v1.1의 원래 상태는 `rejected`이며 이 단계가 그것을 바꾸지 않는다.",
        f"`{FORBIDDEN_CLASSIFICATION}`는 이 단계가 낼 수 있는 분류가 아니다.",
        "",
        "## 증거 위계",
        "",
        "| 층 | 무엇을 재는가 |",
        "|---|---|",
        "| 엄격 실시간 ALFRED | 운영 거동 |",
        "| 최신 수정치 인과 | 개정 민감도·역사 재구성 |",
        "| NBER 날짜 | 회고적 라벨. 그 시점 정보가 아니다 |",
        "",
        "2020년 ALFRED 결과는 이미 들여다봤으므로 손대지 않은 홀드아웃이 **아니다**.",
        "",
        "## 회복 인식 비교 기준",
        "",
        f"개발구간 에피소드 **{benchmark['development_sample_size']}개뿐**이다"
        f"({', '.join(benchmark['development_episodes'])}). 분위수를 통계적으로 안정된",
        "값으로 제시하지 않는다. 최악값을 서술적 상한으로만 쓴다.",
        "",
        f"- 개발 최악 첫 공식 회복 지연: "
        f"{benchmark['worst_development_first_recovery_lag_weeks']}주",
        f"- 개발 최악 저점 이후 침체 연속: "
        f"{benchmark['worst_development_post_trough_contraction_run']}주",
        f"- 2020 실시간 첫 공식 회복 지연: {benchmark['measured_first_recovery_lag_weeks']}주 "
        f"({'통과' if benchmark['first_recovery_lag_passes'] else '실패'})",
        f"- 2020 실시간 저점 이후 침체 연속: "
        f"{benchmark['measured_post_trough_contraction_run']}주 "
        f"({'통과' if benchmark['post_trough_contraction_run_passes'] else '실패'})",
        "",
        "## 게이트",
        "",
        "| 묶음 | 게이트 | 결과 |",
        "|---|---|---|",
    ]
    for group, entries in payload["gates"].items():
        for name, detail in entries.items():
            mark = "통과" if detail["passes"] else "**실패**"
            if detail.get("reported_only"):
                mark = "보고만"
            lines.append(f"| {group} | {name} | {mark} |")
    lines += [
        "",
        "## 남은 한계",
        "",
        "- 엄격 실시간 침체 에피소드가 **하나뿐**이다. 실시간 침체 성능을 일반화할 수 없다.",
        "- 개발 비교 기준이 에피소드 2개에서 나왔고 둘의 회복 지연이 0주와 31주로 크게 벌어진다.",
        "- 개발 기준은 최신 수정치에서, 2020년은 실시간에서 쟀다. 실시간이 구조적으로 느리다.",
        "- 후반 2019 최신 수정치 침체는 개정 민감도 실패로 그대로 남는다.",
        "",
        "이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    settings = load_settings()
    payload = run(settings)
    output = settings.root / "outputs" / OUTPUT_NAME
    output.mkdir(parents=True, exist_ok=True)

    realtime = load_alfred_path(settings)
    latest = latest_vintage_path(settings)
    aligned = revision.align(realtime, latest)
    window = aligned[(aligned["as_of"] >= COMPARISON[0]) & (aligned["as_of"] <= COMPARISON[1])]
    window.to_csv(output / "revision_disagreement_audit.csv", index=False)
    payload["revision_risk"] = revision.summarise(window, latest, COMPARISON)

    episodes = [
        *payload["development_episodes"],
        payload["recession_2020_real_time"],
        payload["recession_2020_latest_vintage_reference"],
    ]
    pd.DataFrame(episodes).to_csv(output / "recovery_timing_audit.csv", index=False)
    pd.DataFrame(payload["delay_decomposition"]).to_csv(
        output / "recovery_delay_decomposition.csv", index=False
    )
    _flatten_gates(payload["gates"]).to_csv(output / "realtime_operational_gates.csv", index=False)

    write_json(output / "provenance.json", payload["provenance"])
    write_json(
        output / "operational_decision.json",
        {
            "classification": payload["decision"]["classification"],
            "allowed_classifications": list(CLASSIFICATIONS),
            "forbidden_classification": FORBIDDEN_CLASSIFICATION,
            "reason": payload["decision"]["reason"],
            "failed_gates": payload["decision"]["failed_gates"],
            "v1_1_status": "rejected",
            "model_status": "rejected",
            "source_commit": payload["provenance"]["expected_source_commit"],
            "executed_at_utc": payload["provenance"]["executed_at_utc"],
            "hashes": payload["provenance"]["hashes"],
        },
    )
    write_json(output / "validation_summary.json", payload)
    (output / "operational_review_report.md").write_text(
        _report(payload), encoding="utf-8", newline="\n"
    )

    print(json.dumps(payload["decision"], ensure_ascii=False, indent=2))
    print(f"산출물: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
