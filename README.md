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
node scripts/build-tickers.mjs
```

생성 파일은 `data/tickers-kr.json`, `data/tickers-us.json`, `data/tickers-global.json`입니다. 국내 ETF·ETN은 `pykrx`를 우선 사용하고, KRX 인증이 필요한 환경에서는 공개 시세 목록으로 재시도합니다. 빌드는 한국 2,500건, 미국 5,000건, 글로벌 1,000건의 최소 건수를 검증하고 스코프별 전체 건수와 ETF·ETN 건수를 출력합니다. 수집 실패나 최소 건수 미달은 종료 코드 `1`과 콘솔 오류로 표시됩니다.

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
