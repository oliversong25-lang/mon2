# 미국 경기국면 모델 단계 A-2 재검증

## 1. 한 줄 결과

**단계 A-2 미통과.** 빈도 수정 자체는 정상기 오탐을 크게 줄였고, 워밍업 의존성의 원인은
좌표(X·Y) 표준화에 최소 이력 규칙이 없다는 점으로 특정했다. 그러나 corrected baseline의
침체 재현율이 84.3%로 참고기준 85%에 못 미치고, 2001년 진입일
차이도 8주 기준을 만족하지 못한다. 설정을 동결하지 않았고 ALFRED도 시작하지 않았다.

미통과 기준: warmup_2001_shift_at_most_8_weeks, recall_at_least_85pct

## 2. 구성요소 ablation

2x2x2 요인 설계다. 1·2·5·8번 칸은 phase3의 네 단계와 같은 설정이며 측정값도 같다.

| 실험 | 빈도수정 | 성숙도 | robust6 | 재현율 | 오탐률 | 정밀도 | F1 | 점프 | 왕복 | 최장오탐 | 2001진입 | GFC진입 | 2020진입 | 2022+오탐 | 현재국면 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1_legacy | 0 | 0 | 0 | 93.4% | 6.34% | 53.8% | 68.3% | 4 | 20 | 13 | 4주 | -6주 | 5주 | 5 | recovery_early |
| 2_frequency | 1 | 0 | 0 | 89.3% | 3.27% | 68.4% | 77.4% | 7 | 17 | 15 | -12주 | 0주 | 6주 | 0 | slowdown_late |
| 3_maturity | 0 | 1 | 0 | 93.4% | 6.21% | 54.3% | 68.7% | 6 | 13 | 14 | 4주 | -19주 | 5주 | 6 | recovery_early |
| 4_robust6 | 0 | 0 | 1 | 94.2% | 9.29% | 44.5% | 60.5% | 9 | 23 | 25 | -20주 | -22주 | 6주 | 0 | slowdown_late |
| 5_frequency_maturity | 1 | 1 | 0 | 89.3% | 2.81% | 71.5% | 79.4% | 5 | 19 | 12 | -9주 | 2주 | 6주 | 0 | slowdown_late |
| 6_frequency_robust6 | 1 | 0 | 1 | 81.0% | 4.25% | 60.1% | 69.0% | 8 | 29 | 20 | -17주 | 19주 | 5주 | 11 | recovery_early |
| 7_maturity_robust6 | 0 | 1 | 1 | 94.2% | 9.61% | 43.7% | 59.7% | 9 | 21 | 30 | -27주 | -22주 | 6주 | 0 | slowdown_late |
| 8_frequency_maturity_robust6 | 1 | 1 | 1 | 81.8% | 4.25% | 60.4% | 69.5% | 7 | 30 | 20 | -17주 | 19주 | 5주 | 11 | recovery_early |

### 무엇이 무엇을 악화시켰나

| 지표 | legacy | 최종 | 빈도수정 단독 | 성숙도 단독 | robust6 단독 | 상호작용 잔차 |
|---|---:|---:|---:|---:|---:|---:|
| 재현율 | 93.4% | 81.8% | -4.1% | +0.0% | +0.8% | -8.3% |
| 다단계 점프 | 4 | 7 | +3 | +2 | +5 | -7 |
| 3주 왕복 | 20 | 30 | -3 | -7 | +3 | +17 |
| 2022년 이후 오탐 | 5 | 11 | -5 | +1 | -5 | +15 |

핵심은 **robust6 단독은 해롭지 않다**는 것이다. 단독으로는 재현율을 오히려 올린다.
악화는 거의 전부 빈도수정과 robust6의 상호작용에서 나온다. 3년 추세를 원빈도에 적용하면
표준화 전 신호의 절대값이 커지고 median/MAD 척도는 작아져서 ±6 제한이 훨씬 자주 걸린다.
phase3가 robust6을 범인으로 지목한 것은 요인을 분리하지 않았기 때문이다.

## 3. 워밍업 시작시점 비교

| 설정 | 시작연도 | 재현율 | 오탐률 | 점프 | 왕복 | 2001 진입 | 검증시점 상태 |
|---|---|---|---|---|---|---|---|
| legacy_benchmark | 1985 | 93.4% | 6.34% | 4 | 20 | 4주 | mature |
| legacy_benchmark | 1990 | 91.7% | 13.02% | 5 | 17 | -40주 | preliminary |
| corrected_baseline | 1985 | 84.3% | 2.68% | 4 | 13 | -12주 | mature |
| corrected_baseline | 1990 | 90.1% | 8.82% | 7 | 15 | -43주 | preliminary |
| corrected_baseline_rolling_coordinates | 1985 | 79.3% | 3.99% | 6 | 19 | -13주 | mature |
| corrected_baseline_rolling_coordinates | 1990 | 78.5% | 9.70% | 6 | 16 | -43주 | preliminary |
| corrected_baseline_mature_coordinates | 1985 | 79.3% | 4.06% | 6 | 19 | -13주 | mature |
| corrected_baseline_mature_coordinates | 1990 | 78.5% | 6.37% | 4 | 12 | -34주 | preliminary |
| corrected_baseline_full_maturity | 1985 | 79.3% | 4.92% | 6 | 18 | -13주 | mature |
| corrected_baseline_full_maturity | 1990 | 73.3% | 2.36% | 2 | 11 | 판정 없음 | preliminary |

## 4. 시작시점 수렴

`selected` 층의 국면 불일치 비율이다. 두 번째 열은 좌표 표준화의 10년 창이 두 실행 모두에서
자료 안에 완전히 들어간 뒤(2006년 이후)만 본다.

| 설정 | 2000년 이후 불일치 | 완전성숙 후 불일치 | 2001 진입 차이 |
|---|---|---|---|
| legacy_benchmark | 21.2% | 15.1% | 44주 |
| corrected_baseline | 18.3% | 11.3% | 31주 |
| corrected_baseline_rolling_coordinates | 10.4% | 2.4% | 30주 |
| corrected_baseline_mature_coordinates | 8.6% | 2.4% | 21주 |
| corrected_baseline_full_maturity | 2.4% | 2.4% | 측정 불가 |

## 5. 워밍업 의존성의 실제 원인

날짜별 분해(`start_date_decomposition.csv`)에서 2000~2001년 구간을 보면 두 실행의
**합성요인 값은 거의 같다**. 예: 2000-12-08에 1985 실행 -0.046, 1990 실행 -0.029.
지표 전처리는 10년 rolling 표준화 덕분에 이미 수렴했다(표준화 신호 상대 평균차 약 1.4%,
legacy는 19.3%).

차이는 **좌표 표준화**에서 생긴다. 같은 날 좌표 표준화의 중심·척도가

- 1985 실행: 중심 0.388, 척도 0.944
- 1990 실행: 중심 1.006, 척도 0.246

이다. 척도가 약 3.8배 작아서 같은 합성요인이 Y = -0.46 대 Y = -4.21로 벌어진다.
1990 실행이 2001년 침체를 훨씬 먼저 외치는 이유가 이것이다.

원인은 좌표 표준화에 **최소 이력 규칙이 없었다**는 점이다. 지표 표준화에는 5년 최소 이력이
있지만, 좌표 표준화는 `standardization_min_periods`의 26주만 요구했다. 1990 시작 실행은
합성요인이 1995년경에야 시작하므로 2000년 시점에 4~5년치, 그것도 조용한 확장기만 담긴
표본으로 척도를 계산한다.

`coordinates()`에 `minimum_history_weeks`를 추가하고 5년을 요구하면 2001 진입 차이가
44주 → 21주로 줄고, 완전성숙 후 국면 불일치는 11.3% →
2.4%로 떨어진다.

다만 8주 기준은 **어떤 설정으로도 2001년에는 만족할 수 없다.** 이 모델의 총 워밍업 요구는
지표 표준화 최소 이력 5년 + 좌표 표준화 창 10년 = 약 15년이다. 1990년에 시작한 실행은
2005년에야 완전 성숙하므로 2001년 판정은 애초에 미성숙 판정이다. 성숙한 판정과
미성숙 판정을 나란히 놓고 8주 안에 들어오라고 요구하는 것은 측정 자체가 성립하지 않는다.
`corrected_baseline_full_maturity`는 이 사실을 숨기지 않고 1990 실행의 2001년 판정을
아예 내지 않는다(진입일 없음). 숫자를 맞추는 대신 판정을 보류하는 쪽이 옳다.

## 6. expanding과 rolling 비교

| 층 | legacy(expanding) | corrected(rolling) |
|---|---|---|
| 지표 표준화 신호 상대 평균차 | 19.3% | 1.4% |
| 좌표 중심 상대 평균차(완전성숙 후) | 28.4% | 35.1% (좌표는 여전히 expanding) |
| 좌표 중심 상대 평균차, 좌표도 rolling | — | 1.8% |
| 국면 불일치(완전성숙 후) | 15.1% | 2.4% |

지표 층의 rolling 전환은 명확히 효과가 있었고, 좌표 층을 그대로 두면 그 효과가
최종 판정까지 오지 못한다.

## 7. 상태필터 초기값 감사

운영 경로의 초기분포는 균등이다. 일부러 한 국면에 100%를 몰아준 분포와 비교하면 총변동거리는
전이행렬의 2번째 고유값 0.9217
(반감기 8.5주)를 따라 줄어들고,
104주 시점에 3.09e-06까지 내려간다.
`minimum_training_weeks = 104`가 burn-in으로 충분하다.

2001년 시점에 두 실행 모두 300주 이상을 지난 뒤이므로 **상태필터 초기값은 2001년 차이의
원인이 아니다.** 원인에서 배제할 수 있다.

## 8. 실업수당(ICSA·CCSA) 재감사

| 방식 | 재현율 | 오탐률 | 점프 | 왕복 | 정상기 기여 | 침체기 기여 | 고확실성 오탐 기여 | 팬데믹 기여 |
|---|---|---|---|---|---|---|---|---|
| keep_both | 84.3% | 2.68% | 4 | 13 | 18.8% | 24.8% | 24.9% | 33.3 |
| subgroup_cap_15pct | 81.0% | 8.87% | 5 | 14 | 14.7% | 19.1% | 15.0% | 25.5 |
| equal_weight_subfactor | 85.1% | 2.68% | 4 | 16 | 17.6% | 24.2% | 24.6% | 32.7 |

두 계열의 상관은 0.786이다.
중복군 상한(15%)은 오탐률을 2.68%에서
8.87%로
크게 악화시켜 채택하지 않는다. 동일가중 부요인은 재현율을 조금 올리고 오탐률·점프는
그대로지만 왕복이 늘어난다. 한 지표만 좋아졌다고 채택하지 않는다는 원칙에 따라
기본 설정은 **두 계열 유지**로 둔다.

## 9. Leave-one-out

| 실험 | 재현율 | 오탐률 | 점프 | 현재 세부국면 | 현재 대국면 | 1순위 확률 | 2순위 | 2순위 확률 |
|---|---|---|---|---|---|---|---|---|
| none | 84.3% | 2.68% | 4 | slowdown_late | slowdown | 90.6% | contraction_early | 7.4% |
| without_PAYEMS | 83.5% | 9.23% | 6 | slowdown_late | slowdown | 95.5% | contraction_early | 3.1% |
| without_W875RX1 | 79.3% | 8.07% | 9 | slowdown_early | slowdown | 48.6% | slowdown_mid | 27.1% |
| without_INDPRO | 86.8% | 6.76% | 8 | slowdown_late | slowdown | 67.0% | contraction_early | 30.9% |
| without_CMRMTSPL | 82.6% | 8.79% | 10 | slowdown_late | slowdown | 81.2% | contraction_early | 16.1% |
| without_RRSFS | 81.0% | 2.49% | 2 | slowdown_late | slowdown | 88.9% | contraction_early | 9.3% |
| without_ICSA | 85.1% | 3.01% | 6 | slowdown_late | slowdown | 90.0% | contraction_early | 8.0% |
| without_CCSA | 86.0% | 3.27% | 3 | slowdown_late | slowdown | 82.6% | contraction_early | 14.9% |

corrected baseline에서는 어떤 지표 하나를 빼도 현재 **대국면이 바뀌지 않는다**
(모두 slowdown). phase3에서 W875RX1 제거가
회복기→확장기로 뒤집혔던 현상은 재현되지 않는다. W875RX1을 빼면 세부국면만
`slowdown_late`에서 `slowdown_early`로 바뀌고 1순위 확률이
48.6%,
2순위가 27.1%로
좁혀진다. 경계 부근이라는 뜻이며, 국면을 고정하는 문제가 아니라 불확실성과 2순위를
드러내는 문제로 다룬다.

최저 재현율은 79.3%로 침체 포착이 붕괴하지 않는다.

## 10. 역사 사례

| 사례 | legacy | corrected baseline |
|---|---|---|
| 2001 진입 / 이탈 | 4주 / 8주 | -12주 / 2주 |
| 금융위기 진입 / 이탈 | -6주 / -1주 | 10주 / -5주 |
| 2020 진입 / 이탈 | 5주 / 10주 | 4주 / 9주 |
| 2022년 이후 오탐 | 5주 | 0주 |

경제적 해석(검증된 사실이 아님): legacy의 `trend_span_weeks=156`은 월간 지표에 156**개월**,
즉 13년 추세를 적용했다. 13년 추세는 완만해서 금융위기처럼 오래 누적되는 이탈을 그대로
남긴다. 3년 추세는 그 누적분을 추세가 흡수하므로 금융위기 진입이 늦어진다.
`trend_horizon_sensitivity.csv`(phase3)에서 5년으로 늘리면 금융위기 지연이 줄어드는 것이
이 해석과 맞는다. 즉 legacy의 재현율 93.4% 가운데 상당 부분은 빈도 단위 오류가 만든 것이다.

## 11. 단계 A-2 기준 판정

| 기준 | 결과 |
|---|---|
| calendar_consistent_trend_horizons | 통과 |
| native_frequency_preprocessing | 통과 |
| ten_year_causal_rolling_standardization | 통과 |
| rolling_scale_fallbacks_documented | 통과 |
| explicit_minimum_history_handling | 통과 |
| warmup_2001_shift_at_most_8_weeks | **미통과** |
| preprocessing_identical_after_full_maturity | 통과 |
| state_initialization_decays_within_burn_in | 통과 |
| single_indicator_removal_does_not_collapse | 통과 |
| recall_at_least_85pct | **미통과** |
| false_positive_rate_at_most_10pct | 통과 |
| jumps_not_worse_than_legacy | 통과 |
| whipsaws_not_worse_than_legacy | 통과 |

## 12. 설정 동결과 ALFRED

미통과이므로 설정을 동결하지 않았고 SHA-256도 만들지 않았다. ALFRED 빈티지 백테스트도
시작하지 않았다. 기본 `configs/model.yaml`은 그대로다.

## 13. 한계

- 최신 수정치 FRED 자료다. real-time vintage 성능은 미검증이다.
- 12개 세부국면에는 공식 정답 라벨이 없다. NBER는 침체/비침체만 준다.
- 2021년 이후는 이미 결과를 본 `observed diagnostic holdout`이며 표본외가 아니다.
- 대국면 확실성은 Composite 내부 사후확률이고 자료 신뢰도나 모델 간 합의와 다른 개념이다.

## 14. 사실·해석·미검증 가정

- 검증된 사실: 이 디렉터리의 CSV·JSON 수치, 테스트로 확인한 인과성·창 불변성·상한·burn-in.
- 경제적 해석: 13년 추세와 금융위기 조기 포착의 관계, 빈도수정과 robust6의 상호작용 설명.
- 미검증 가정: 최신 수정치 성능이 실시간 빈티지에서도 유지된다는 가정.

## 15. 재현

```bash
cd model
python -m business_cycle.validation.phase4
```
