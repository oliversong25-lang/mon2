# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트

AssetFlow — 개인 자산관리 웹앱. `oliversong25-lang/mon2`, GitHub Pages 배포(`monasset.kr`).
사용자와 문서는 한국어입니다. 커밋 메시지·주석·화면 문구 모두 한국어로 씁니다.

**주의: `C:\Users\olive`는 전혀 다른 저장소입니다**(태안 유출유 프로젝트). 세션 시작 cwd가
그쪽일 수 있으니 git 명령 전에 `cd C:/dev/mon2`를 붙이세요.

## 명령

```bash
npm test                      # 전체 스위트 (8개, 약 283건, 10~15분)
npm run test:home             # 홈·자산·경제지표 화면 (가장 큼)
npm run test:quotes-mock      # 시세 배치 로직 (mock fetch)
npm run test:indicators-mock  # 지표 배치 로직 (mock fetch)
npm run test:rls              # 실제 Supabase 두 계정 — 환경변수 없으면 실패로 끝난다
node scripts/build-quotes.mjs # 배치 단독 실행 (아래 키 필요)
```

로컬 확인은 정적 서버로 합니다. `file://`로 열면 `data/*.json` 로드가 차단됩니다.

```bash
python -m http.server 4174 --bind 127.0.0.1
```

배치를 돌리려면 환경변수가 필요합니다(대화에 붙여넣지 말고 셸에만 두세요):
`DATA_GO_KR_KEY`(디코딩 키) · `KOREAEXIM_AUTH_KEY` · `COINGECKO_API_KEY` · `ECOS_AUTH_KEY`.

## 깨면 안 되는 제약

- **빌드 스텝이 없습니다.** 순수 정적 HTML/CSS/JS. 번들러·프레임워크·트랜스파일러를 들이지
  마세요. Pages가 저장소 루트를 그대로 서빙합니다.
- **`lib/*.js`는 클래식 스크립트이고 전역으로 노출합니다**(`window.Valuation`·`Portfolio`·
  `SessionStore`·`Shell`·`Format`·`Indicators`·`DataFetch`). ES 모듈로 바꾸면
  `asset-input.html`이 의존하는 전역 스코프와 회귀 테스트의 `page.evaluate` 경로가 함께
  깨집니다. 반대로 `scripts/*.mjs`(배치)는 ESM입니다.
- **경로는 전부 상대 경로.** 절대 경로(`/data/...`)는 하위 경로 배포(`/mon2/`)에서만
  깨져 로컬에서는 안 보입니다.
- **줄바꿈은 LF**(`.gitattributes`).
- **`lib/valuation.js`가 평가금액의 유일한 출처.** 화면마다 평가 로직을 따로 두면 같은
  자산이 화면마다 다른 금액으로 보입니다.

## 관통하는 원칙: 실패가 성공처럼 보이면 안 된다

이 저장소의 설계 판단 대부분이 여기서 나옵니다. 새 코드도 이 규칙을 따라야 합니다.

- 조용한 0, 빈 화면, 뭉뚱그린 "없습니다" 금지. **모르면 `null`을 반환하고 화면이 이유를
  말합니다.** 환율을 모를 때 `|| 1`로 넘기면 USD 2,000이 2,000원이 됩니다.
- **데이터가 없어도 HTTP 200을 주는 API가 여럿입니다**(CoinGecko·OECD SDMX·DART·ECOS·
  공공데이터포털). 상태 코드만 보면 성공으로 읽히므로 0건을 성공으로 넘기지 마세요.
- **API 필드명을 추측하지 마세요.** 틀리면 예외가 아니라 조용한 0으로 끝납니다. 실제
  응답이나 코드리스트를 보고 고르고, 가능하면 런타임 교차검증을 겁니다 —
  `build-quotes.mjs`가 `vs / (clpr - vs) * 100 ≈ fltRt` 항등식을 매 실행 검사하는 것이 예입니다.
- **검증할 수 없는 값은 지어내지 않고 제품 안에서 공백을 드러냅니다**(KSIC 세분류명,
  금통위 2027 일정, 규칙으로 잡은 ISM 예정일의 `dateBasis: "rule"` 표시).
- 배치 산출물은 임시 파일에 쓰고 검증을 통과해야 원자적으로 교체합니다. 실패하면 기존
  파일을 보존합니다.

## 구조

```
*.html              화면 (index · login · asset-input · home · assets · indicators · settings)
lib/*.js            브라우저 런타임 (클래식 스크립트 · 전역)
scripts/*.mjs       배치와 회귀 테스트 (ESM · Node 20)
scripts/lib/*.mjs   배치가 쓰는 외부 API 클라이언트
data/**.json        배치 산출물. 앱은 fetch만 한다
```

**앱은 런타임 백엔드가 없습니다.** GitHub Actions 배치가 `data/` 아래 정적 JSON을 굽고
커밋하면, 브라우저가 그것만 읽습니다. API 키는 Actions Secrets에만 있고 클라이언트에
들어가지 않습니다.

| 워크플로 | 주기 | 산출물 |
|---|---|---|
| `quotes.yml` | 14:17 / 18:43 KST | `data/quotes.json` |
| `daily-rates.yml` | 17:23 / 21:37 KST | `data/indicators/index-daily.json` · `latest/rates.json` · `daily/*` |
| `indicators.yml` | 15:00 KST | `data/indicators/index.json` · `latest/*` · `oecd/*` |
| `tickers.yml` | 일요일 05:00 KST | `data/tickers-*.json` |
| `pages.yml` | 위 넷 완료 시 + push | 배포 |

### 배치를 건드릴 때 반드시 지킬 것

- **예약 슬롯은 정각을 피합니다.** 정각이 가장 붐벼 실행이 75분 밀린 적이 있습니다. 각
  배치는 두 번째 슬롯을 갖고, 이미 최신이면 아무것도 커밋하지 않습니다.
- **결과는 셋입니다: `updated` / `already-current` / `failed`.** 뭉치면 아무 일도 안 한
  실행이 성공으로 읽힙니다. 워크플로가 실행 요약에 한 줄로 찍습니다.
- **한 출처가 죽어도 나머지는 갱신합니다.** 실패는 엔드포인트 이름과 함께 산출물의
  `failures`와 로그에 남고, 수집 0건이면 쓰지 않고 기존 파일을 보존합니다.
- **`GITHUB_TOKEN` 커밋은 다른 워크플로를 트리거하지 않습니다.** 그래서 배포는 `push`가
  아니라 `workflow_run`으로 걸려 있습니다. 새 배치를 더하면 `pages.yml`의 목록에도 넣으세요.
- **자체 유량 제한이 필요한 출처가 있습니다.** OECD는 호출 간격 8초(`OECD_MIN_GAP_MS`로
  끌 수 있음 — 테스트가 그렇게 씁니다), DART는 분당 900회로 스스로 조입니다.

### 데이터 캐시

앱이 받는 데이터 파일은 전부 `lib/fetch-data.js`를 지납니다. **매일 바뀌는 파일은
`cache: "no-cache"`로 매번 서버에 확인**하고(그대로면 304), 주 1회 바뀌는 종목 목록
(4MB)만 기본 캐시를 씁니다. 빌드 스텝이 없어 `?v=` 버전 문자열은 데이터 갱신을 따라갈 수
없습니다. 배치가 커밋·배포까지 성공했는데 브라우저가 어제 파일을 들고 있어 며칠을 헤맨
적이 있습니다.

### 경제지표 카탈로그

인덱스가 **둘**입니다(`index.json` OECD 월·분기, `index-daily.json` 일간 금리). 배치가
서로의 산출물을 덮어쓰지 않게 나눈 것이고, 화면(`lib/indicators-view.js`)이 합쳐 읽습니다.
합칠 때 `headlineSeries`를 빠뜨리면 홈 지표 카드가 통째로 비고, `periodToMonths`가 새
주기를 모르면 전 계열이 "오래됨"으로 판정됩니다 — 둘 다 실제로 겪었습니다.

계열 id는 `<출처>:<지표>:<국가>`입니다. 출처는 별도 시스템이 아니라 필드이므로, 새 출처는
같은 모양의 배열을 이어 붙이면 들어옵니다.

## 회귀 테스트

Playwright로 **실제 클릭·타이핑을 재현**해 검증합니다. 값을 JS로 주입하는 방식은 통과
근거로 쓰지 않습니다. 새 화면 동작을 더하면 같은 방식으로 테스트를 답니다.

여기서 실제로 데이터를 잃거나 거짓 통과를 겪은 것들 — 새 테스트도 같은 함정을 피해야 합니다.

- **테스트는 커밋된 실데이터 위에서 돕니다.** route 핸들러의 미포착 예외가 `finally`보다
  먼저 프로세스를 죽여 4,402건짜리 `quotes.json`이 2건짜리 픽스처로 남은 적이 있습니다.
  `process.on("exit")`·`uncaughtException`·`unhandledRejection`에 동기 복원 훅을 답니다.
- **배치 완료를 고정 시간으로 기다리지 마세요.** `globalThis.__quotesBatchRuns` 같은 완료
  카운터를 폴링합니다. 재시도가 붙은 실패 경로는 정리 코드보다 늦게 끝납니다.
- **`localStorage.clear()`는 깨끗한 시작이 아닙니다.** Supabase 인증 이후 mock remote가
  `sessionStorage`에 남습니다. `scripts/lib/test-auth.mjs`의 헬퍼를 쓰세요.
- **locator를 잡아 두고 나중에 읽지 마세요.** 비동기 재렌더로 노드가 떨어져 나가 계산
  스타일이 빈 문자열로 나옵니다. 조회와 읽기를 한 틱 안에서 합니다.
- **높이 검사에 `scrollHeight`를 쓰지 마세요.** 이미 늘어난 값이라 행 높이와 늘 같아,
  카드가 행을 밀어 올려도 통과합니다. `alignSelf`를 잠깐 풀고 내용 높이를 잽니다.
- 도넛 조각 클릭은 **3시 방향**에서 합니다. `fill="none"`이라 칠해진 호만 히트되고
  12시에서는 마지막 조각이 첫 조각을 덮습니다.

## 화면 디자인

`lib/app.css`가 토큰의 단일 출처입니다. 자세한 근거는 README의 「화면 디자인」에 있습니다.

- **강조색을 쓰지 않습니다.** 예전 `--brand`가 하락색과 ΔE 6.1로 사실상 같은 색이라
  버튼·포커스가 "하락"과 구별되지 않았습니다. 조작 가능한 것은 무채색 본지(`--action`),
  **색을 가진 것은 상승(빨강)·하락(파랑)과 구성 6색뿐**입니다. 국내 관례대로 상승이 빨강.
- 구성 차트에 좋고 나쁨을 뜻하는 색을 쓰지 않습니다. 도넛 팔레트는 색각 이상 검증을
  거쳤으므로 순서나 색을 바꾸면 다시 검증해야 합니다.
- 활자 세 역할: Hahmlet(명조) 표제·금액 · Pretendard 본문 · IBM Plex Mono 기준일·코드.
  폰트는 `@import`가 아니라 각 화면 `<head>`에서 `media="print"` 뒤바꾸기로 비차단 로드합니다
  (`@import`가 렌더를 막아 로딩 회귀 테스트가 거짓 통과한 적이 있습니다).
- **격자 칸에 `min-width:0`이 필요합니다.** 없으면 열 8개짜리 표가 칸을 밀어내 선언한
  비율이 무너지고 좁은 폭에서 페이지가 가로로 밀립니다.
- 점수·게이지는 만점이 있는 것처럼 보이면 안 됩니다. `/100`도, 꽉 차는 원형 진행도 금지 —
  합의된 형태는 양 끝에 `높음`/`낮음`이 붙은 선입니다.
- 자산 분류는 **원금 보장 여부**이지 안전/위험이 아닙니다.

## 문서 갱신

여러 도구(Claude Code · Codex)가 번갈아 이 저장소를 만집니다. 대화 컨텍스트는 도구를
넘어가지 않으므로 저장소 문서가 유일한 인수인계 경로입니다.

- **`README.md`** — 설치·실행·구조와 **왜 이 구조인지**. 오래 유지되는 내용.
- **`WORKLOG.md`** — 날짜별 변경·검증·남은 일. 자체 형식이 있고 최신이 맨 위입니다.
  구조나 판단이 필요한 결정을 바꿨으면 같은 커밋에서 갱신합니다.

## 규제 경계

`다가오는 일정` 카드의 시나리오 문구에는 **매수·매도·비중 조정 지시, 읽는 사람의 보유
자산 언급, 어느 쪽이 될지에 대한 예측이나 확률을 넣지 않습니다.** 일반적으로 알려진 파급
경로만 서술합니다. 이 금지 표현은 생성기(`scripts/build-macro-calendar.mjs`)와 회귀
테스트가 양쪽에서 검사합니다.
