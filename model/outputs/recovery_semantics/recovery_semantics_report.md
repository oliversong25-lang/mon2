# 4국면 v1.1 회복 인식 의미론 심사

**분류: `provisional_operational_adoption`**

사유: 2009년 회복은 NBER 전환 월 **안**이고, 저점보다 앞선 확인된 회복은 없으며, 2020년 달력 지연은 amber이고 §6 조건이 모두 통과했다

`final_validated`는 이 단계가 낼 수 있는 값이 아니다. 동결 모델을 읽기만 했고 모수·변환·국면 점수·전이·확인·폭 규칙을 하나도 바꾸지 않았다.

## 첫 장에 두는 주요 한계

- **2001년 침체는 같은 구간대에서 `red`다(31주).** 채택 게이트는 §9-A·§9-B가 `recession_2020` 하나로 한정했으므로 이 결과는 게이트가 아니라 진단이다. 범위를 모든 에피소드로 넓히면 결론은 `operational_rejection_confirmed`로 바뀐다.
- **2009년 5월 22일에 원시·필터 층이 이미 4주 회복 열을 만들었다** (`begins_pre_trough_and_overlaps_turning_month`). 공식 국면이 막았다고 해서 무해하다고 적지 않는다. 안정성 진단으로 공시한다.
- **2020년 회복 지연은 `amber`(11주)다.** 조정 지연 4주는 허용 4주와 정확히 같아 여유가 없다.

## §2. 2001년 red의 범위

- 구간대 어휘는 모든 에피소드에 적용된다: True
- 채택 게이트의 범위: `strict_alfred_real_time_episode_only` (에피소드 `recession_2020`)
- 결과를 계산하기 전에 선언됐는가: True — §9-A '2020 calendar recovery delay is green or amber, not red' · §9-B '2020 recovery latency is red'
- red 구간대 에피소드: ['recession_2001'] · 게이트 대상: False
- 결과를 본 뒤 면제했는가: False
- 게이트가 아닌 이유: 2001년과 금융위기는 진짜 빈티지가 없어 최신 수정치에서만 볼 수 있다. 운영 게이트는 실시간 거동을 재는 것이고, 최신 수정치 경로는 그 시점에 존재하지 않았던 정보를 쓴다. 그래서 §9가 게이트를 실시간 에피소드 하나로 한정했다.

### 31주는 무엇을 잰 수인가

- 저점 월말 이후 공백 31주 중 공식 침체는 **7주**뿐이다.
- 침체를 벗어난 주 2002-01-25 (+8주, `green`)
- 국면별 주 수: {'recovery': 1, 'expansion': 13, 'slowdown': 10, 'contraction': 7}
- 분류: **`recovery_label_skipped_on_the_way_out`** — 공식 `recovery` 라벨이 나오기까지 걸린 달력 시간이다. 침체가 그만큼 이어졌다는 뜻이 아니다. 이 에피소드에서는 침체를 먼저 벗어난 뒤 후퇴기·확장기를 지나 회복기에 닿았다.

연속 침체가 아니다. 회복 라벨을 거치지 않고 후퇴기·확장기를 지나 시계를 돈 것이다.
그렇다고 red를 지우지 않는다. 공식 `recovery` 라벨까지 31주가 걸린 것은 사실이다.

## §3. 2009년 층별 회복 타임라인

| 층 | 첫 회복 | 4주 열 | 6월 이전 | 6월 안 | 위치 | 역할 |
|---|---|---|---|---|---|---|
| raw_phase | 2009-05-22 | 2009-05-22~2009-06-12 | 2주 | 2주 | `begins_pre_trough_and_overlaps_turning_month` | stability_diagnostic_disclosed_not_gated |
| filtered_winner | 2009-05-01 | 2009-05-22~2009-06-12 | 2주 | 2주 | `begins_pre_trough_and_overlaps_turning_month` | stability_diagnostic_disclosed_not_gated |
| official_phase | 2009-06-05 | 2009-06-05~2009-06-26 | 0주 | 4주 | `within_turning_month` | adoption_gate |

네 어휘는 시작만이 아니라 끝까지 보고 정한다. 5월 22일에 시작해 6월 12일에 끝나는
열은 저점 월과 절반이 겹치므로 `entirely_pre_trough_month`가 아니다. 채택 게이트가
보는 '진짜 저점 이전'은 그 하나뿐이고, 게이트 층은 §9-B의 '**confirmed** recovery'
그대로 공식 국면이다. 새 규칙을 만든 것이 아니라 원래 규칙을 정확히 적은 것이다.

세 층 모두 4·8·13주 안 침체 복귀가 0이다.

## 주간 대 월간 전환점 규약

NBER 정점·저점은 **월** 날짜다. 그 달 안 어느 주가 전환점인지 말해 주지 않는다.
1차 규약은 구간 검열이다. 저점 월 전체가 하나의 구간이고, 그 안에서 모델에 유리한
날을 고르지 않는다. USREC 주간 매핑은 2차 비교로만 적는다.

- `pre_trough_recovery` 저점 월 첫날보다 앞선 회복. 이것만이 진짜 조기 이탈이다.
- `within_turning_month` 저점 월 안. 조기라고도 늦었다고도 단정하지 않는다.
- `post_trough_delay` 저점 월 마지막 날보다 뒤진 회복. 지연은 월말부터 잰다.

## 사전 선언 지연 구간대

| 구간대 | 저점 월말 이후 | 뜻 |
|---|---|---|
| green | ≤ 8주 | 운영상 적시다. |
| amber | 9~13주 | 한계를 공시한 잠정 사용에 한해 쓸 수 있다. |
| red | > 13주 | 운영 기각이다. |

통계적으로 추정한 문턱이 아니라 월간 자료를 쓰는 투자 사이클 모델의 운영 정책이다.
결과를 본 뒤에 바꾸지 않았다.

## 전환 월 감사

| 에피소드 | 표본 역할 | 저점 월 | 첫 공식 회복 | 위치 | 달력 지연 | 구간대 | 게이트 |
|---|---|---|---|---|---|---|---|
| recession_2001 | development_latest_vintage | 2001-11 | 2002-07-05 | post_trough_delay | 31주 | red | 보고만 |
| gfc_2009 | development_latest_vintage | 2009-06 | 2009-06-05 | within_turning_month | 0주 | green | 보고만 |
| recession_2020 | strict_alfred_real_time | 2020-04 | 2020-07-17 | post_trough_delay | 11주 | amber | 예 |

## 2009년 회복 재분류

- 첫 원시 회복 2009-05-22 (pre_trough_recovery)
- 첫 공식 회복 2009-06-05 (within_turning_month)
- 첫 4주 확인 회복 2009-06-05 (within_turning_month)
- 재분류: **within_turning_month**
- 명백한 조기 이탈인가: 아니오
- 4·8·13주 안 침체 복귀: 0 · 0 · 0
- 실제 진동: 없음

앞 단계의 기록은 그대로 남는다 — 저점 월을 침체에 포함하는 규약 아래 2009-06-05 시작 4주 회복을 `no_premature_four_week_recovery_inside_a_recession` 실패로 기록했다.
NBER은 저점을 **월**로만 준다. 저점 월 안에서 시작한 회복을 조기라고 단정하려면 그 달 안 어느 주 뒤에 저점이 있었다는 독립적인 주간 증거가 필요하다. 그런 증거를 지어내지 않는다.

### 원시·필터 층에서 나온 앞선 회복 (공시)

게이트는 공식 국면에 건다. 운영이 내보내는 것이 공식 국면이고, 앞 단계의 게이트도
같은 층에 걸려 있었다. 층을 바꾸면 재확인이 아니라 다른 시험이 된다. 다만 아래를
숨기지 않는다.

- {'gfc_2009': {'raw': '2009-05-22', 'raw_position': 'begins_pre_trough_and_overlaps_turning_month', 'filtered_winner': '2009-05-22', 'filtered_winner_position': 'begins_pre_trough_and_overlaps_turning_month'}}

즉 원시 증거와 필터 승자는 저점 월보다 앞서 회복을 가리켰고, 공식 국면을 그 자리에
붙잡은 것은 확인 규칙이었다.

## 2020년 회복 지연 분해

| 층 | 주 |
|---|---|
| publication_delay_weeks | 5 |
| domain_observation_availability_delay_weeks | 2 |
| transformation_delay_weeks | 4 |
| raw_phase_score_delay_weeks | 0 |
| transition_filter_delay_weeks | 0 |
| confirmation_delay_weeks | 0 |
| freshness_or_withholding_delay_weeks | 0 |

### 순차 구간 — 겹침 없음, 빈틈 없음

| 구간 | 시작 | 끝 | 주 | 일 | 진입 조건 | 종료 조건 |
|---|---|---|---|---|---|---|
| publication_delay | 2020-04-30 | 2020-06-05 | 5 | 36 | NBER 저점 월이 끝났다. 그 달 다음 달을 덮는 관측이 아직 하나도 없다. | 동행 도메인 중 **하나라도** 저점 다음 달을 덮는 관측을 얻었다. |
| domain_observation_availability_delay | 2020-06-05 | 2020-06-19 | 2 | 14 | 저점 다음 달 관측이 한 도메인에만 있다. 동결 모델의 폭 요건을 못 채운다. | 저점 다음 달을 덮는 동행 도메인이 `minimum_coincident_domains`에 도달했다. |
| transformation_delay | 2020-06-19 | 2020-07-17 | 4 | 28 | 폭은 채웠으나 동결 변환이 만든 총량 모멘텀이 아직 비양수다. | 총량 모멘텀이 처음 양수가 됐다. |
| raw_phase_score_delay | 2020-07-17 | 2020-07-17 | 0 | 0 | 총량 모멘텀은 양수인데 관측 점수의 승자가 아직 회복이 아니다. | 원시 국면이 처음 `recovery`가 됐다. |
| transition_filter_delay | 2020-07-17 | 2020-07-17 | 0 | 0 | 원시 국면은 회복인데 소프트 필터 승자가 아직 회복이 아니다. | 필터 승자가 처음 `recovery`가 됐다. |
| confirmation_delay | 2020-07-17 | 2020-07-17 | 0 | 0 | 필터 승자는 회복인데 §8 확인 규칙이 공식 국면을 아직 바꾸지 않았다. | 공식 국면이 처음 `recovery`가 됐다. |

불변식: 경계 단조 True · 빈틈 없음 True · 겹침 없음 · 주 합계 11 = 달력 11 · 일 합계 78 = 달력 78.

### 변환 지연 4주는 어느 변환에서 오는가

- 귀속: **`bounded_equal_weight_domain_aggregation`**
- 구간 5주 중 다섯 도메인 모멘텀이 모두 상한 3.7547에 닿은 주: **5주**
- 총량이 상한 부호 투표와 정확히 같았던 주: **5주**

상한을 건 등가중 평균이 포화하면 총량 모멘텀은 사실상 도메인 **부호 투표**가 된다.
그래서 총량이 돌아서려면 도메인 과반이 부호를 바꿔야 했다. 일방 추세 추정이나
중립대 문턱이 아니라 **도메인 총량화**가 이 4주를 만들었다.

이 구간 안에서 동시에 작동한 원인 (더하지 않는다):

- `bounded_equal_weight_domain_aggregation` — 구간 5주 전부에서 다섯 도메인 모멘텀이 상한 3.7547에 닿아 있었다. 총량은 부호 투표와 같았고, 돌아서려면 도메인 과반이 부호를 바꿔야 했다.
- `further_publication_beyond_the_first_post_trough_month` — 폭 요건은 저점 다음 달 관측으로 채워졌지만, 부호를 뒤집은 것은 그 다음 달 관측이었다. 이 구간에서 발표와 변환은 **동시에** 작동했다.
- `one_sided_momentum_window_of_8_weeks` — 모멘텀은 인과 창으로만 계산한다. 한 달치 저점 이후 자료로는 창 안 부호가 바뀌지 않았다.

11주 전부를 발표 지연이라고 적지 않는다. 발표에 귀속되는 것은 5주뿐이다.

| 날짜 | 값 |
|---|---|
| calendar_trough_interval_end | 2020-04-30 |
| first_post_trough_data_available | 2020-06-05 |
| recovery_observable_date | 2020-06-19 |
| first_raw_recovery | 2020-07-17 |
| recovery_recognizable_date | 2020-07-17 |
| first_filtered_recovery | 2020-07-17 |
| first_official_recovery | 2020-07-17 |
| transformation_turn_date | 2020-07-17 |
| first_confirmed_recovery | 2020-07-17 |

- 달력 회복 지연 **11주** (`amber`)
- 자료가용성 조정 지연 **4주** (기준 2020-06-19, 허용 4주)
- 상태 기계 지연 0주
- 한계 어휘: **`coincident-data recognition lag`** (허용 어휘 coincident-data recognition lag, state-machine delay, transformation/evidence lag)

조정 지연이 달력 지연을 지우지 않는다. 둘을 나란히 적는다.

## 사전 통과 게이트 재확인

- 재확인한 게이트 13개
- 퇴행: 없음
- 실시간 경로 재현: 일치 (35주)

## §6 amber 조건

| 조건 | 값 | 한도 | 결과 |
|---|---|---|---|
| delay_not_extended_by_filter_or_confirmation | 0 | 4 | 통과 |
| official_follows_raw_within_confirmation_allowance | 0 | 4 | 통과 |
| adjusted_latency_within_allowance | 4 | 4 | 통과 |
| no_contraction_recovery_contraction_round_trip_within_13_weeks | 0 | - | 통과 |
| no_genuine_pre_trough_four_week_recovery | [] | - | 통과 |
| pre_trough_recovery_in_the_raw_or_filtered_layer | {'gfc_2009': {'raw': '2009-05-22', 'raw_position': 'begins_pre_trough_and_overlaps_turning_month', 'filtered_winner': '2009-05-22', 'filtered_winner_position': 'begins_pre_trough_and_overlaps_turning_month'}} | - | 통과 |
| previously_passed_gates_still_pass | [] | - | 통과 |
| one_unambiguous_official_phase | 0 | - | 통과 |

## 남은 한계

- 엄격 실시간 침체 에피소드가 **하나뿐**이다. 실시간 침체 성능을 일반화할 수 없다.
- 2020년은 이미 들여다봤으므로 손대지 않은 홀드아웃이 아니다.
- 2013-06-14 이전에는 진짜 빈티지가 없다. 2001년과 금융위기는 최신 수정치에서만 봤다.
- 개발 에피소드가 둘뿐이고 회복 거동이 크게 갈린다.
- v1.1은 최신 수정치 규약 아래에서 기각된 상태로 남아 있다.

이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.

## 의미 지문

- `semantic_digest` ec6fdb414718905bd6cccc359c24e5c6cb8aeb50a050e6472b9fc2dbe45e0f3d
- 덮는 것: classification, adoption_status, gate_results, protected_hashes, sample_roles, latency_values, episode_classifications, current_official_phase, model_status, decision_reasons
- 빼는 것: executed_at_utc, head_commit, runtime_seconds — 이 셋뿐이다

실행 시각만 바뀌면 지문은 같다. 분류·게이트 결과·보호 지문·지연 값·현재 국면 중
하나라도 바뀌면 달라진다. 원본 산출물은 감사 기록으로 그대로 남긴다.
