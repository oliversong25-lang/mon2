# 모델 데이터 디렉터리

- `raw/`: 원본 다운로드. Git에서 제외합니다.
- `processed/`: 정규화된 관측 스키마와 중간 산출물. Git에서 제외합니다.
- `cache/`: FRED 응답 캐시. 수집 실패 시 기존 캐시를 사용할 수 있습니다.
- `sample/`: 재배포 가능한 합성 fixture만 추적합니다.

관측 스키마는 `indicator_id, observation_period, value, release_date, vintage_date,
fetched_at, source, revision_status, freshness_score`입니다. FRED 최신 수정치 CSV에는 당시
발표일과 빈티지가 없으므로 `release_date`는 설정된 보수적 발표 지연으로 추정하고
`vintage_date`는 비워 둡니다. 따라서 해당 결과는 real-time vintage backtest가 아닙니다.
