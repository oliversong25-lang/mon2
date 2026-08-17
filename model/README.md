# 미국 경기국면 모델 v0.1

미국 실물경기의 주간 잠재 수준 `Y`와 최근 모멘텀 `X`를 추정해 12개 경기국면의
필터링 확률과 대표 국면 하나를 출력하는 독립 연구 모델입니다. 기존 AssetFlow
프런트엔드와 결합하지 않으며 `model/` 안에서 설치·실행·테스트됩니다.

## 모델의 목적과 12개 국면

`Y`는 고용·소득·생산·소비·판매에서 추출한 추세 대비 공통 실물경기 수준이고,
`X`는 `Y_t - Y_{t-4}`와 상태공간모형의 국소 기울기를 각각 과거 자료로 표준화해
평균한 모멘텀입니다. `atan2(Y, X)`의 0~360도 각도를 설정 가능한 30도 구간으로
나눕니다.

| 각도 | 대표 국면 |
|---:|---|
| 270~300 | 회복기 초기 |
| 300~330 | 회복기 중기 |
| 330~360 | 회복기 말기 |
| 0~30 | 확장기 초기 |
| 30~60 | 확장기 중기 |
| 60~90 | 확장기 말기 |
| 90~120 | 후퇴기 초기 |
| 120~150 | 후퇴기 중기 |
| 150~180 | 후퇴기 말기 |
| 180~210 | 침체기 초기 |
| 210~240 | 침체기 중기 |
| 240~270 | 침체기 말기 |

각도 관측확률은 원형 Gaussian이라 경계에서 0과 1로 끊기지 않습니다. 원점 거리
`R = sqrt(X² + Y²)`가 작으면 분포를 넓힙니다. 유지·다음·이전 상태를 우대하는
순환 전이행렬로 forward filtering하며 미래 관측을 쓰는 backward smoothing은 하지
않습니다. 각도 구간과 전이확률은 `configs/transitions.yaml`에서 바꿉니다.

## 경기 본체와 선행 레이어

경기 본체는 `PAYEMS`, `W875RX1`, `INDPRO`, `CMRMTSPL`, `RRSFS`, `ICSA`,
`CCSA`만 사용합니다. 영역 초기 비중은 고용 25%, 생산 20%, 소득 20%, 소비·판매
20%, 주간 브리지 15%이며, 개별 지표·영역 상한과 가용 지표 재정규화 규칙은
`configs/indicators.yaml`에 있습니다.

두 경기 수준 모델은 같은 희소 주간 사건 행렬을 받습니다.

- `CompositeFactorModel`: 제한된 신선도 기간 동안 마지막 신호를 보유하고 가용
  가중치를 재정규화하는 해석 가능한 기준선입니다.
- `DynamicFactorModel`: `[level, slope]` 국소선형추세 상태와 지표별 결측 관측을 가진
  실제 Kalman filter입니다. 월간 발표가 없는 주에는 예측만 하고, 이전 월 값을 새
  관측처럼 반복 삽입하지 않습니다. v0.1은 계열별 loading과 잡음분산을 재추정하는
  완전한 대규모 DFM은 아닙니다.

`PERMIT`, `NEWORDER`, `AWHMAN`, `T10Y3M`, `ANFCI`는 현재 `Y`에 섞지 않는 선행
레이어 설정으로 분리했습니다. 13주 예측은 아직 보정되지 않았으므로 확률을
지어내지 않고 `forecast_13w.status = not_calibrated`를 출력합니다.

## 데이터 출처와 가용일

`FredCollector`는 FRED graph CSV 최신 수정치를 원자료 캐시에 저장합니다. 네트워크
실패 시 기존 캐시가 있으면 경고와 함께 사용하고, 캐시도 없으면 실패합니다. API 키와
인증정보는 저장하지 않습니다. `FRED_API_KEY` 자리만 `.env.example`에 남겨 향후 공식
API·ALFRED 수집기를 같은 인터페이스에 붙일 수 있게 했습니다.

로컬 입력은 통합 `observations.csv` 또는 지표별 CSV를 지원합니다. 공통 스키마:

```text
indicator_id, observation_period, value, release_date, vintage_date,
fetched_at, source, revision_status, freshness_score
```

실제 `release_date`가 없으면 지표 설정의 보수적 지연일로 가용일을 추정하고 경고합니다.
FRED 최신 수정치에는 당시 빈티지가 없으므로 `vintage_date`는 비어 있을 수 있습니다.
원자료·가공자료·캐시는 각각 `data/raw`, `data/processed`, `data/cache`로 분리하며 Git에
포함하지 않습니다. 테스트는 재배포 제한이 없는 합성 데이터만 사용합니다.

## 설치와 실행

Python 3.11 이상:

```bash
cd model
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
```

FRED 최신 수정치 수집:

```bash
business-cycle fetch --start 1960-01-01
```

수집한 로컬 자료로 현재 판정:

```bash
business-cycle nowcast --as-of 2026-08-14
business-cycle nowcast --data-source local --data-dir ./data/sample --as-of 2026-08-14
```

FRED를 즉시 호출해 판정하려면 `--data-source fred`를 지정합니다. 네트워크 없이 전체
파이프라인과 보고서를 실행하는 방법:

```bash
business-cycle demo
```

시간순 백테스트:

```bash
business-cycle backtest \
  --start 1965-01-01 \
  --end 2026-08-14 \
  --walk-forward
```

## 설정 변경

- `configs/indicators.yaml`: 변환, 방향, 가중치, 발표 지연, 신선도, 결측 기준
- `configs/model.yaml`: 최소 학습기간, one-sided 추세, Kalman 분산, 확실성 계수
- `configs/transitions.yaml`: 12개 각도 경계와 순환 전이확률

기본 변환과 대안 변환은 구분합니다. v0.1 nowcast가 여러 변환의 임의 평균이 되지
않으며, 대안은 설정을 바꾼 별도 백테스트로 비교합니다.

## 출력 파일

`outputs/` 아래에 다음을 원자적으로 생성합니다.

- `*.json`: 대표 국면, 12확률, 좌표, 3개 확실성, 근거·반대 지표, 경고, 메타데이터
- `*.csv`: 주간 `X`, `Y`, 각도, 반경, 12개 필터링 확률, 대표 국면 이력
- `*.md`: 사람이 읽는 현재 판정 요약
- `backtest-metrics.json`: 전환·점프·지속기간·NBER 침체/비침체 비교

확률 합은 `1 ± 1e-9`로 검증합니다. 핵심지표 확보율이 최소 기준보다 낮으면 대표 잠정
확률은 남기되 `status = withheld`와 판정 보류 사유를 출력합니다.

## 백테스트와 미래정보 누출 방지

전처리는 현재 관측이 자기 기준분포를 바꾸지 않도록 한 칸 지연한 expanding 평균·표준
편차와 one-sided EMA를 사용합니다. 상태확률은 forward filter만 사용합니다. 한 번의
순방향 재귀가 매주 다시 적합하는 walk-forward와 같은 결과를 내므로 전체기간 그래프를
사후 smoothing한 것이 아닙니다.

테스트는 미래 구간을 10,000배로 바꾼 두 데이터셋의 과거 전체 판정이 완전히 같은지
비교합니다. 월간 지표의 비발표 주가 NaN 사건으로 남는지도 확인합니다.

NBER 기준일은 침체/비침체 포착률·오탐률에만 사용합니다. NBER에는 12개 세부국면
정답이 없으므로 12국면 정확도를 계산하지 않습니다.

> 본 결과는 최신 수정치 기준의 preliminary backtest이며, 당시 실제 공개정보만 사용한
> real-time vintage backtest가 아닙니다.

## 품질검사

```bash
python -m pytest
python -m ruff format --check src tests
python -m ruff check src tests
python -m mypy src
```

필수 회귀 테스트는 각도 12구간, 0/360 경계, 침체기 말기→회복기 초기 순환, 확률합,
원점·후보격차 확실성, 결측 신뢰도, 월간 관측 비복제, causal 표준화, 미래값 변경 비교,
가중치 재정규화, 역방향 부호, 점프 억제, JSON 스키마, 전체 smoke, seed 재현성을
포함합니다.

## 단계 A 강건성 재검증

`python -m business_cycle.validation.phase3`는 기존 설정, 원빈도 달력 3년 추세,
지표 성숙도, causal rolling median/MAD와 기여단계 Huber 제한을 순서대로 비교합니다.
원신호·제한 전 robust z·제한 후 신호를 함께 보존하고, MAD가 0이면 현재값을 제외한
과거 IQR과 표준편차 순으로 대체합니다. 지표는 이력 5년 미만이면 제외하고 5~10년은
가중치를 선형으로 늘리며, 이후에도 개별 20%·영역 30% 상한을 적용합니다.

2026-08-17 실자료 재검증에서 robust6 후보는 팬데믹 반응과 오탐률을 개선했지만
재현율·다단계 점프·3주 왕복 기준을 통과하지 못했습니다. 따라서 기본 설정은 변경하지
않았고 설정 스냅샷·해시를 만들지 않았으며 ALFRED도 시작하지 않았습니다. 측정값과
10개 차트는 `outputs/robustness_validation/phase3/`에 있습니다.

## 단계 A-2 corrected baseline

`configs/baselines.yaml`이 설정을 명시적으로 분리한다. `load_baseline(name)`으로 읽는다.

| 이름 | 추세 | 지표 표준화 | 성숙도 | robust | 좌표 표준화 |
|---|---|---|---|---|---|
| `legacy_benchmark` | 156주 고정 | expanding | 없음 | 없음 | expanding |
| `corrected_baseline` | 달력 3년 | 10년 rolling 평균·표준편차 | 5~10년 ramp | 없음 | expanding |
| `corrected_baseline_rolling_coordinates` | 달력 3년 | 같음 | 같음 | 없음 | 10년 rolling |
| `corrected_baseline_mature_coordinates` | 달력 3년 | 같음 | 같음 | 없음 | 10년 rolling, 최소 5년 |
| `corrected_baseline_full_maturity` | 달력 3년 | 같음 | 같음 | 없음 | 10년 rolling, 최소 10년 |
| `corrected_baseline_huber8` | 달력 3년 | 10년 rolling 중앙값·MAD | 같음 | Huber ±8 | expanding |

`legacy_benchmark`는 월간 지표에 156**개월**(약 13년) 추세를 적용하는 빈도 단위 문제를
그대로 갖고 있다. 이전 결과를 재현하기 위해서만 남겨 두며 운영 후보가 아니고, 재현율
93.4%를 복원해야 할 목표로도 쓰지 않는다.

```bash
python -m business_cycle.validation.phase4
```

2026-08-17 재검증에서 단계 A-2는 **미통과**다. 빈도 수정은 정상기 오탐을 크게 줄였지만
corrected baseline의 침체 재현율이 84.3%로 참고기준 85%에 못 미쳤고, 1985·1990 시작의
2001년 진입일 차이도 8주 기준을 만족하지 못했다. 설정을 동결하지 않았고 ALFRED도
시작하지 않았다. 측정값과 10개 차트는 `outputs/robustness_validation/phase4/`에 있다.

확인된 원인 두 가지를 함께 적어 둔다.

1. robust6은 단독으로는 해롭지 않다. 악화는 빈도 수정과의 상호작용에서 나온다.
   3년 추세를 원빈도에 적용하면 신호 절대값이 커지고 median/MAD 척도는 작아져 ±6
   제한이 훨씬 자주 걸린다.
2. 워밍업 의존성은 지표 전처리가 아니라 **좌표(X·Y) 표준화**에서 나온다. 지표 표준화에는
   5년 최소 이력 규칙이 있지만 좌표 표준화에는 26주뿐이었다. 그래서 1990 시작 실행은
   조용한 5년 표본으로 계산한 척도 0.25로 같은 합성요인을 Y = -4까지 부풀렸다.
   `coordinates(minimum_history_weeks=...)`로 최소 이력을 요구하면 차이가 44주에서
   21주로, 완전성숙 후 국면 불일치가 11.3%에서 2.4%로 줄어든다.

이 모델의 총 워밍업 요구는 지표 표준화 최소 이력 5년과 좌표 표준화 창 10년을 합쳐 약
15년이다. 1990년에 시작한 실행은 2005년에야 완전 성숙하므로 2001년 판정은 미성숙
판정이며, `corrected_baseline_full_maturity`는 그 경우 진입일을 아예 내지 않는다.

## 합성 데모 실측

seed 42, 1985~2026 합성자료의 `2026-08-14` 출력은 **회복기 말기**, 대국면 확실성
약 85점, 데이터 신뢰도 약 80점이었습니다. 1·2순위 차이가 약 1%p이고 원점에 가까워
세부 확실성은 낮았습니다. 이 값은 파이프라인 동작 확인용일 뿐 미국 경제에 대한 실제
판정이 아닙니다.

1995~2026 합성 백테스트는 1,650주, 평균 국면 지속 약 4.1주, 합성·동적요인 상관 약
0.98이었습니다. 합성 순환은 실제 NBER 날짜와 맞추지 않았으므로 NBER 성능 수치는 모델
성능으로 해석하지 않습니다. 실제 FRED 공통기간 백테스트가 다음 검증 단계입니다.

## v0.1 한계

- 최신 수정치 FRED 자료이며 real-time vintage 자료가 아닙니다.
- 실제 발표일이 없으면 설정 지연일로 추정합니다.
- 동적요인은 1차원 공통 loading의 국소선형추세 필터이며 계열별 loading 재추정은
  experimental 후속 범위입니다.
- 각도 경계·전이행렬·확실성 계수는 calibration 전 초기값입니다.
- 13주 선행확률은 `not_calibrated`입니다.
- SLOOS·ADS·WEI·OECD CLI·GDP·GDI는 외부 검증 인터페이스의 후속 범위입니다.
- 시장 선행·심리·산업 밸류에이션은 데이터 없이 생성하지 않았습니다.
- 실제 FRED 전기간의 역사적 사례별 경제적 검토는 아직 수행하지 않았습니다.

## 향후 개발 순서

1. ALFRED 빈티지 데이터 연결
2. 발표일 캘린더 정교화
3. 동적요인모형 loading·분산 재추정 개선
4. 실시간 빈티지 walk-forward 백테스트
5. 12개 각도 경계 보정
6. 상태전이행렬 보정
7. 확실성 점수 calibration
8. 13주 선행확률 calibration
9. 시장 선행 레이어 추가
10. 산업 밸류에이션 데이터 확보
11. 기존 자산관리 서비스 UI와의 API 연결
