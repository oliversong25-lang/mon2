# 미국 경기국면 모델 단계 A 재검증

## 1. 전체 결과 한 줄 요약

**단계 A 미통과.** 빈도 단위·인과적 강건 처리·성숙도 규칙을 구현했으며, 동결과 ALFRED 진행은 판정에 종속된다.

## 2. 추세기간 단위 수정 내용

3년을 주간 156개, 월간 36개, 분기 12개 관측으로 변환하고 원빈도에서 one-sided 추세를 계산한 뒤 주간 공개가용 패널로 정렬했다.

## 3. 중앙값·MAD가 실제 충격을 보존하는 방식

중앙값과 MAD는 현재값을 제외한 과거 기준분포에만 사용한다. 실제 원신호는 바꾸지 않고 `original_signal`, `preclip_signal`, `postclip_signal`을 모두 보존한다. MAD=0이면 causal IQR, 이어서 causal 표준편차를 사용한다.

## 4. 기존 방식과 causal robust 비교

기존 재현 측정값은 재현율 93.4%, 오탐률 6.34%, 최종은 재현율 81.8%, 오탐률 4.25%다. 세부 비교는 `robust_method_comparison.csv`에 있다.

## 5. 팬데믹 충격 보존 결과

기존 최초/4주 확인 지연은 2.0/5.0주, 최종은 2.0/5.0주다. robust 실행의 음의 기여 경제영역 수는 5개다.

## 6. 1985·1990 워밍업 비교

1990 시작은 1995 검증시점에 5년뿐이므로 `preliminary`로 표시했다. 1985·1990 결과와 지표별 5년/10년 도달일은 `warmup_maturity.csv`에 있다.

## 7. 2001년 판정 안정성

최종 구조에서 워밍업 시작 변경에 따른 2001 진입 차이는 25.0주다. 기준은 26주 이하이며 판정은 True다.

## 8. 금융위기 결과

각 단계의 GFC 진입·이탈 지연은 `baseline_comparison.csv`의 `gfc_entry_lag_weeks`, `gfc_exit_lag_weeks`에 기록했다.

## 9. 2020년 결과

원신호·제한 전후 값, 좌표, 확률, 판정 지연과 영역 기여는 `frequency_unit_audit.csv`와 `pandemic_shock_audit.csv`에 기록했다.

## 10. 2022년 이후 결과

기존/최종 2022년 이후 오탐 주는 5/11주다.

## 11. ICSA·CCSA 감사

전체·침체·정상기 상관, 명목·유효가중치, 고확실성 오탐 절대기여와 팬데믹 기여는 `claims_overlap_audit.csv`에 있다. 별도 중복군 상한은 자동 채택하지 않았다.

## 12. Leave-one-out 결과

7개 지표를 각각 제거했다. 현재 대국면 반전 여부와 각 실행의 1·2순위 확률 및 좌표는 `leave_one_out.csv`에 있다.

## 13. 기존 baseline과 최종 후보 비교

변경 전 기준값을 `validation_summary.json`에 별도 보존했고 실제 재실행 값은 `baseline_comparison.csv` 첫 행에 저장했다.

## 14. 강건성 통과·미통과 판정

판정: **미통과**. 최종 후보는 재현율 81.8%, 점프 7건, 왕복 30건으로 각각 85%, 기존+2건, 기존+5건 기준을 충족하지 못했다. 세부 불리언 기준은 `validation_summary.json`의 `checks`에 있다.

## 15. 설정 동결 여부

미통과이므로 설정을 동결하지 않았다.

## 16. ALFRED 진행 여부

단계 A 미통과이므로 ALFRED를 시작하지 않았다.

## 17. 테스트·lint·type check

최종 품질검사 결과는 커밋 전 실행해 WORKLOG와 최종 응답에 기록한다.

## 18. 주요 파일

`preprocessing/transforms.py`, `preprocessing/standardize.py`, `models/composite.py`, `validation/phase3.py`, `tests/test_phase3_robustness.py`.

## 19. 실행 명령

`python -m business_cycle.validation.phase3`

## 20. 현재 한계

최신 수정치 FRED 기반 검증이다. 실시간 빈티지 성능은 ALFRED 단계 전에는 미검증이다.

## 21. 커밋 해시

보고서 생성 시점에는 미커밋이며 최종 응답에서 기록한다.

## 22. 원격 push 여부

보고서 생성 시점에는 미푸시이며 최종 응답에서 기록한다.

## 23. GitHub 링크

`https://github.com/oliversong25-lang/mon2/tree/model/business-cycle-v0.1/model/outputs/robustness_validation/phase3`

## 사실·해석·미검증 가정 구분

- 검증된 사실: CSV와 JSON에 직접 측정된 수치, 테스트로 확인한 인과성·상한·단위 변환.
- 경제적 해석: 다영역 동시 악화와 실업수당 중복기여에 관한 설명.
- 미검증 가정: 최신 수정치 성능이 실시간 빈티지에서도 유지된다는 가정. ALFRED 전에는 확인되지 않았다.
