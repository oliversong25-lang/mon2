# 4국면 v1.1 운영 수용 심사

동결된 v1.1을 **읽기만** 한 별도 사전 등록 단계다. 기각된 채택 규약의 연장이
아니고, 모수 탐색이 아니며, v1.2를 만들 권한도 아니다.

**분류: `operational_rejection`**

사유: 운영 게이트 실패: recovery_and_exit.development_benchmark_post_trough_contraction_run, recovery_and_exit.no_premature_four_week_recovery_inside_a_recession

v1.1의 원래 상태는 `rejected`이며 이 단계가 그것을 바꾸지 않는다.
`final_validated`는 이 단계가 낼 수 있는 분류가 아니다.

## 증거 위계

| 층 | 무엇을 재는가 |
|---|---|
| 엄격 실시간 ALFRED | 운영 거동 |
| 최신 수정치 인과 | 개정 민감도·역사 재구성 |
| NBER 날짜 | 회고적 라벨. 그 시점 정보가 아니다 |

2020년 ALFRED 결과는 이미 들여다봤으므로 손대지 않은 홀드아웃이 **아니다**.

## 회복 인식 비교 기준

개발구간 에피소드 **2개뿐**이다(recession_2001, gfc_2009). 분위수를 통계적으로 안정된
값으로 제시하지 않는다. 최악값을 서술적 상한으로만 쓴다.

- 개발 최악 첫 공식 회복 지연: 31주
- 개발 최악 저점 이후 침체 연속: 8주
- 2020 실시간 첫 공식 회복 지연: 11주 (통과)
- 2020 실시간 저점 이후 침체 연속: 11주 (실패)

## 게이트

| 묶음 | 게이트 | 결과 |
|---|---|---|
| contraction_entry | no_confirmed_2019_contraction_before_the_recession | 통과 |
| contraction_entry | first_official_contraction_within_10_weeks | 통과 |
| contraction_entry | first_persistent_four_week_sequence_within_10_weeks | 통과 |
| contraction_entry | at_least_four_of_eight_recession_weeks_as_contraction | 통과 |
| contraction_entry | no_official_contraction_below_two_confirming_domains | 통과 |
| contraction_entry | no_concentrated_signal_decided_the_official_phase | 통과 |
| recovery_and_exit | development_benchmark_first_recovery_lag | 통과 |
| recovery_and_exit | development_benchmark_post_trough_contraction_run | **실패** |
| recovery_and_exit | no_premature_four_week_recovery_inside_a_recession | **실패** |
| recovery_and_exit | contraction_recovery_round_trips_within_13_weeks | 보고만 |
| operational_integrity | zero_future_information_violations | 통과 |
| operational_integrity | cache_only_no_network_no_key | 통과 |
| operational_integrity | exactly_688_as_of_weeks | 통과 |
| operational_integrity | withheld_and_preliminary_weeks_reproduced | 통과 |
| operational_integrity | no_official_phase_on_a_withheld_week | 통과 |
| operational_integrity | raw_measurements_preserved_on_withheld_weeks | 통과 |
| operational_integrity | raw_versus_official_disagreement_within_structural_limit | 통과 |

## 남은 한계

- 엄격 실시간 침체 에피소드가 **하나뿐**이다. 실시간 침체 성능을 일반화할 수 없다.
- 개발 비교 기준이 에피소드 2개에서 나왔고 둘의 회복 지연이 0주와 31주로 크게 벌어진다.
- 개발 기준은 최신 수정치에서, 2020년은 실시간에서 쟀다. 실시간이 구조적으로 느리다.
- 후반 2019 최신 수정치 침체는 개정 민감도 실패로 그대로 남는다.

이 단계는 투자 판단·섹터·비중·종목·매매 지시를 만들지 않는다.
