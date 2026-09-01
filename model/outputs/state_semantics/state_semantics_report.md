# 4국면 v1.1 상태 의미론 감사

**결정: `provisional_model_locked`**

사유: 높은 증거 의미 충돌 0건, 유계 지연이 한도 안, 낮은 증거가 낮다고 보고됐고, 2001년 경로에 숨은 높은 증거 모순이 없으며, 이전 게이트와 보호 지문이 그대로다

## 먼저 적는 것

- 높은 증거 의미 충돌 **0건** (두 표본 합).
- §5 표에서 문자 그대로 어긋난 항목: ['no_high_evidence_week_contradicts_both_level_and_momentum']. §10의 잠금 조건 목록에는 들어 있지 않지만 숨기지 않는다. 모든 §5 항목을 게이트로 걸었다면 결론은 `operational_rejection_confirmed`가 된다.
- 현재 공식 국면 **expansion**, 증거 품질 **low**. 품질을 올리지 않았다.

## 국면 순서는 게이트가 아니다

필터의 전이 행렬은 순환 거리로 감쇠하되 모든 성분이 양수다. 인접 강제도, 단방향 회전 강제도 없다. 후퇴기→확장기 재가속, 회복기→침체 되돌림, 급격한 충격에서의 국면 건너뛰기가 모두 구조적으로 허용된다.

묻는 질문은 '모델이 항상 recovery → expansion → slowdown → contraction 순으로
돌았는가'가 아니라 '그 주의 증거가 그 라벨을 뒷받침했는가'다. 순서 강제는 앞서
진단한 경로 의존과 끈적한 상태 결함을 규칙으로 되살린다.

## 동결 의미 계약

quadrant = sigmoid(level / neutral_level) × sigmoid(momentum / neutral_momentum) 의 네 조합. 부호 경계는 0이고 neutral_*은 전이의 부드러움을 정하는 척도다.

| 국면 | 수준 | 모멘텀 | 폭 | 심각도 |
|---|---|---|---|---|
| recovery | level ≤ 0 (수준이 정상 아래) | momentum > 0 (개선 중) | 회복 증거는 양수 모멘텀 동행 도메인 1개 이상을 요구한다. 노동시장은 이 셈에 들어가지 않는다. | 모멘텀이 recovery_momentum 0.764에 대해 재고, 총량 모멘텀이 중립대를 넘어 연속 9주 양수여야 지속 항이 1에 닿는다. |
| expansion | level > 0 | momentum > 0 | 고유 폭 요건 없음. breadth_support가 가볍게 기울일 뿐이다. | 없음 |
| slowdown | level > 0 | momentum ≤ 0 | 고유 폭 요건 없음. | 없음 |
| contraction | level ≤ 0 | momentum ≤ 0 | §2의 하드 게이트. 공식 침체는 독립적인 동행 도메인 2개의 확인을 요구하며, 넓은 하락 경로와 급속 악화 경로에 각각 마스크로 걸린다. | 침체 증거가 contraction_entry 0.75를 넘은 만큼만 침체 몫이 살아남는다. 못 넘으면 몫이 0으로 눌리고 나머지 셋에 비례 배분된다. |

**침체 증거가 진입 문턱에 못 미치고 회복 증거가 없으면, 두 감쇠의 잔여 몫이 확장·후퇴로 흘러간다. 그래서 수준이 정상 아래인 주에도 확장기가 이길 수 있다. 이것은 구성의 성질이므로 별도 진단으로 세되 국면 순서 위반으로 취급하지 않는다.**

## 표본별 의미 분류

| 표본 | 적격 주 | 지지 | 중립대 유지 | 확인 지연 | 낮은 증거 | 충돌 | 높은 증거 충돌 |
|---|---|---|---|---|---|---|---|
| latest_vintage_causal | 1675 | 1092 | 79 | 152 | 352 | 0 | **0** |
| strict_alfred_real_time | 678 | 384 | 19 | 43 | 232 | 0 | **0** |

| 표본 | 직전 상태 의존 | 최장 원시-공식 불일치 | 최장 확인 대기 | 필터 흡수 | 정상 아래 확장기 |
|---|---|---|---|---|---|
| latest_vintage_causal | 231주 (13.8%) | 10주 | 2주 | 69주 | 144주 |
| strict_alfred_real_time | 62주 (9.1%) | 5주 | 2주 | 28주 | 33주 |

국면별 충돌률

| 표본 | 회복 | 확장 | 후퇴 | 침체 |
|---|---|---|---|---|
| latest_vintage_causal | 0/151 | 0/685 | 0/677 | 0/162 |
| strict_alfred_real_time | 0/34 | 0/288 | 0/338 | 0/18 |

2주 이상 이어진 충돌 구간: 없음

## 2001년 경로 감사

- 창 2001-12-07 ~ 2002-08-30 · 39주
- 분류: {'bounded_confirmation_lag': 11, 'semantically_supported': 28}
- 의미 충돌 0건 · 높은 증거 충돌 0건
- 수준·모멘텀이 **둘 다** 중립대 밖에서 어긋난 높은 증거 주: **1건**
- 공식 전환: [{'from': 'contraction', 'to': 'slowdown'}, {'from': 'slowdown', 'to': 'expansion'}, {'from': 'expansion', 'to': 'slowdown'}, {'from': 'slowdown', 'to': 'expansion'}, {'from': 'expansion', 'to': 'recovery'}]

**1_contraction_exit_supported_by_contemporaneous_evidence** — 그 주의 관측 승자와 공식 라벨이 갈렸고 확인 규칙이 진행 중이었다. 침체 이탈 자체는 총량 모멘텀이 중립대를 향해 올라온 뒤에 일어났다.

**2_why_slowdown_rather_than_recovery** — 회복 몫은 무정보 기준선을 넘는 부분만 recovery_evidence만큼 남는다. 그 시점 총량 모멘텀은 연속 지속 요건(9주)을 채우지 못했고 양수 모멘텀 동행 도메인 폭도 얕아 회복 증거가 거의 0이었다. 그래서 회복 몫이 기준선까지 깎이고, 깎인 몫이 확장·후퇴·침체로 비례 배분되면서 후퇴기가 먼저 이겼다.

**3_why_expansion_later** — 모멘텀이 계속 개선돼 rising이 0.5를 크게 넘었고, 침체 증거는 진입 문턱 아래로 내려가 침체 몫이 0으로 눌렸다. 회복 증거는 여전히 약했으므로 두 감쇠의 잔여 몫이 확장기로 흘러갔다. 수준이 아직 정상 아래인데도 확장기가 이긴 이유다.

**4_why_recovery_only_after_slowdown_and_expansion** — 회복 라벨은 모멘텀 부호가 아니라 폭과 **지속**을 요구한다. 총량 모멘텀이 중립대를 넘어 연속으로 양수인 기간이 쌓인 뒤에야 회복 증거가 커졌고, 그때 비로소 회복 몫이 살아남았다.

**5_did_labels_reflect_genuine_changes** — 각 전환 시점에서 총량 수준과 모멘텀이 실제로 움직였다. 전환은 라벨의 순환이 아니라 관측값의 이동을 따라갔다.

**6_was_any_label_retained_against_strong_evidence** — 그렇다. 확인 창의 마지막 주에 총량 수준과 모멘텀이 **둘 다** 동결 중립대 밖에서 공식 라벨과 어긋난 주가 있다. 모두 3주 확인 한도 안이었고 다음 주에 해소됐지만, 그 주의 공식 출력이 현재 상태를 잘 서술하지 못한 것은 사실이다.

**7_semantic_contradiction_or_non_monotonic_but_supported** — 의미 충돌은 0건이다. 경로는 비단조지만 매주 증거가 뒷받침했다. contraction → slowdown → expansion → recovery 순서는 NBER 서사와 다르지만, 그 순서를 강제하는 것이야말로 앞서 진단한 경로 의존을 되살리는 일이다.

**8_would_forcing_a_recovery_label_have_been_more_accurate** — 아니다. 그 구간의 총량 모멘텀은 회복이 요구하는 지속과 폭을 갖추지 못했다. 회복 라벨을 강제했다면 모멘텀이 한 주 뒤집힐 때마다 회복↔후퇴 진동이 생겼을 것이고, 그것이 바로 후보 J에서 10건 관측돼 지속 요건을 넣게 만든 결함이다. 다만 수준이 정상 아래인 구간을 `expansion`이라 부른 것은 별도의 한계로 남는다.

2013-06-14 이전에는 진짜 빈티지가 없다. 이 감사는 존재하는 최신 수정치 인과 증거로 의미 정합성을 판정했고, 실시간 판정으로 위장하지 않는다.

## 에피소드

| 에피소드 | 표본 | 주 | 지지 | 확인 지연 | 낮은 증거 | 충돌 | 성격 |
|---|---|---|---|---|---|---|---|
| recession_2001_exit | latest_vintage_causal | 40 | 29 | 11 | 0 | 0 | `genuine_current_state_change` |
| gfc_entry | latest_vintage_causal | 61 | 59 | 1 | 1 | 0 | `genuine_current_state_change` |
| gfc_exit | latest_vintage_causal | 73 | 68 | 5 | 0 | 0 | `genuine_current_state_change` |
| late_2019_latest_vintage_false_contraction | latest_vintage_causal | 32 | 19 | 4 | 7 | 0 | `revision_induced_path_dependence` |
| late_2019_strict_real_time | strict_alfred_real_time | 26 | 12 | 0 | 14 | 0 | `low_evidence_ambiguity_dominated` |
| recession_2020_entry_and_recovery | strict_alfred_real_time | 39 | 27 | 3 | 9 | 0 | `genuine_current_state_change` |
| slowdown_and_reacceleration_2022_onward | strict_alfred_real_time | 156 | 68 | 3 | 81 | 0 | `low_evidence_ambiguity_dominated` |
| current_2026 | strict_alfred_real_time | 32 | 0 | 5 | 22 | 0 | `low_evidence_ambiguity_dominated` |

## 현재 2026년 출력

```
Current official U.S. phase: expansion
Evidence quality: low
```

- 필터 사후확률에서 `expansion`가 0.7191로 1위이고 2위 `slowdown`가 0.1620다. 분리도 0.5571는 하한 0.1보다 크지만 증거 품질을 높이기에는 다른 조건이 모자란다.
- 원시 점수 {'recovery': 0.25, 'expansion': 0.501614, 'slowdown': 0.248385, 'contraction': 1e-06}
- 필터 점수 {'recovery': 0.118965, 'expansion': 0.71908, 'slowdown': 0.161955, 'contraction': 0.0}
- 2위와의 차이 0.557125
- 수준 -0.247821 · 모멘텀 0.274323 · 부호 사분면 `recovery` (공식과 일치: False)
- 확증 동행 도메인 2 · 양수 모멘텀 도메인 1
- 집중도 0.525839 (경계 0.6104, 과밀 False)
- 도메인 신선도(주) {'production': 8.0, 'employment': 5.0, 'real_income': 6.0, 'consumption': 4.0, 'labor_stress': 0.0}
- 의미 분류 `low_evidence_ambiguous` → **low_evidence_but_contemporaneously_agreed**
- 직전 국면 `expansion`가 결과를 실질적으로 정하는가: **False**

국면이 바뀌려면

- `slowdown`가 필터 승자가 되고 **동시에** 증거 품질이 high여야 하며, 원시 점수 마진이 0.3 이상이어야 한다. 지금 증거 품질이 low라 이 경로는 닫혀 있다.
- `slowdown`가 필터 승자로 3주 연속 버티면 증거가 약해도 전환한다. 흡수 상태는 없다.
- 중립대 밖으로 나가고(|수준| > 0.503 또는 |모멘텀| > 0.3903), 집중도가 0.6104 아래로 내려가며, 국면 분리도가 0.1 이상이 되고, 신선한 동행 도메인이 유지돼야 한다.

## 분류 어휘

- `semantically_supported`
- `neutral_band_retention`
- `bounded_confirmation_lag`
- `low_evidence_ambiguous`
- `semantic_conflict`
- `withheld`

사유 코드

- `withheld` — 판정 보류 주다. 공식 국면을 내지 않는다.
- `official_equals_raw_high_quality` — 공식 라벨이 그 주 관측 점수의 승자와 같고 증거 품질이 높다.
- `confirmation_in_flight` — 도전자가 확인 기간 안에서 누적 중이다. 기존 확인 규칙의 범위다.
- `neutral_band` — 증거가 동결 중립대 안이라 직전 공식 국면이 유지된다.
- `filter_absorbed_raw_flip` — 공식 라벨이 동결 소프트 필터의 사후확률 승자와 **같다**. 원시 승자가 한두 주 튄 것을 필터가 흡수한 것이며, 불일치가 선언된 구조적 한도 안이다.
- `low_separation_or_stale` — 어느 국면도 강한 분리를 갖지 못했다. 증거 품질이 낮다고 보고한다.
- `contradicts_strong_evidence` — 증거 품질이 높은데 공식 라벨이 원시 승자와도 필터 승자와도 다르고, 중립대·확인·신선도·필터 경계로 설명되지 않는다.

## 의미 지문

- `semantic_digest` 0ea6db2eaabd9adf0c20d9bbf31e633fd4c82737a36dd561550f9f693bd75c1d
- 덮는 것: classification, protected_hashes, weekly_semantic_classifications, path_2001_classification, high_evidence_conflict_count, current_phase_semantic_classification, previous_state_dependence, hard_rules, episode_kinds, decision_reasons
- 빼는 것: executed_at_utc, head_commit, runtime_seconds

`final_validated`는 이 단계가 낼 수 있는 값이 아니다. 허용 분류는 provisional_model_locked, operational_rejection_confirmed뿐이다.

이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.
