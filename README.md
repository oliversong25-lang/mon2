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
