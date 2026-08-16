# 미국 경기국면 모델 v0.1 — 공식 FRED 실자료 검증

> 본 결과는 최신 수정치 기준의 preliminary backtest이며, 당시 실제 공개정보만 사용한 real-time vintage backtest가 아닙니다.

## 한 줄 요약

공식 FRED 최신 수정치에서 8주 최소 조정은 점프를 줄이고 침체 재현율을 94.2%로 높였지만, 오탐률 22.9%·정밀도 24.6%라 현재 모델은 운영 판정용으로 부족하다.

## 데이터와 모델

- 공식 자료: FRED 핵심 7개 지표 + `USREC` NBER 침체지표
- 실행 시각(UTC): 2026-08-16T14:05:36.089672+00:00
- 실행 전 Git 커밋: `d2930d318582e37406e5019369aad22b2e31d8d2`
- 워밍업 수집 시작: 1985-01-01
- 실제 모델 이력: 1995-01-06 ~ 2026-08-14
- 지표별 최종 관측일: CCSA 2026-08-01, CMRMTSPL 2026-05-01, ICSA 2026-08-08, INDPRO 2026-06-01, PAYEMS 2026-07-01, RRSFS 2026-07-01, W875RX1 2026-06-01
- 대표모델: `CompositeFactorModel`
- 비교모델: `DynamicFactorModel`
- baseline 설정: momentum_weeks=4
- adjusted 설정: momentum_weeks=8
- 현재 판정: 회복기 중기 (recovery)
- 확실성: 대국면 90.7, 세부 43.3, 데이터 64.6

## NBER 침체 비교

- baseline 재현율 88.4%, 정밀도 22.4%, 오탐률 24.3%
- adjusted 재현율 94.2%, 정밀도 24.6%, 오탐률 22.9%
- NBER는 침체/비침체의 객관적 비교에만 사용했다. 12개 세부국면에는 공식 정답 라벨이 없다.

## 역사적 사례

### 2001년 침체

- 세부국면 변경 25회, 다단계 점프 0회
- 침체 오탐 주수 34주
- 압축 경로: expansion_late → slowdown_early → slowdown_mid → slowdown_early → expansion_late → slowdown_early → slowdown_mid → slowdown_late → slowdown_mid → slowdown_early → slowdown_mid → slowdown_late → contraction_early → contraction_mid → contraction_late → contraction_mid → contraction_late → recovery_early → contraction_late → recovery_early → contraction_late → recovery_early → recovery_mid → recovery_early → contraction_late → recovery_early

- NBER 진입 대비 -15.0주, 종료 대비 +1.0주
- 진입 전 26주 후퇴기 신호: True

### 2007~2009년 금융위기

- 세부국면 변경 41회, 다단계 점프 0회
- 침체 오탐 주수 60주
- 압축 경로: recovery_late → recovery_mid → recovery_early → contraction_late → contraction_mid → contraction_early → contraction_mid → contraction_late → contraction_mid → contraction_late → recovery_early → contraction_late → recovery_early → recovery_mid → recovery_early → contraction_late → recovery_early → recovery_mid → recovery_early → recovery_mid → recovery_early → recovery_mid → recovery_early → contraction_late → contraction_mid → contraction_late → contraction_mid → contraction_late → contraction_mid → contraction_late → contraction_mid → contraction_late → recovery_early → recovery_mid → recovery_early → recovery_mid → recovery_early → recovery_mid → recovery_early → recovery_mid → recovery_early → recovery_mid

- NBER 진입 대비 -25.0주, 종료 대비 -4.0주
- 진입 전 26주 후퇴기 신호: False
- 리먼 파산일 인접 주 판정: contraction_mid (contraction); 리먼일은 NBER 시작일로 사용하지 않음

### 2020년 팬데믹

- 세부국면 변경 30회, 다단계 점프 1회
- 침체 오탐 주수 46주
- 압축 경로: contraction_early → contraction_mid → contraction_late → recovery_early → recovery_mid → recovery_early → contraction_late → contraction_mid → contraction_late → contraction_mid → contraction_late → recovery_early → recovery_mid → recovery_late → recovery_mid → recovery_early → contraction_late → contraction_mid → contraction_late → recovery_early → recovery_mid → contraction_early → contraction_mid → contraction_late → recovery_early → recovery_mid → recovery_late → recovery_mid → recovery_early → recovery_mid → recovery_late

- NBER 진입 대비 +2.0주, 종료 대비 +7.0주
- 진입 전 26주 후퇴기 신호: False

### 2022년 이후

- 세부국면 변경 51회, 다단계 점프 3회
- 침체 오탐 주수 77주
- 압축 경로: expansion_mid → expansion_late → slowdown_early → slowdown_mid → slowdown_early → expansion_late → slowdown_early → slowdown_mid → slowdown_late → slowdown_mid → slowdown_late → slowdown_mid → slowdown_late → slowdown_mid → slowdown_early → expansion_late → slowdown_early → slowdown_mid → slowdown_late → contraction_early → slowdown_late → expansion_mid → expansion_early → recovery_mid → contraction_mid → contraction_early → contraction_mid → contraction_late → contraction_mid → contraction_late → recovery_early → recovery_mid → recovery_early → contraction_late → contraction_mid → contraction_late → recovery_early → contraction_late → recovery_early → recovery_mid → recovery_early → contraction_late → recovery_early → recovery_mid → recovery_early → contraction_late → recovery_early → contraction_late → recovery_early → contraction_late → recovery_early → recovery_mid


## 안정성 진단과 최소 조정

- baseline: 변경 424회, 점프 21회, 왕복 40회
- adjusted: 변경 332회, 점프 7회, 왕복 37회
- 변경은 X 모멘텀 창을 4주에서 8주로 늘린 한 가지뿐이다. 월간 발표 계단과 주간 청구 노이즈 민감도를 줄이면서 침체 재현율이 악화되지 않아 채택했다.
- 그러나 adjusted도 침체 정밀도가 낮고 정상기 오탐이 많아 운영 판정용으로 충분하지 않다.
- 모든 점프와 3주 왕복은 별도 CSV에 날짜·좌표·확률·기여지표·발표주 영향·충격 맥락을 기록했다.
- 점프 판정 분포: {'model instability review': 7}
- 왕복 원인 분포: {'origin uncertainty': 25, 'release-week step effect': 12}
- 최소 지표 가용률: 85.7%; 최소 기준 미달 0주

## Composite와 Dynamic 비교

- 요인 상관계수: 0.787
- 대국면 일치율: 51.4%
- Dynamic은 민감도 비교용이며 공식 판정에는 사용하지 않았다.

## 검증된 사실·해석·미검증 가정

### 실제로 검증된 사실

- 공식 FRED 최신 수정치 자료로 1995년 이후 인과적 forward-filter 백테스트를 실행했다.
- 미래 관측 변경 불변성, 확률합, 수집 실패 표시 테스트를 자동 검사했다.
- baseline과 8주 모멘텀 조정안을 같은 자료와 지표로 비교했다.

### 경제적 해석에 근거한 판단

- baseline의 잦은 왕복과 점프는 4주 X 모멘텀의 발표주 계단·노이즈 민감성이 주원인으로 판단했다.
- 2020년의 일부 급격한 이동은 외생 충격에 비추어 다른 시기의 점프보다 정당화 가능하다.

### 아직 검증되지 않은 가정

- 최신 수정치 결과가 당시 실제 공개정보에서도 같았을 것이라는 가정은 검증되지 않았다.
- ALFRED 빈티지, 실제 발표일 전체 이력, 12개 세부국면 정답 라벨은 이번 범위에 없다.
- 고정 가중치와 8주 모멘텀의 다른 표본·향후 기간 안정성은 추가 검증이 필요하다.

## 차트 읽기

- `01`은 X·Y 좌표의 충격과 회복 경로, `02`~`03`은 NBER 음영 대비 판정 경로를 보여준다.
- `04`~`05`는 후보 확률 격차와 확실성이 전환기에서 낮아지는지 확인한다.
- `06`~`09`는 네 역사 사례 확대, `10`은 대표·비교모델의 요인과 대국면 일치도를 보여준다.

## 재현 명령

```powershell
.\.venv\Scripts\business-cycle.exe validate-real --start 1995-01-01 --end 2026-08-14
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\mypy.exe --strict src
```

## 다음 우선 작업

- NBER 비침체 오탐의 구조적 원인을 Y 수준·각도 대국면 매핑별로 분해한다.
- ALFRED 실시간 빈티지와 실제 발표일을 연결해 최신 수정치 편향을 측정한다.
- 12개 세부국면은 공식 정답이 없으므로 경제적 사건표와 별도 검증 설계를 먼저 확정한다.
