"""운영 수용 심사 실행기. 동결 v1.1을 읽기만 한다.

증거 위계를 코드에 박아 둔다(§3).

1. 엄격 실시간 ALFRED가 운영 거동을 잰다.
2. 최신 수정치 인과 경로는 개정 민감도와 역사 재구성 거동을 잰다.
3. NBER 날짜는 회고적 라벨이며 그 시점 정보가 아니다.
4. 2020년 ALFRED 결과는 **이미 들여다봤으므로** 손대지 않은 홀드아웃이 아니다.
5. 13주 전방 모니터링은 파이프라인 안정성만 검증할 수 있고, 침체가 실제로 오지
   않는 한 침체 탐지 정확도를 검증하지 못한다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import numpy as np
import pandas as pd

from ..config import Settings, load_baseline, load_settings
from ..four_phase import alfred as AL
from ..four_phase import contract as C
from ..four_phase.engine import load_config, prepare, score
from ..validation.phase4 import END, load_core_observations
from ..validation.real_data import _official_recession_flags
from . import preserve
from .recovery import DEVELOPMENT_EPISODES, PEAKS, TROUGHS, decompose_delay, episode_audit

OUTPUT_NAME = "operational_review"
AS_OF = pd.Timestamp(END)
ALFRED_PATH = Path("outputs/four_phase_v1_1/alfred_audit/weekly_path.csv")

#: NBER 2020 침체 시작. 회고적 라벨이다.
NBER_2020_START: Final[pd.Timestamp] = pd.Timestamp("2020-02-07")
NBER_2020_END: Final[pd.Timestamp] = pd.Timestamp("2020-04-30")

#: 이 단계가 낼 수 있는 분류. `final_validated`는 목록에 없다.
CLASSIFICATIONS: Final[tuple[str, ...]] = (
    "provisional_operational_adoption",
    "operational_rejection",
    "insufficient_evidence",
)

FORBIDDEN_CLASSIFICATION: Final[str] = "final_validated"


def transition_watch(scores: dict[str, float], phase: str) -> str:
    """`report.current_output`과 같은 규칙. 재현일 뿐 새 로직이 아니다."""

    order = {name: index for index, name in enumerate(C.PHASES)}
    if phase not in order:
        return "none"
    forward = C.PHASES[(order[phase] + 1) % len(C.PHASES)]
    backward = C.PHASES[(order[phase] - 1) % len(C.PHASES)]
    adjacent = {name: scores[name] for name in (forward, backward)}
    leader = max(adjacent, key=lambda name: adjacent[name])
    return f"toward_{leader}" if adjacent[leader] > scores[phase] * 0.6 else "none"


def load_alfred_path(settings: Settings) -> pd.DataFrame:
    """이전 단계가 만든 실시간 경로. 이 단계는 다시 계산하지 않고 읽는다."""

    path = pd.read_csv(settings.root / ALFRED_PATH, parse_dates=["as_of"]).set_index("as_of")
    path["official_phase"] = path["official_phase"].fillna("").astype(str)
    watches: list[str] = []
    for moment in path.index:
        scores = {name: float(str(path.at[moment, f"filtered_{name}"])) for name in C.PHASES}
        watches.append(transition_watch(scores, str(path.at[moment, "official_phase"])))
    path["transition_watch"] = watches
    return path


def latest_vintage_path(settings: Settings) -> pd.DataFrame:
    """비교용 최신 수정치 경로. 같은 동결 설정으로 만든다."""

    config = load_config(settings)
    core, source = load_core_observations(settings)
    prepared = prepare(core, load_baseline("candidate_h_breadth_gate", settings), AS_OF, config)
    run = score(prepared, config)
    recession = _official_recession_flags(source, prepared.index)
    frame = pd.DataFrame(
        {
            "raw_phase": run.raw_phase,
            "filtered_winner": run.filtered_winner,
            "official_phase": run.official_phase,
            "phase_status": "official",
            "activity_level": run.activity_level,
            "activity_momentum": run.activity_momentum,
            "negative_level_domains": run.negative_level_domains,
            "negative_momentum_domains": run.negative_momentum_domains,
            "positive_momentum_domains": run.positive_momentum_domains,
            "confirming_domains": run.confirming_domains,
            "concentration": run.concentration,
            "recession_alert": run.alert_level,
            "recession_alert_character": run.alert_character,
            "evidence_quality_high": run.evidence_quality_high,
            "confirmation_pending": run.confirmation_pending,
            "usrec": recession.astype(int),
        }
    )
    ordered = np.sort(run.filtered_scores[list(C.PHASES)].to_numpy(dtype=float), axis=1)
    frame["phase_separation"] = ordered[:, -1] - ordered[:, -2]
    for name in C.PHASES:
        frame[f"raw_{name}"] = run.raw_scores[name]
        frame[f"filtered_{name}"] = run.filtered_scores[name]
    watches: list[str] = []
    for moment in frame.index:
        scores = {name: float(str(frame.at[moment, f"filtered_{name}"])) for name in C.PHASES}
        watches.append(transition_watch(scores, str(frame.at[moment, "official_phase"])))
    frame["transition_watch"] = watches
    return frame


@dataclass(frozen=True)
class Benchmark:
    """§6에서 **결과를 보기 전에** 선언한 판정 규칙."""

    worst_first_recovery_lag_weeks: int | None
    worst_post_trough_contraction_run: int | None
    episodes: list[str]

    def evaluate(self, episode: dict[str, Any]) -> dict[str, Any]:
        lag = episode.get("official_first_recovery_lag_weeks")
        run = episode.get("longest_post_trough_contraction_run")
        lag_ok = (
            lag is not None
            and self.worst_first_recovery_lag_weeks is not None
            and lag <= self.worst_first_recovery_lag_weeks
        )
        run_ok = (
            run is not None
            and self.worst_post_trough_contraction_run is not None
            and run <= self.worst_post_trough_contraction_run
        )
        return {
            "development_episodes": self.episodes,
            "development_sample_size": len(self.episodes),
            "worst_development_first_recovery_lag_weeks": self.worst_first_recovery_lag_weeks,
            "worst_development_post_trough_contraction_run": (
                self.worst_post_trough_contraction_run
            ),
            "measured_first_recovery_lag_weeks": lag,
            "measured_post_trough_contraction_run": run,
            "first_recovery_lag_passes": lag_ok,
            "post_trough_contraction_run_passes": run_ok,
            "passes": bool(lag_ok and run_ok),
        }


def build_benchmark(latest: pd.DataFrame) -> tuple[Benchmark, list[dict[str, Any]]]:
    """개발구간(1995~2012) 에피소드만으로 비교 기준을 만든다. 2020년을 쓰지 않는다."""

    audits = [
        episode_audit(
            latest,
            pd.Timestamp(TROUGHS[name]),
            name,
            "development_latest_vintage",
            peak=pd.Timestamp(PEAKS[name]),
        )
        for name in DEVELOPMENT_EPISODES
    ]
    lags = [
        a["official_first_recovery_lag_weeks"]
        for a in audits
        if a.get("official_first_recovery_lag_weeks") is not None
    ]
    runs = [
        a["longest_post_trough_contraction_run"]
        for a in audits
        if a.get("longest_post_trough_contraction_run") is not None
    ]
    return (
        Benchmark(
            worst_first_recovery_lag_weeks=max(lags) if lags else None,
            worst_post_trough_contraction_run=max(runs) if runs else None,
            episodes=list(DEVELOPMENT_EPISODES),
        ),
        audits,
    )


def _first_run_start(mask: pd.Series, index: pd.DatetimeIndex, length: int) -> str | None:
    values = mask.to_numpy(dtype=bool)
    run = 0
    for position in range(len(values)):
        run = run + 1 if values[position] else 0
        if run >= length:
            return str(pd.Timestamp(index[position - length + 1]).date())
    return None


def contraction_entry_gates(path: pd.DataFrame, recession: pd.Series) -> dict[str, Any]:
    """§7의 침체 진입 게이트. 실시간 경로에서만 잰다."""

    index = pd.DatetimeIndex(path.index)
    official = path["official_phase"].astype(str)
    contraction = official.eq("contraction")
    truth = recession.reindex(index).fillna(False).astype(bool)
    late_2019 = (index >= pd.Timestamp("2019-01-01")) & (index < NBER_2020_START)
    confirmed_2019 = _first_run_start(contraction & pd.Series(late_2019, index=index), index, 4)
    after_start = index >= NBER_2020_START
    first_official = index[(contraction & pd.Series(after_start, index=index)).to_numpy(bool)]
    first_persistent = _first_run_start(contraction & pd.Series(after_start, index=index), index, 4)
    in_recession = truth & pd.Series(
        (index >= NBER_2020_START) & (index <= NBER_2020_END), index=index
    )
    return {
        "no_confirmed_2019_contraction_before_the_recession": {
            "value": confirmed_2019,
            "passes": confirmed_2019 is None,
        },
        "first_official_contraction_within_10_weeks": {
            "value": int((pd.Timestamp(first_official[0]) - NBER_2020_START).days // 7)
            if len(first_official)
            else None,
            "passes": bool(
                len(first_official)
                and (pd.Timestamp(first_official[0]) - NBER_2020_START).days // 7 <= 10
            ),
        },
        "first_persistent_four_week_sequence_within_10_weeks": {
            "value": None
            if first_persistent is None
            else int((pd.Timestamp(first_persistent) - NBER_2020_START).days // 7),
            "passes": bool(
                first_persistent is not None
                and (pd.Timestamp(first_persistent) - NBER_2020_START).days // 7 <= 10
            ),
        },
        "at_least_four_of_eight_recession_weeks_as_contraction": {
            "value": int((contraction & in_recession).sum()),
            "of": int(in_recession.sum()),
            "passes": int((contraction & in_recession).sum()) >= 4,
        },
        "no_official_contraction_below_two_confirming_domains": {
            "value": int((contraction & path["confirming_domains"].astype(int).lt(2)).sum()),
            "passes": int((contraction & path["confirming_domains"].astype(int).lt(2)).sum()) == 0,
        },
        "no_concentrated_signal_decided_the_official_phase": {
            "value": int(
                (
                    contraction
                    & path["recession_alert_character"].astype(str).eq("severe_but_concentrated")
                ).sum()
            ),
            "passes": int(
                (
                    contraction
                    & path["recession_alert_character"].astype(str).eq("severe_but_concentrated")
                ).sum()
            )
            == 0,
        },
    }


def integrity_gates(
    path: pd.DataFrame, audit: dict[str, Any], cache: dict[str, Any]
) -> dict[str, Any]:
    """§7의 운영 무결성 게이트."""

    official = path["official_phase"].astype(str)
    status = path["phase_status"].astype(str)
    withheld = status.eq("withheld")
    raw = path["raw_phase"].astype(str)
    disagreement = raw.ne(official) & ~withheld
    longest = streak = 0
    for value in disagreement:
        streak = streak + 1 if bool(value) else 0
        longest = max(longest, streak)
    return {
        "zero_future_information_violations": {
            "value": int(path["future_observations"].astype(int).sum()),
            "passes": int(path["future_observations"].astype(int).sum()) == 0,
        },
        "cache_only_no_network_no_key": {
            "value": {
                key: cache[key]
                for key in (
                    "network_used",
                    "api_key_used",
                    "latest_vintage_substitution",
                    "backward_fill_used",
                )
            },
            "passes": not any(
                cache[key]
                for key in (
                    "network_used",
                    "api_key_used",
                    "latest_vintage_substitution",
                    "backward_fill_used",
                )
            ),
        },
        "exactly_688_as_of_weeks": {
            "value": int(len(path)),
            "passes": int(len(path)) == 688,
        },
        "withheld_and_preliminary_weeks_reproduced": {
            "value": dict(audit["phase_eligibility"]),
            "passes": dict(audit["phase_eligibility"])["withheld_weeks"] == int(withheld.sum()),
        },
        "no_official_phase_on_a_withheld_week": {
            "value": int((withheld & official.ne("")).sum()),
            "passes": int((withheld & official.ne("")).sum()) == 0,
        },
        "raw_measurements_preserved_on_withheld_weeks": {
            "value": int((withheld & raw.isin(C.PHASES)).sum()),
            "passes": int((withheld & ~raw.isin(C.PHASES)).sum()) == 0,
        },
        "raw_versus_official_disagreement_within_structural_limit": {
            "value": longest,
            "limit": 26,
            "passes": longest <= 26,
        },
    }


def recovery_gates(
    twenty: dict[str, Any], benchmark: dict[str, Any], development: list[dict[str, Any]]
) -> dict[str, Any]:
    """§7의 회복·이탈 게이트."""

    premature = [
        episode["episode"]
        for episode in [*development, twenty]
        if episode.get("premature_four_week_recovery_inside_the_recession") is not None
    ]
    round_trips = twenty.get("return_to_contraction_within_13_weeks")
    return {
        "development_benchmark_first_recovery_lag": {
            "value": benchmark["measured_first_recovery_lag_weeks"],
            "limit": benchmark["worst_development_first_recovery_lag_weeks"],
            "passes": benchmark["first_recovery_lag_passes"],
        },
        "development_benchmark_post_trough_contraction_run": {
            "value": benchmark["measured_post_trough_contraction_run"],
            "limit": benchmark["worst_development_post_trough_contraction_run"],
            "passes": benchmark["post_trough_contraction_run_passes"],
        },
        "no_premature_four_week_recovery_inside_a_recession": {
            "value": premature,
            "passes": not premature,
        },
        "contraction_recovery_round_trips_within_13_weeks": {
            "value": round_trips,
            "reported_only": True,
            "passes": True,
        },
    }


def classify(gates: dict[str, dict[str, Any]], measurable: bool) -> dict[str, Any]:
    """§10의 결정 트리. 기계적으로 적용한다. 우회 경로가 없다."""

    failed = [
        f"{group}.{name}"
        for group, entries in gates.items()
        for name, detail in entries.items()
        if not detail["passes"]
    ]
    if not measurable:
        classification = "insufficient_evidence"
        reason = "필수 게이트 하나를 자료에서 잴 수 없다"
    elif failed:
        classification = "operational_rejection"
        reason = f"운영 게이트 실패: {', '.join(failed)}"
    else:
        classification = "provisional_operational_adoption"
        reason = "사전 등록된 실시간 운영 게이트를 모두 통과했다"
    if classification not in CLASSIFICATIONS:  # pragma: no cover - 방어
        raise ValueError(f"허용되지 않은 분류입니다: {classification}")
    return {
        "classification": classification,
        "reason": reason,
        "failed_gates": failed,
        "final_validated_is_not_available": True,
        "v1_1_original_status": "rejected",
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run(settings: Settings | None = None) -> dict[str, Any]:
    base = settings or load_settings()
    provenance = preserve.verify(base)
    provenance["executed_at_utc"] = datetime.now(UTC).isoformat(timespec="seconds")

    path = load_alfred_path(base)
    latest = latest_vintage_path(base)
    core, source = load_core_observations(base)
    recession = _official_recession_flags(source, pd.DatetimeIndex(path.index))
    audit = json.loads(
        (base.root / "outputs/four_phase_v1_1/alfred_audit/audit_summary.json").read_text(
            encoding="utf-8"
        )
    )
    cache = AL.cache_audit(base, AS_OF)

    # §6. 비교 기준을 **2020년을 보기 전에** 개발구간에서만 만든다.
    benchmark, development = build_benchmark(latest)
    twenty = episode_audit(
        path,
        pd.Timestamp(TROUGHS["recession_2020"]),
        "recession_2020",
        "strict_alfred_real_time",
        peak=pd.Timestamp(PEAKS["recession_2020"]),
    )
    twenty_latest = episode_audit(
        latest,
        pd.Timestamp(TROUGHS["recession_2020"]),
        "recession_2020_latest_vintage",
        "latest_vintage_reference",
        peak=pd.Timestamp(PEAKS["recession_2020"]),
    )
    benchmark_result = benchmark.evaluate(twenty)

    gates = {
        "contraction_entry": contraction_entry_gates(path, recession),
        "recovery_and_exit": recovery_gates(twenty, benchmark_result, development),
        "operational_integrity": integrity_gates(path, audit, cache),
    }
    decision = classify(gates, measurable=twenty.get("covered", False))

    # 저점 날짜 규약이 프로젝트가 쓰는 USREC 라벨과 실제로 일치하는지 확인한다.
    # 규약이 어긋나 있으면 "침체 안"과 "저점 이후"의 경계가 통째로 흔들린다.
    truth_all = _official_recession_flags(source, pd.DatetimeIndex(latest.index)).fillna(False)
    convention: dict[str, Any] = {}
    for name, trough_date in TROUGHS.items():
        trough = pd.Timestamp(trough_date)
        weeks = pd.DatetimeIndex(latest.index)
        inside = weeks[(weeks <= trough) & (weeks > trough - pd.Timedelta(weeks=1))]
        after = weeks[(weeks > trough) & (weeks <= trough + pd.Timedelta(weeks=1))]
        convention[name] = {
            "trough": trough_date,
            "usrec_at_trough_week": int(truth_all.loc[inside[-1]]) if len(inside) else None,
            "usrec_after_trough_week": int(truth_all.loc[after[0]]) if len(after) else None,
        }
    convention["matches_usrec_labels"] = all(
        entry["usrec_at_trough_week"] == 1 and entry["usrec_after_trough_week"] == 0
        for entry in convention.values()
        if isinstance(entry, dict)
    )
    convention["rule"] = "NBER 저점 월을 침체에 포함한다. 프로젝트가 쓰는 USREC 라벨과 같다."

    return {
        "provenance": provenance,
        "trough_date_convention": convention,
        "evidence_hierarchy": {
            "operational_behaviour": "strict_alfred_real_time",
            "revision_sensitivity": "latest_vintage_causal",
            "episode_labels": "nber_retrospective_only",
            "2020_is_untouched_holdout": False,
            "thirteen_week_monitoring_can_validate_recession_accuracy": False,
        },
        "benchmark": benchmark_result,
        "development_episodes": development,
        "recession_2020_real_time": twenty,
        "recession_2020_latest_vintage_reference": twenty_latest,
        "delay_decomposition": [
            decompose_delay(episode) for episode in [*development, twenty, twenty_latest]
        ],
        "gates": gates,
        "decision": decision,
        "cache": cache,
        "alfred_audit_classification": audit["outcome"]["classification"],
    }
