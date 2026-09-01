# 미국 경기국면 모델 v0.1 — 2차 실자료 보정

> 본 결과는 최신 수정치 기준의 preliminary backtest이며, 당시 실제 공개정보만 사용한 real-time vintage backtest가 아닙니다.

## 결론

Y 수준 근거 게이트와 원점 부근 인접이동 제약을 단계적으로 적용한 후보를 최종 채택했다.
8주 baseline의 높은 침체 재현율과 2020 반응성을 유지하면서 정상기 오탐과 점프를 크게 줄였다.

## 전체기간 비교

| model | segment | true_positive_weeks | false_negative_weeks | false_positive_weeks | true_negative_weeks | recession_recall | recession_false_positive_rate | recession_precision | recession_specificity | recession_f1 | balanced_accuracy | phase_changes | broad_changes | multi_step_jumps | three_week_whipsaws | false_positive_episode_count | longest_false_positive_weeks | turning_points | current_phase |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| baseline_4w | overall | 107 | 14 | 371 | 1158 | 0.8843 | 0.2426 | 0.2238 | 0.7574 | 0.3573 | 0.8208 | 424 | 175 | 21 | 40 | 58 | 22 | [{"official_start_week": "2001-04-06", "official_end_week": "2001-11-30", "confirmed_entry_week": "2001-01-12", "entry_lead_lag_weeks": -12.0, "confirmed_exit_week": "2002-01-18", "exit_lead_lag_weeks": 7.0, "confirmation_rule": "4 consecutive weeks, no backdating"}, {"official_start_week": "2008-01-04", "official_end_week": "2009-06-26", "confirmed_entry_week": "2007-11-23", "entry_lead_lag_weeks": -6.0, "confirmed_exit_week": "2009-05-29", "exit_lead_lag_weeks": -4.0, "confirmation_rule": "4 consecutive weeks, no backdating"}, {"official_start_week": "2020-03-06", "official_end_week": "2020-04-24", "confirmed_entry_week": "2020-04-10", "entry_lead_lag_weeks": 5.0, "confirmed_exit_week": "2020-06-12", "exit_lead_lag_weeks": 7.0, "confirmation_rule": "4 consecutive weeks, no backdating"}] | recovery_mid |
| baseline_8w | overall | 114 | 7 | 350 | 1179 | 0.9421 | 0.2289 | 0.2457 | 0.7711 | 0.3897 | 0.8566 | 332 | 117 | 7 | 37 | 39 | 27 | [{"official_start_week": "2001-04-06", "official_end_week": "2001-11-30", "confirmed_entry_week": "2001-01-12", "entry_lead_lag_weeks": -12.0, "confirmed_exit_week": "2002-01-25", "exit_lead_lag_weeks": 8.0, "confirmation_rule": "4 consecutive weeks, no backdating"}, {"official_start_week": "2008-01-04", "official_end_week": "2009-06-26", "confirmed_entry_week": "2007-08-03", "entry_lead_lag_weeks": -22.0, "confirmed_exit_week": "2009-06-19", "exit_lead_lag_weeks": -1.0, "confirmation_rule": "4 consecutive weeks, no backdating"}, {"official_start_week": "2020-03-06", "official_end_week": "2020-04-24", "confirmed_entry_week": "2020-04-10", "entry_lead_lag_weeks": 5.0, "confirmed_exit_week": "2020-07-03", "exit_lead_lag_weeks": 10.0, "confirmation_rule": "4 consecutive weeks, no backdating"}] | recovery_mid |
| candidate_y_gate | overall | 113 | 8 | 85 | 1444 | 0.9339 | 0.0556 | 0.5707 | 0.9444 | 0.7085 | 0.9391 | 219 | 72 | 22 | 17 | 12 | 13 | [{"official_start_week": "2001-04-06", "official_end_week": "2001-11-30", "confirmed_entry_week": "2001-05-04", "entry_lead_lag_weeks": 4.0, "confirmed_exit_week": "2002-01-25", "exit_lead_lag_weeks": 8.0, "confirmation_rule": "4 consecutive weeks, no backdating"}, {"official_start_week": "2008-01-04", "official_end_week": "2009-06-26", "confirmed_entry_week": "2007-11-23", "entry_lead_lag_weeks": -6.0, "confirmed_exit_week": "2009-06-19", "exit_lead_lag_weeks": -1.0, "confirmation_rule": "4 consecutive weeks, no backdating"}, {"official_start_week": "2020-03-06", "official_end_week": "2020-04-24", "confirmed_entry_week": "2020-04-10", "entry_lead_lag_weeks": 5.0, "confirmed_exit_week": "2020-07-03", "exit_lead_lag_weeks": 10.0, "confirmation_rule": "4 consecutive weeks, no backdating"}] | recovery_early |
| candidate_y_gate_adjacent | overall | 113 | 8 | 97 | 1432 | 0.9339 | 0.0634 | 0.5381 | 0.9366 | 0.6828 | 0.9352 | 217 | 66 | 4 | 20 | 14 | 13 | [{"official_start_week": "2001-04-06", "official_end_week": "2001-11-30", "confirmed_entry_week": "2001-05-04", "entry_lead_lag_weeks": 4.0, "confirmed_exit_week": "2002-01-25", "exit_lead_lag_weeks": 8.0, "confirmation_rule": "4 consecutive weeks, no backdating"}, {"official_start_week": "2008-01-04", "official_end_week": "2009-06-26", "confirmed_entry_week": "2007-11-23", "entry_lead_lag_weeks": -6.0, "confirmed_exit_week": "2009-06-19", "exit_lead_lag_weeks": -1.0, "confirmation_rule": "4 consecutive weeks, no backdating"}, {"official_start_week": "2020-03-06", "official_end_week": "2020-04-24", "confirmed_entry_week": "2020-04-10", "entry_lead_lag_weeks": 5.0, "confirmed_exit_week": "2020-07-03", "exit_lead_lag_weeks": 10.0, "confirmation_rule": "4 consecutive weeks, no backdating"}] | recovery_early |
| dynamic_factor | overall | 105 | 16 | 48 | 1481 | 0.8678 | 0.0314 | 0.6863 | 0.9686 | 0.7664 | 0.9182 | 363 | 129 | 7 | 95 | 9 | 16 | [{"official_start_week": "2001-04-06", "official_end_week": "2001-11-30", "confirmed_entry_week": "2001-01-05", "entry_lead_lag_weeks": -13.0, "confirmed_exit_week": "2002-01-11", "exit_lead_lag_weeks": 6.0, "confirmation_rule": "4 consecutive weeks, no backdating"}, {"official_start_week": "2008-01-04", "official_end_week": "2009-06-26", "confirmed_entry_week": "2008-01-18", "entry_lead_lag_weeks": 2.0, "confirmed_exit_week": "2009-05-22", "exit_lead_lag_weeks": -5.0, "confirmation_rule": "4 consecutive weeks, no backdating"}, {"official_start_week": "2020-03-06", "official_end_week": "2020-04-24", "confirmed_entry_week": "2020-04-17", "entry_lead_lag_weeks": 6.0, "confirmed_exit_week": "2020-07-10", "exit_lead_lag_weeks": 11.0, "confirmation_rule": "4 consecutive weeks, no backdating"}] | slowdown_late |

## 최종 nowcast

- 국면: 회복기 초기
- 대국면/세부/데이터 확실성: 98.4 / 53.7 / 64.6
- Composite-Dynamic 대국면 일치율: 56.8%
- 대국면 확실성은 대표모델 내부의 같은 대국면 확률합이고 모델 간 합의도가 아니다.
- 모델 불일치는 데이터 신뢰도의 model_agreement 구성요소에만 반영된다.
- 세부 확실성이 낮으면 현재 대국면 안의 세부 위치 해석을 보수적으로 해야 한다.

## 구간 해석

- 1995~2012는 개발·원인분석 구간이다.
- 2013~2019는 정상 확장기 안정성 확인 구간이다.
- 2020은 별도 극단 충격 스트레스 구간이다.
- 2021~2026은 이미 결과를 본 observed diagnostic holdout이며 진정한 표본외가 아니다.

## 검증된 사실·경제적 해석·미검증 가정

### 코드와 실자료에서 검증된 사실
- 후퇴기는 침체 양성에 포함되지 않는다.
- 공식 FRED USREC와 같은 기간·자료로 네 모델을 비교했다.
- 최종 후보는 2020 진입·종료 반응을 8주 baseline과 동일하게 유지했다.

### 경제적 해석
- 약한 음수 Y와 작은 반지름은 공식 침체보다 둔화·불확실성으로 해석하는 것이 타당하다.
- INDPRO의 큰 음의 신호는 2022+ 오탐을 오래 지지했으나 단일 가중치 상한 위반은 아니다.

### 아직 검증되지 않은 가정
- 최신 수정치 결과가 당시 실시간 빈티지에서도 유지된다는 보장은 없다.
- 12개 세부국면에는 공식 정답 라벨이 없다.
- 2021+는 observed diagnostic holdout이므로 향후 신규 자료의 prospective 검증이 필요하다.
