# 자산 입력 화면

브라우저 보안 정책 때문에 `asset-input.html`을 `file://`로 직접 열면 `data/tickers-*.json` 로드가 차단될 수 있습니다. 저장소 루트에서 로컬 서버를 실행한 뒤 접속하세요.

```powershell
python -m http.server 4174 --bind 127.0.0.1
```

브라우저에서 `http://127.0.0.1:4174/asset-input.html`을 엽니다.

종목 데이터는 앱 런타임과 분리된 1회성 빌드로 갱신합니다.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-tickers.txt
$env:DATA_GO_KR_KEY="..."; $env:DART_API_KEY="..."; node scripts/build-tickers.mjs
```

생성 파일은 `data/tickers-kr.json`, `data/tickers-us.json`, `data/tickers-global.json`입니다. 국내 주식 목록은 KIND(한국거래소) 페이지를 우선 사용하고, 너무 적게 나오면 공공데이터포털 금융위원회_KRX상장종목정보로 재시도합니다. 국내 ETF·ETN은 금융위원회_증권상품시세정보에서 받습니다(예전엔 `pykrx`·네이버 금융 스크래핑을 썼습니다 — 출처 신뢰성 때문에 공식 API로 교체했습니다). `DART_API_KEY`가 있으면 OPEN DART(금융감독원)에서 종목별 업종을 함께 채워 넣습니다 — 없어도 빌드는 진행되고 업종만 비어 있습니다. 빌드는 한국 2,500건, 미국 5,000건, 글로벌 1,000건의 최소 건수를 검증하고 스코프별 전체 건수와 ETF·ETN 건수를 출력합니다. 수집 실패나 최소 건수 미달은 종료 코드 `1`과 콘솔 오류로 표시됩니다.

이 빌드는 `.github/workflows/tickers.yml`이 **매주 일요일 05:00 KST**에 자동 실행합니다. 매일 도는 시세 배치와 일부러 분리해 뒀습니다 — 종목 목록 갱신은 DART 업종 조회만 2,700회가 넘어 훨씬 잘 깨지는데, 한 잡에 묶으면 업종 조회가 실패한 날 시세까지 멈춥니다.

### 업종 (KSIC)

DART 기업개황의 `induty_code`는 한국표준산업분류(KSIC) 코드를 3~5자리로 줍니다(삼성전자 `264`, 현대차 `30121`, NAVER `63120`). 세분류라 그대로 쓰면 도넛 조각이 수백 개로 쪼개지고, 숫자를 화면에 띄울 수도 없습니다. 그래서 종목마다 세 필드를 저장합니다.

| 필드 | 예시 | 용도 |
|---|---|---|
| `sec` | `"264"` | DART 원본 세분류 코드. 상세 화면·재분류용으로 보존 |
| `secDiv` | `"26"` | 앞 2자리 중분류 코드 |
| `secDivName` | `"전자·통신장비"` | 중분류 한글명. 홈 화면 산업 축(도넛)이 쓰는 값 |

중분류 표는 `scripts/lib/ksic.mjs`에 있습니다(KSIC 10차 기준, 전 중분류 수록 — 실측 데이터의 348개 세분류 코드가 전부 알려진 중분류로 풀립니다). **세분류(3~5자리) 한글명은 싣지 않았습니다** — 롱테일이라 상위 45개 코드가 절반도 덮지 못하는데, 공식 분류표 없이 손으로 적으면 틀린 업종명을 그럴듯하게 박아 넣게 됩니다. 세분류 이름이 필요해지면 통계청 한국표준산업분류 표를 받아 `ksic.mjs`에 추가하고 `resolveSector`가 그걸 우선 쓰게 하면 됩니다(호출부는 그대로 둬도 됩니다).

## 국내 시세 배치 (data/quotes.json)

국내 주식·ETF·ETN 시세, 금·환율(및 있으면 은)을 매일 1회 받아 `data/quotes.json`을 굽습니다. `.github/workflows/quotes.yml`이 매일 06:00 KST에 자동 실행하고, 변경분이 있을 때만 커밋합니다. 앱은 이 정적 파일을 fetch만 할 뿐 API 키를 전혀 갖고 있지 않습니다.

```powershell
$env:DATA_GO_KR_KEY="..."; $env:KOREAEXIM_AUTH_KEY="..."; node scripts/build-quotes.mjs
```

**필요한 키 4곳** (전부 무료 · 제가 대신 발급받을 수 없어 직접 신청해야 합니다):

| 환경변수 | 발급처 | 용도 |
|---|---|---|
| `DATA_GO_KR_KEY` | [data.go.kr](https://www.data.go.kr) — 금융위원회_주식시세정보(15094808)·증권상품시세정보(15094806)·일반상품시세정보(15094805)·KRX상장종목정보(15094775) 활용신청 | 국내 주식·ETF·ETN·금 시세, 종목 목록 |
| `KOREAEXIM_AUTH_KEY` | [한국수출입은행 Open API](https://www.koreaexim.go.kr/ir/HPHKIR020M01) | 환율 |
| `DART_API_KEY` | [OPEN DART](https://opendart.fss.or.kr) | 종목 업종 (선택 — 없으면 업종 없이 진행). `build-tickers.mjs`만 씁니다 |
| `ECOS_AUTH_KEY` + `ECOS_SILVER_STAT_CODE` | [한국은행 ECOS](https://ecos.bok.or.kr/api) | 은 시세 (선택 — 통계표코드는 키 발급 후 `StatisticItemList`로 확인 필요, 없으면 은 없이 진행) |

GitHub Actions에서 쓰려면 위 값들을 저장소 Settings → Secrets and variables → Actions에 같은 이름으로 등록하세요. 워크플로별로 쓰는 키가 다릅니다.

| 워크플로 | 주기 | 쓰는 Secret |
|---|---|---|
| `quotes.yml` (시세) | 매일 06:00 KST | `DATA_GO_KR_KEY`, `KOREAEXIM_AUTH_KEY`, `ECOS_AUTH_KEY`, `ECOS_SILVER_STAT_CODE` |
| `tickers.yml` (종목 목록·업종) | 매주 일요일 05:00 KST | `DATA_GO_KR_KEY`, `DART_API_KEY` |

`quotes.json` 형식:
```json
{
  "asOf": "2026-08-06T00:00:00+09:00",
  "sources": { "equity": "금융위원회_주식시세정보", "fx": "한국수출입은행" },
  "quotes": { "005930": { "price": 73400, "currency": "KRW" } },
  "rates": { "USD": 1380.5 },
  "commodities": { "goldPerGram": 151200 }
}
```
`asOf`는 배치 실행 시각이 아니라 실제 시세 기준일입니다(환율 조회는 주식 기준일부터 역순으로 탐색해서 시작하고, 그래도 갈리면 더 이른 쪽을 씀). 시세를 못 받은 종목은 앱에서 "시세 확인 불가"로 명시되고 평가금액 계산에서 빠집니다 — 조용히 사라지지 않습니다.

### 종목코드 형식

`data.go.kr`의 두 엔드포인트는 같은 종목에 다른 코드 형식을 씁니다 — KRX상장종목정보는 `srtnCd`에 `A` 접두어를 붙이고(`"A900110"`), 주식시세정보는 안 붙입니다(`"900110"`). `lib/data-go-kr.mjs`의 `normalizeKrCode(srtnCd, isinCd)`가 모든 코드 추출 지점에서 이걸 흡수합니다 — 표준 국내 ISIN(`KR7` + 6자리 + 검사숫자)이면 ISIN에서 6자리를 복원하고, 아니면(외국적 상장사 등) 접두어만 제거합니다. `data/tickers-*.json`이나 사용자 자산 레코드에 `A` 접두어가 남아있으면 안 됩니다.

### 산출물 검증

`build-quotes.mjs`는 쓰기 전에 검증하고, 실패하면 **기존 `data/quotes.json`을 건드리지 않습니다**(임시 파일에 먼저 쓰고 통과해야 원자적으로 교체):

- 실패(종료 코드 1, 기존 파일 보존): 주식·ETF·ETN **각각의** 시세 매칭률 90% 미만, 환율 통화 10개 미만, 금 시세 누락, `asOf`가 7일 이상 과거, 전체 시세 3,000건 미만
- 경고(로그만, 게시는 진행): 직전 산출물 대비 시세 건수 ±30% 초과 변동, 종목 목록에 없는 시세 코드 300건 초과

매칭률의 분자는 **종목 목록과 시세 코드의 교집합**입니다. 시세 API가 돌려준 행 수를 그대로 분자로 쓰면 목록에 없는 코드까지 세어 100%를 넘고(실측 104.1%), 그보다 나쁜 건 코드 조인이 깨져 교집합이 0이 돼도 행 수는 그대로라 검증이 통과해 버린다는 점입니다 — 접두어 버그를 잡으려고 넣은 검증이 정확히 그 버그를 통과시켰습니다. 이 케이스는 `test-quotes-mock.mjs`에 회귀 테스트로 고정돼 있습니다.

`build-tickers.mjs`는 DART 업종 매칭률이 **80% 미만이면 실패**합니다(예전엔 경고만 하고 진행 — 그래서 63%가 비어 있는 산출물이 "성공"으로 커밋됐습니다). 미확보분은 `corpCode 없음`과 `호출 실패`로 나눠 출력하므로 원인을 바로 구분할 수 있습니다.

DART는 분당 호출 한도가 있고, 넘기면 이후 요청을 전부 `020`으로 돌려줍니다. 예전 코드는 `status !== "000"`을 전부 null로 뭉개서 이 차단을 "업종 없는 종목"과 구별하지 못했고, 그 결과 2,758건 중 **정확히 앞 1,010건만** 업종이 붙고 나머지가 통째로 비었습니다. 지금은 `lib/dart.mjs`가 분당 900회로 스스로 유량을 제한하고, `013`(데이터 없음)만 정상적인 빈 결과로 취급하며, `020`은 재시도 후 실패로 보고하고, 인증키 오류(`010`·`011`·`012`·`901`)는 즉시 예외로 올립니다.

## 회귀 테스트

Playwright로 실제 클릭·키보드 입력을 재현해 검증합니다 (값을 JS로 주입하는 방식은 통과 근거로 쓰지 않습니다).

```powershell
npm install
npx playwright install chromium
npm test
```

각각 자체 정적 서버를 띄우는 독립 스크립트입니다. 개별 실행도 가능합니다.

- `npm run test:focus` — 렌더가 돌 때 포커스 중인 입력 필드를 잃지 않는지(커서 이탈 방지). 8개 자산군의 금액·수량 필드 클릭 후 포커스 유지, 연속 타이핑, 통화·시·도 변경 후 값 보존, 검토 화면 새로고침 유지 등.
- `npm run test:numeric` — `numeric:true` 필드(수량·지분율·금리 등)에 문자·한글이 섞여 들어가지 않는지. beforeinput 정제, 붙여넣기, 한글 IME 조합 커밋, 자산군별 소수점 자릿수 제한, 지분율 0~100 clamp.
- `npm run test:groups` — 펀드·채권·원자재·부동산 4개 자산군의 실클릭 E2E. 필드별 클릭·타이핑·삭제·재입력, 복수 등록·중복 방지, 통화 전환, 원자재 종류 전환, 시·도 연동, 건너뛰기/이전/이어하기, 새로고침 보존, 최종 검토 화면, 모바일 뷰포트.
- `npm run test:quotes-integration` — `data/quotes.json`이 실제로 화면에 반영되는지. MOCK에 한 번도 없던 종목(카카오)을 실제 검색·등록해 평가금액이 정상 계산되는지, 시세 없는 종목(현대차)은 "확인 불가"로 명시되고 조용히 사라지지 않는지, 기준일 표기가 `asOf` 기반인지, `quotes.json` 자체가 없어도(배치 실패/최초 상태) 앱이 죽지 않는지 확인합니다. 실제 커밋된 `data/tickers-kr.json`을 그대로 쓰고 테스트용 `quotes.json`만 임시로 얹었다 지웁니다.
- `npm run test:quotes-mock` / `npm run test:tickers-mock` — `build-quotes.mjs`/`build-tickers.mjs`의 페이징·기준일 탐색·필드 매핑·DART corpCode zip 파싱 로직을 실제 API 키 없이 mock fetch로 검증합니다. **실제 공공데이터 API 응답 필드명 자체를 검증하지는 않습니다** — 그건 실제 키로 첫 배치를 돌려봐야 확인됩니다(`build-quotes.mjs`/`build-tickers.mjs`는 `[raw-sample]`로 각 응답의 원본 첫 행을 콘솔에 출력합니다).
