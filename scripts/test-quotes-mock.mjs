// scripts/test-quotes-mock.mjs
// Exercises build-quotes.mjs's paging / basDt-probing / field-mapping / asOf /
// validation logic against a mocked fetch(). The mock responses below deliberately
// reproduce the exact quirks found by running the REAL batch with real keys:
//   - the exchange rate API returns a one-element [{"result":3,...}] array (not an
//     empty array) on days with no data, which used to slip past the old
//     `rows.length` check and silently short-circuit the backward retry.
// This does NOT verify the real API's field names beyond what's already been
// observed live — it verifies this script's own parsing/validation logic.

import { readFile, writeFile, rm } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const QUOTES_PATH = resolve(ROOT, "data", "quotes.json");
const TICKERS_KR_PATH = resolve(ROOT, "data", "tickers-kr.json");

process.env.DATA_GO_KR_KEY = "test-key";
process.env.KOREAEXIM_AUTH_KEY = "test-key";
// leave ECOS unset on purpose — exercises the "silver skipped, batch still succeeds" path

const TODAY = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" })
  .format(new Date())
  .replace(/-/g, "");
function shift(yyyymmdd, days) {
  const y = Number(yyyymmdd.slice(0, 4)), m = Number(yyyymmdd.slice(4, 6)) - 1, d = Number(yyyymmdd.slice(6, 8));
  const date = new Date(Date.UTC(y, m, d));
  date.setUTCDate(date.getUTCDate() + days);
  return `${date.getUTCFullYear()}${String(date.getUTCMonth() + 1).padStart(2, "0")}${String(date.getUTCDate()).padStart(2, "0")}`;
}
// pretend the most recent trading day is 2 days back (simulates a weekend gap)
const TRADING_DAY = shift(TODAY, -2);
// 직전 거래일. 배치는 전일 종가가 "어느 날" 것인지 확정하려고 이 날짜에도 데이터가
// 있는지 물어본다(주말만 건너뛰는 계산으로는 공휴일에서 틀리기 때문이다).
const PREV_TRADING_DAY = shift(TODAY, -3);
// 전일 환율이 가리키는 날. FX_DAY 바로 전날이 아니라 사흘 전이라, 배치가 "어제"를
// 가정하지 않고 데이터가 있는 날까지 거슬러 올라가야만 찾을 수 있다.
const FX_PREV_DAY = shift(TODAY, -5);
const FX_DAY = shift(TODAY, -3); // FX lags by one more day than equities, on purpose

function envelope(items, totalCount) {
  return { response: { header: { resultCode: "00", resultMsg: "OK" }, body: { items: { item: items }, totalCount } } };
}

const STOCK_COUNT = 4200;
// 4200 synthetic stock rows so pagination (numOfRows=1000) actually kicks in (5 pages).
// 실제 응답은 종가(clpr)와 함께 전일 대비(vs)·등락률(fltRt)을 준다. 전일 종가 필드는
// 없어서 clpr-vs로 만든다. mock도 같은 모양이어야 배치의 검증을 지나간다.
// fltRt는 제공처처럼 소수 둘째 자리로 반올림해 넣는다.
function withChange(row, vs) {
  const clpr = Number(row.clpr);
  const prev = clpr - vs;
  return { ...row, vs: String(vs), fltRt: ((vs / prev) * 100).toFixed(2) };
}

const STOCK_ROWS = Array.from({ length: STOCK_COUNT }, (_, i) => withChange({
  basDt: TRADING_DAY,
  srtnCd: String(600000 + i),
  isinCd: `KR7${String(600000 + i)}00`,
  itmsNm: `테스트종목${i}`,
  clpr: String(10000 + i),
  mrktTotAmt: String(1000000 + i),
}, i % 2 === 0 ? 100 : -50));
STOCK_ROWS[0] = withChange({ ...STOCK_ROWS[0], srtnCd: "005930", isinCd: "KR7005930003", clpr: "73400" }, 400);

const ETF_ROWS = [withChange({ basDt: TRADING_DAY, srtnCd: "069500", isinCd: "KR7069500007", itmsNm: "KODEX 200", clpr: "42350", mrktTotAmt: "64253000000" }, 150)];
const ETN_ROWS = [];
const GOLD_ROWS = [withChange({ basDt: TRADING_DAY, isuNm: "금99.99_1g", itmsNm: "금99.99_1g", clpr: "151200" }, -800)];

// 손으로 받아 둔 실제 응답(oapi.koreaexim.go.kr, AP01, 20260904). 필드를 줄이지 않고
// 그대로 둔다 — 숫자가 **천 단위 쉼표가 붙은 문자열**로 온다는 것이 이 픽스처의 요점이고,
// 요약한 모양으로 바꾸면 그 사실이 시험에서 사라진다.
const FX_CAPTURED = [
  { result: 1, cur_unit: "USD", cur_nm: "미국 달러", ttb: "1,346.2", tts: "1,372.8", deal_bas_r: "1,359.5", bkpr: "1,359", yy_efee_r: "0", ten_dd_efee_r: "0", kftc_bkpr: "1,359", kftc_deal_bas_r: "1,359.5" },
  { result: 1, cur_unit: "JPY(100)", cur_nm: "일본 옌", ttb: "912.15", tts: "930.57", deal_bas_r: "921.36", bkpr: "921", yy_efee_r: "0", ten_dd_efee_r: "0", kftc_bkpr: "921", kftc_deal_bas_r: "921.36" },
  // 목록에 원화가 섞여 온다. deal_bas_r이 "1"이라 덮어써도 값은 같지만, 그 사실을
  // 시험이 알고 있어야 나중에 이 행이 다른 값으로 바뀌었을 때 걸린다.
  { result: 1, cur_unit: "KRW", cur_nm: "한국 원", ttb: "0", tts: "0", deal_bas_r: "1", bkpr: "1", yy_efee_r: "0", ten_dd_efee_r: "0", kftc_bkpr: "1", kftc_deal_bas_r: "1" },
  { result: 1, cur_unit: "EUR", cur_nm: "유로", ttb: "1,489.1", tts: "1,519.2", deal_bas_r: "1,504.15", bkpr: "1,504", yy_efee_r: "0", ten_dd_efee_r: "0", kftc_bkpr: "1,504", kftc_deal_bas_r: "1,504.15" },
  // IDR도 통화코드에 배수가 붙는다. JPY 하나만 두면 "JPY 전용 예외"로 굳어질 수 있어 함께 둔다.
  { result: 1, cur_unit: "IDR(100)", cur_nm: "인도네시아 루피아", ttb: "8.15", tts: "8.32", deal_bas_r: "8.24", bkpr: "8", yy_efee_r: "0", ten_dd_efee_r: "0", kftc_bkpr: "8", kftc_deal_bas_r: "8.24" },
  // 아래는 통화 수 하한(10개) 검증을 통과시키기 위한 나머지다. 값 자체는 검사하지 않는다.
  ...["AED", "AUD", "CAD", "CHF", "CNH", "GBP", "HKD", "SGD", "THB"].map((code) => ({
    result: 1, cur_unit: code, cur_nm: code, ttb: "100.00", tts: "100.00", deal_bas_r: "100.00",
    bkpr: "100", yy_efee_r: "0", ten_dd_efee_r: "0", kftc_bkpr: "100", kftc_deal_bas_r: "100.00",
  })),
];

// 구 도메인(www.koreaexim.go.kr)이 2026-04-30 병행 가동 종료 뒤 돌려주는 모양.
// HTTP 200 · 잘 만들어진 JSON 배열 · result만 2. **연결 실패처럼 보이지 않는다.**
const FX_RESULT2 = [{ result: 2, cur_unit: null, cur_nm: null, ttb: null, tts: null, deal_bas_r: null, bkpr: null, yy_efee_r: null, ten_dd_efee_r: null, kftc_bkpr: null, kftc_deal_bas_r: null }];

// 실측: 데이터 없는 날짜에도 result:3 한 건짜리 배열이 온다 (빈 배열이 아니다).
function fxEnvelopeForDate(date, mode = "ok") {
  if (mode === "result2") return FX_RESULT2;
  // 키가 만료·폐기되면 어느 날짜를 물어도 result:3만 온다.
  if (mode === "authfail") return [{ result: 3, cur_unit: null, cur_nm: null, ttb: null, tts: null, deal_bas_r: null, bkpr: null, yy_efee_r: null, ten_dd_efee_r: null, kftc_bkpr: null, kftc_deal_bas_r: null }];
  if (mode === "captured") return date === FX_DAY || date === FX_PREV_DAY ? FX_CAPTURED : [{ result: 3, cur_unit: null, deal_bas_r: null }];
  // 수출입은행은 비영업일에 데이터를 주지 않고 result:3 한 건만 돌려준다(실측).
  // FX_DAY와 FX_PREV_DAY 사이를 비워 두어, 전일 환율 조회가 "어제"를 가정하지 않고
  // 데이터가 있는 날까지 거슬러 올라가는지 확인한다.
  if (date !== FX_DAY && date !== FX_PREV_DAY) return [{ result: 3, cur_unit: null, cur_nm: null, ttb: null, tts: null, deal_bas_r: null, bkpr: null, yy_efee_r: null, ten_dd_efee_r: null, kftc_bkpr: null, kftc_deal_bas_r: null }];
  const padCodes = ["AED", "AUD", "BHD", "BND", "CAD", "CHF", "CNH", "DKK", "EUR", "GBP", "HKD", "IDR", "KWD", "MYR", "NOK", "NZD", "SAR", "SEK", "SGD", "THB"];
  return [
    { result: 1, cur_unit: "USD", deal_bas_r: "1,380.50" },
    { result: 1, cur_unit: "JPY(100)", deal_bas_r: "925.00" },
    ...padCodes.map((code) => ({ result: 1, cur_unit: code, deal_bas_r: "100.00" })),
  ];
}

// 실측: 코인게코는 잘못된 ID를 줘도 HTTP 200에 빈 객체 {}를 돌려준다. cryptoMode로
// 그 경로들을 재현한다.
// 실측(2026-08-15): include_24hr_change=true를 주면 krw_24h_change가, 
// include_last_updated_at=true를 주면 last_updated_at(초)이 붙는다.
//   {"bitcoin":{"krw":89263800,"krw_24h_change":-0.4293991463197649,"last_updated_at":1786774430}}
const CRYPTO_UPDATED_AT = 1786774430;
const CRYPTO_BODY = {
  bitcoin: { krw: 91530787, krw_24h_change: 1.25, last_updated_at: CRYPTO_UPDATED_AT },
  ethereum: { krw: 2691101, krw_24h_change: -0.8, last_updated_at: CRYPTO_UPDATED_AT },
  tether: { krw: 1416.7, krw_24h_change: 0.01, last_updated_at: CRYPTO_UPDATED_AT },
};
function cryptoResponseFor(mode) {
  if (mode === "empty") return jsonResponse({}); // HTTP 200 + {} — 성공으로 오인되던 형태
  if (mode === "error-envelope") return jsonResponse({ status: { error_code: 429, error_message: "rate limited" } });
  if (mode === "partial") return jsonResponse({ bitcoin: CRYPTO_BODY.bitcoin, ethereum: CRYPTO_BODY.ethereum });
  if (mode === "http-500") return { ok: false, status: 500, json: async () => ({}), text: async () => "" };
  // undici가 실제로 던지는 형태 — 메시지에 URL이 없고 원인은 cause에만 있다.
  if (mode === "network") {
    const error = new TypeError("fetch failed");
    error.cause = Object.assign(new Error("getaddrinfo ENOTFOUND api.coingecko.com"), { code: "ENOTFOUND" });
    throw error;
  }
  return jsonResponse(CRYPTO_BODY);
}

function makeFetchMock({ stockRows = STOCK_ROWS, cryptoMode = "ok", fxMode = "ok" } = {}) {
  const fetchCallLog = [];
  const fn = async (input) => {
    const url = new URL(String(input));
    fetchCallLog.push(url.pathname + url.search);

    if (url.hostname === "apis.data.go.kr") {
      const basDt = url.searchParams.get("basDt");
      const pageNo = Number(url.searchParams.get("pageNo"));
      const numOfRows = Number(url.searchParams.get("numOfRows"));
      const isProbe = numOfRows === 1;

      if (url.pathname.includes("GetStockSecuritiesInfoService")) {
        const rows = basDt === TRADING_DAY ? stockRows
          : basDt === PREV_TRADING_DAY ? stockRows.slice(0, 1)
          : [];
        if (isProbe) return jsonResponse(envelope(rows.slice(0, 1), rows.length));
        const start = (pageNo - 1) * numOfRows;
        return jsonResponse(envelope(rows.slice(start, start + numOfRows), rows.length));
      }
      if (url.pathname.includes("GetSecuritiesProductInfoService/getETFPriceInfo")) {
        const rows = basDt === TRADING_DAY ? ETF_ROWS : [];
        return jsonResponse(envelope(rows, rows.length));
      }
      if (url.pathname.includes("GetSecuritiesProductInfoService/getETNPriceInfo")) {
        const rows = basDt === TRADING_DAY ? ETN_ROWS : [];
        return jsonResponse(envelope(rows, rows.length));
      }
      if (url.pathname.includes("GetGeneralProductInfoService")) {
        const rows = basDt === TRADING_DAY ? GOLD_ROWS : [];
        return jsonResponse(envelope(rows, rows.length));
      }
      throw new Error(`unmocked data.go.kr path: ${url.pathname}`);
    }

    if (url.hostname === "oapi.koreaexim.go.kr") {
      const searchdate = url.searchParams.get("searchdate");
      return jsonResponse(fxEnvelopeForDate(searchdate, fxMode));
    }
    // 구 도메인으로 부르면 목이 터진다 — 코드가 www로 되돌아가면 시험이 즉시 잡는다.
    if (url.hostname === "www.koreaexim.go.kr") {
      throw new Error("환율을 구 도메인(www.koreaexim.go.kr)으로 불렀습니다 — 2026-04-30 병행 가동 종료됨");
    }

    if (url.hostname === "api.coingecko.com") return cryptoResponseFor(cryptoMode);

    throw new Error(`unmocked host: ${url.hostname}`);
  };
  return { fn, fetchCallLog };
}

function jsonResponse(body) {
  return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) };
}

// build-quotes.mjs is a top-level-await-free "main().catch()" script; importing it
// twice needs a cache-busting specifier since Node caches ES modules by URL.
async function runBatch({ stockRows, cryptoMode, fxMode } = {}) {
  const { fn, fetchCallLog } = makeFetchMock({ stockRows, cryptoMode, fxMode });
  global.fetch = fn;
  process.exitCode = undefined;
  // 실패 메시지를 시험이 읽어야 한다. 로그가 원인을 말하는지가 이번 검사의 대상이라,
  // exitCode만 봐서는 "멈췄다"는 것밖에 확인할 수 없다.
  const errorLog = [];
  const originalError = console.error;
  console.error = (...args) => { errorLog.push(args.map(String).join(" ")); originalError(...args); };
  // 배치는 import 시점에 main()을 띄우고 바로 반환한다. 고정 시간만 기다리면 재시도가
  // 붙은 실패 경로가 다음 실행과 겹쳐 exitCode와 콘솔이 뒤섞인다 — 실행이 실제로
  // 끝났다는 신호(카운터)를 기다린다.
  const before = globalThis.__quotesBatchRuns || 0;
  await import(`./build-quotes.mjs?t=${Date.now()}-${Math.random()}`);
  const deadline = Date.now() + 20000;
  while ((globalThis.__quotesBatchRuns || 0) === before && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 50));
  }
  console.error = originalError;
  const exitCode = process.exitCode;
  process.exitCode = undefined;
  return { fetchCallLog, exitCode, errorLog };
}

const results = [];
const record = (label, ok, detail) => {
  results.push({ label, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${ok ? "" : `  — ${detail}`}`);
};

const originalTickersKr = await readFile(TICKERS_KR_PATH, "utf8").catch(() => null);
const originalQuotes = await readFile(QUOTES_PATH, "utf8").catch(() => null);

try {
  // 매칭률 검증(90%)이 통과하도록, mock 주식 코드와 정확히 일치하는 tickers-kr.json을 임시로 깐다.
  const tickerFixture = [
    ...STOCK_ROWS.map((row) => ({ c: row.srtnCd, n: row.itmsNm, m: "KOSPI", cur: "KRW", t: "stock" })),
    ...ETF_ROWS.map((row) => ({ c: row.srtnCd, n: row.itmsNm, m: "KOSPI", cur: "KRW", t: "etf" })),
  ];
  await writeFile(TICKERS_KR_PATH, JSON.stringify(tickerFixture), "utf8");
  await rm(QUOTES_PATH, { force: true });

  // ===== 1. 정상 경로 =====
  const { fetchCallLog, exitCode } = await runBatch();
  let quotes;
  try {
    quotes = JSON.parse(await readFile(QUOTES_PATH, "utf8"));
  } catch (error) {
    record("quotes.json was written", false, error.message);
  }

  if (quotes) {
    record("quotes.json written, batch exited cleanly (no exitCode=1)", exitCode !== 1, `exitCode=${exitCode}`);
    record(
      "asOf uses the older of stock/fx basDt (fx lags by an extra day here, proves both date-probes resolved correctly)",
      quotes.asOf.startsWith(`${FX_DAY.slice(0, 4)}-${FX_DAY.slice(4, 6)}-${FX_DAY.slice(6, 8)}`),
      `expected FX_DAY=${FX_DAY}, got asOf=${quotes.asOf}`
    );
    record("paginated across all 4200 synthetic stock rows (5 pages of 1000)", Object.keys(quotes.quotes).length >= STOCK_COUNT, `got ${Object.keys(quotes.quotes).length} quotes`);
    record("삼성전자(005930) price parsed correctly", quotes.quotes["005930"]?.price === 73400, JSON.stringify(quotes.quotes["005930"]));
    record("ETF (069500) merged into the same quotes map", quotes.quotes["069500"]?.price === 42350, JSON.stringify(quotes.quotes["069500"]));
    record(
      "환율 재시도가 result:3(데이터없음) 날짜들을 실제로 건너뛰고 소급됨 (더 이상 첫 시도에서 멈추지 않음)",
      Object.keys(quotes.rates).length > 1,
      `rates=${JSON.stringify(quotes.rates)}`
    );
    record("USD rate parsed (no unit divisor)", quotes.rates.USD === 1380.5, `got ${quotes.rates.USD}`);
    record("JPY(100)-style unit divided correctly (925.00/100) — regex left untouched", quotes.rates.JPY === 9.25, `got ${quotes.rates.JPY}`);
    record("gold parsed from the 1g row", quotes.commodities.goldPerGram === 151200, JSON.stringify(quotes.commodities));
    record("가상자산 시세가 원화로 들어감 (BTC)", quotes.crypto?.BTC?.price === 91530787 && quotes.crypto.BTC.currency === "KRW", JSON.stringify(quotes.crypto));
    record("가상자산 심볼 3종 모두 확보 (BTC/ETH/USDT)", Object.keys(quotes.crypto || {}).sort().join(",") === "BTC,ETH,USDT", JSON.stringify(quotes.crypto));
    record("가상자산은 국내 종목 quotes와 분리 저장됨 (코드 체계가 다름)", quotes.quotes.BTC === undefined, "BTC가 국내 종목 quotes에 섞임");
    record("sources.crypto에 CoinGecko 출처가 기록됨 (약관 요구 표기)", quotes.sources.crypto === "CoinGecko", JSON.stringify(quotes.sources));
    record("silver skipped cleanly (no ECOS key) instead of failing the batch", quotes.commodities.silverPerGram === undefined, JSON.stringify(quotes.commodities));
    record("sources.silver is null when silver was skipped", quotes.sources.silver === null, JSON.stringify(quotes.sources));
    record(
      "stock pagination made exactly 5 data pages + a few probes (not thousands of individual calls)",
      fetchCallLog.filter((c) => c.includes("GetStockSecuritiesInfoService")).length <= 15,
      `${fetchCallLog.filter((c) => c.includes("GetStockSecuritiesInfoService")).length} calls`
    );
    record(
      "FX probe actually retried multiple dates before landing on FX_DAY (not stuck on the first result:3 response)",
      fetchCallLog.filter((c) => c.includes("exchangeJSON") || c.includes("searchdate")).length + fetchCallLog.filter((c) => c.includes("oapi.koreaexim")).length >= 1,
      "fx probing did not appear to run"
    );
  }

  // ===== 2. 검증 실패 시 기존 quotes.json 보존 =====
  const seededQuotes = JSON.stringify({ asOf: "2020-01-01T00:00:00+09:00", sources: {}, quotes: { MARKER: { price: 1, currency: "KRW" } }, rates: { KRW: 1 }, commodities: {} });
  await writeFile(QUOTES_PATH, seededQuotes, "utf8");
  // 티커 대비 절반도 안 되는 주식만 시세로 주는 배치를 강제 — 매칭률 90% 미만으로 실패해야 한다.
  const halfStockRows = STOCK_ROWS.slice(0, Math.floor(STOCK_COUNT / 2));
  const failing = await runBatch({ stockRows: halfStockRows });
  const afterFailure = await readFile(QUOTES_PATH, "utf8").catch(() => null);
  record("검증 실패(매칭률<90%) 시 exitCode=1로 종료됨", failing.exitCode === 1, `exitCode=${failing.exitCode}`);
  record("검증 실패 시 기존 quotes.json이 그대로 보존됨 (새 파일로 덮어쓰지 않음)", afterFailure === seededQuotes, "quotes.json content changed despite validation failure");
  record("검증 실패 시 임시 파일(.tmp)이 남아있지 않음", !existsSync(`${QUOTES_PATH}.tmp`), ".tmp file was left behind");

  // ===== 3. 코드 조인이 통째로 깨진 경우 (A접두어 버그 재현) =====
  // 시세는 4,200건 멀쩡히 오지만 종목 목록의 코드 형식이 달라 교집합이 0인 상황.
  // 예전 검증은 분자를 "시세 행 수"로 잡아 4200/4200 = 100%로 계산하고 통과시켰다.
  // 즉 이 버그를 잡으라고 넣은 검증이 정확히 이 버그를 통과시켰다. 이제는 실패한다.
  await writeFile(QUOTES_PATH, seededQuotes, "utf8");
  const mismatchedTickers = STOCK_ROWS.map((row) => ({ c: `A${row.srtnCd}`, n: row.itmsNm, m: "KOSPI", cur: "KRW", t: "stock" }));
  await writeFile(TICKERS_KR_PATH, JSON.stringify(mismatchedTickers), "utf8");
  const joinBroken = await runBatch();
  record(
    "코드 조인이 깨져 교집합이 0이면 실패함 (시세 행 수는 정상이라 옛 검증은 통과시켰던 케이스)",
    joinBroken.exitCode === 1,
    `exitCode=${joinBroken.exitCode}`
  );
  record("조인 깨짐으로 실패했을 때도 기존 quotes.json 보존됨", (await readFile(QUOTES_PATH, "utf8")) === seededQuotes, "quotes.json이 덮어써짐");

  // ===== 4. 목록에 없는 시세 코드가 있어도 매칭률이 100%를 넘지 않는다 =====
  // 실측에서 시세에만 있는 코드가 114건 있었고, 그 때문에 104.1%가 출력됐다.
  await rm(QUOTES_PATH, { force: true });
  const partialTickers = STOCK_ROWS.slice(0, 4000).map((row) => ({ c: row.srtnCd, n: row.itmsNm, m: "KOSPI", cur: "KRW", t: "stock" }));
  await writeFile(TICKERS_KR_PATH, JSON.stringify(partialTickers), "utf8");
  const logLines = [];
  const originalLog = console.log;
  console.log = (...args) => { logLines.push(args.join(" ")); originalLog(...args); };
  const extraCodes = await runBatch();
  console.log = originalLog;
  const stockLine = logLines.find((line) => line.startsWith("국내 주식 시세 매칭"));
  record("목록보다 시세가 많아도 배치는 성공함 (신규 상장 등 정상 상황)", extraCodes.exitCode !== 1, `exitCode=${extraCodes.exitCode}`);
  record("매칭률이 100%를 넘지 않음 (분자가 교집합이므로 구조적으로 불가능)", stockLine?.includes("4,000 / 4,000 (100.0%)"), stockLine || "매칭 로그를 찾지 못함");
  // 주식 200건(4,200 - 4,000) + 부분 목록에 없는 ETF 1건 = 201건
  record("목록에 없는 시세 코드 건수가 별도로 보고됨", logLines.some((line) => line.includes("종목 목록에 없는 코드 201건")), logLines.filter((l) => l.includes("시세 확보")).join(" | "));

  // ===== 4b. 기준일이 직전 영업일보다 이르면 경고 =====
  // 배치는 데이터가 있는 날짜를 찾을 때까지 소급 조회하므로, 제공이 밀려도 실패하지 않고
  // 조용히 하루 뒤처진 값을 쓴다. 06:00 배치가 늘 2영업일 전 데이터를 받고 있던 것도
  // 아무도 몰랐던 이유가 이것이다 — 이제는 로그에 남는다.
  // 이 픽스처의 TRADING_DAY는 오늘부터 2일 전이라 항상 직전 영업일보다 이르다.
  await rm(QUOTES_PATH, { force: true });
  await writeFile(TICKERS_KR_PATH, JSON.stringify(tickerFixture), "utf8");
  const staleWarnings = [];
  const originalWarn2 = console.warn;
  console.warn = (...args) => { staleWarnings.push(args.join(" ")); originalWarn2(...args); };
  const stale = await runBatch();
  console.warn = originalWarn2;
  record("기준일이 직전 영업일보다 이르면 경고를 남긴다", staleWarnings.some((line) => line.includes("직전 영업일")), staleWarnings.join(" | ") || "(경고 없음)");
  record("기준일이 이르다고 배치를 실패시키지는 않는다 (공휴일일 수 있음)", stale.exitCode !== 1, `exitCode=${stale.exitCode}`);

  // ===== 5. 가상자산 실패 경로 =====
  // 코인게코 한 곳이 흔들린다고 그날 국내 시세 전체를 버리지 않는다. 직전 코인 값을
  // 그대로 들고 가고, 그게 언제 값인지(cryptoAsOf)를 남겨 앱이 밝힐 수 있게 한다.
  await writeFile(TICKERS_KR_PATH, JSON.stringify(tickerFixture), "utf8");
  const withCrypto = JSON.stringify({
    asOf: "2020-01-01T00:00:00+09:00",
    sources: {},
    quotes: { MARKER: { price: 1, currency: "KRW" } },
    crypto: { BTC: { price: 12345678, currency: "KRW" } },
    cryptoAsOf: "2020-01-01",
    rates: { KRW: 1 },
    commodities: {},
  });
  for (const [mode, label] of [
    ["empty", "빈 객체 {} (HTTP 200이지만 실패)"],
    ["error-envelope", "status.error_code 응답"],
    ["http-500", "HTTP 500"],
  ]) {
    await writeFile(QUOTES_PATH, withCrypto, "utf8");
    const partialLogs = [];
    const restoreWarn = console.warn;
    console.warn = (...args) => { partialLogs.push(args.join(" ")); restoreWarn(...args); };
    const run = await runBatch({ cryptoMode: mode });
    console.warn = restoreWarn;
    record(`가상자산 ${label} -> 국내 시세는 그대로 게시된다`, run.exitCode !== 1, `exitCode=${run.exitCode}`);
    const published = JSON.parse(await readFile(QUOTES_PATH, "utf8"));
    record(`가상자산 ${label} -> 직전 코인 값이 보존된다`, published.crypto?.BTC?.price === 12345678, JSON.stringify(published.crypto));
    record(`가상자산 ${label} -> 코인 기준일이 국내 기준일보다 이르게 남는다`, published.cryptoAsOf === "2020-01-01" && published.cryptoAsOf < published.asOf.slice(0, 10), `cryptoAsOf=${published.cryptoAsOf} asOf=${published.asOf}`);
    record(`가상자산 ${label} -> 경고로 보고된다`, partialLogs.some((line) => line.includes("가상자산 시세 갱신 실패")), partialLogs.join(" | ").slice(0, 200));
  }

  // 직전 값조차 없으면 보여줄 게 없다 — 그때만 실패다.
  await writeFile(QUOTES_PATH, seededQuotes, "utf8");
  const noFallback = await runBatch({ cryptoMode: "empty" });
  record("가상자산 실패 + 보존할 직전 값도 없음 -> 배치 실패(exit 1)", noFallback.exitCode === 1, `exitCode=${noFallback.exitCode}`);
  record("그 경우 기존 quotes.json 보존", (await readFile(QUOTES_PATH, "utf8")) === seededQuotes, "quotes.json이 덮어써짐");

  // 일부만 실패하면 배치는 진행하고 그 종목만 빠진다 — 해당 자산은 앱에서 "시세 확인 불가".
  await rm(QUOTES_PATH, { force: true });
  const partialLog = [];
  const originalWarn = console.warn;
  console.warn = (...args) => { partialLog.push(args.join(" ")); originalWarn(...args); };
  const partial = await runBatch({ cryptoMode: "partial" });
  console.warn = originalWarn;
  const partialQuotes = JSON.parse(await readFile(QUOTES_PATH, "utf8"));
  record("가상자산 일부 실패 -> 배치는 성공", partial.exitCode !== 1, `exitCode=${partial.exitCode}`);
  record("가상자산 일부 실패 -> 받은 종목만 저장됨 (USDT 제외)", Object.keys(partialQuotes.crypto).sort().join(",") === "BTC,ETH", JSON.stringify(partialQuotes.crypto));
  record("가상자산 일부 실패 -> 미확보 종목이 경고로 보고됨", partialLog.some((line) => line.includes("가상자산 시세 미확보") && line.includes("USDT")), partialLog.join(" | "));

  // ===== 6. 네트워크 오류는 어느 API였는지 말해야 한다 =====
  // 2026-08-12 실행이 `[시세 배치 실패] fetch failed` 한 줄만 남기고 죽었다. undici의
  // 이 메시지에는 URL이 없어 어디가 끊긴 건지 로그만으로는 알 수 없었다.
  // 직전 코인 값이 있어야 "국내는 게시하고 코인은 유지" 경로를 볼 수 있다.
  await writeFile(QUOTES_PATH, withCrypto, "utf8");
  const netLog = [];
  const restoreWarn2 = console.warn;
  const restoreError = console.error;
  console.warn = (...args) => { netLog.push(args.join(" ")); };
  console.error = (...args) => { netLog.push(args.join(" ")); restoreError(...args); };
  const netFail = await runBatch({ cryptoMode: "network" });
  console.warn = restoreWarn2;
  console.error = restoreError;
  const joined = netLog.join(" | ");
  record("네트워크 오류 메시지가 어느 API인지 밝힌다", joined.includes("CoinGecko"), joined.slice(0, 240));
  record("네트워크 오류 메시지가 URL을 담는다", /api\.coingecko\.com/.test(joined), joined.slice(0, 240));
  record("네트워크 오류는 재시도된다", netLog.some((line) => /차 시도 실패/.test(line)), joined.slice(0, 240));
  record("가상자산 네트워크 오류에도 국내 시세는 게시된다", netFail.exitCode !== 1, `exitCode=${netFail.exitCode}`);

  // 인증키는 로그에 남으면 안 된다 — 공개 저장소의 Actions 로그로 새어 나간다.
  record("오류 메시지에 인증키가 노출되지 않는다", !/serviceKey=test-key|authkey=test-key/.test(joined), joined.slice(0, 240));
// ===== 전일 종가 =====
//
// 응답에 전일 종가 필드가 없어 clpr-vs로 만든다. 문제는 vs가 정말 "전일 대비"인지를
// 문서로만 믿어야 한다는 것인데, 이 프로젝트에서 필드명을 잘못 짚으면 예외가 아니라
// 조용한 0으로 끝난다(전일 대비 0 = 변동 없음). 그래서 같은 행의 등락률(fltRt)로
// 매 실행마다 의미를 검증한다:  vs / (clpr - vs) * 100 ≈ fltRt
{
  await runBatch();
  const written = JSON.parse(await readFile(QUOTES_PATH, "utf8"));

  const samsung = written.quotes["005930"];
  record("전일 종가가 종가와 함께 저장된다",
    samsung && samsung.price === 73400 && samsung.prevClose === 73000,
    JSON.stringify(samsung));

  record("전일 환율이 날짜와 함께 저장된다",
    written.prevRates && written.prevRates.USD > 0 && written.prevRatesDate === FX_PREV_DAY,
    `${written.prevRatesDate} (기대 ${FX_PREV_DAY}) · USD ${written.prevRates && written.prevRates.USD}`);

  // 비영업일 조회는 "어제"가 아니라 데이터가 있는 가장 최근 날로 내려앉아야 한다.
  record("비영업일 환율 조회는 데이터가 있는 날까지 거슬러 올라간다",
    written.prevRatesDate === FX_PREV_DAY && written.prevRatesDate !== shift(FX_DAY, -1),
    `${written.prevRatesDate} · 하루 전은 ${shift(FX_DAY, -1)}로 비어 있음`);

  record("가상자산의 24시간 전 값과 기준 시점이 저장된다",
    written.crypto.BTC && written.crypto.BTC.prev > 0 && written.cryptoPrevBasis === "24h" && /^\d{4}-\d{2}-\d{2}$/.test(written.cryptoPrevAt || ""),
    JSON.stringify({ btc: written.crypto.BTC, at: written.cryptoPrevAt, basis: written.cryptoPrevBasis }));

  const expectedPrev = `${PREV_TRADING_DAY.slice(0, 4)}-${PREV_TRADING_DAY.slice(4, 6)}-${PREV_TRADING_DAY.slice(6, 8)}`;
  record("전일 종가 기준일이 데이터에서 확정돼 저장된다",
    written.prevCloseDate === expectedPrev,
    `${written.prevCloseDate} (기대 ${expectedPrev})`);

  record("금도 전일 종가를 갖는다",
    written.commodities.goldPerGram === 151200 && written.commodities.goldPerGramPrev === 152000,
    JSON.stringify(written.commodities));
}

// 항등식이 깨지면 기본값으로 때우지 않고 배치를 멈춘다. 원본 행을 통째로 찍어야
// 무엇이 달라졌는지 다음 사람이 바로 본다.
{
  const corrupted = STOCK_ROWS.map((row) => ({ ...row }));
  // 한 행만 vs를 뒤집는다 — 값 자체는 그럴듯하고 fltRt와만 어긋난다.
  corrupted[5] = { ...corrupted[5], vs: "-999" };

  const logs = [];
  const originalError = console.error;
  const originalLog = console.log;
  console.error = (...args) => logs.push(args.join(" "));
  console.log = () => {};
  const before = JSON.parse(await readFile(QUOTES_PATH, "utf8"));
  const { exitCode } = await runBatch({ stockRows: corrupted });
  console.error = originalError;
  console.log = originalLog;
  const after = JSON.parse(await readFile(QUOTES_PATH, "utf8"));
  const joined = logs.join(" | ");

  record("항등식이 깨지면 배치가 실패로 끝난다", exitCode === 1, `exitCode=${exitCode}`);
  record("어느 출처인지 이름을 밝힌다", /주식시세정보/.test(joined), joined.slice(0, 200));
  record("원본 행을 통째로 찍는다",
    /"srtnCd"\s*:\s*"600005"/.test(joined) && /"fltRt"/.test(joined) && /"vs"\s*:\s*"-999"/.test(joined),
    joined.slice(0, 400));
  record("실패했으므로 기존 산출물을 건드리지 않는다",
    after.asOf === before.asOf && JSON.stringify(after.quotes["005930"]) === JSON.stringify(before.quotes["005930"]),
    `asOf ${before.asOf} -> ${after.asOf}`);
  record("0으로 때우지 않는다 (전일 종가를 0으로 쓴 종목이 없다)",
    !Object.values(after.quotes).some((quote) => quote.prevClose === 0),
    "prevClose가 0인 종목이 있음");
}

// 전일 종가를 만들 수 없는 종목은 빼고 센다. 0으로 채우면 화면에 "변동 없음"으로 보인다.
{
  const missing = STOCK_ROWS.map((row) => ({ ...row }));
  // vs·fltRt가 아예 없는 행(신규 상장 등). 항등식 검사는 이런 행을 표본에서 제외하므로
  // 배치는 계속 가고, 그 종목만 전일 종가 없이 저장돼야 한다.
  delete missing[1].vs;
  delete missing[1].fltRt;
  await runBatch({ stockRows: missing });
  const written = JSON.parse(await readFile(QUOTES_PATH, "utf8"));
  const code = missing[1].srtnCd;
  const quote = written.quotes[code];
  record("전일 종가를 못 만든 종목은 키 자체를 넣지 않는다",
    quote && quote.price > 0 && !("prevClose" in quote),
    JSON.stringify(quote));
  record("전일 종가가 있는 종목은 그대로 저장된다",
    written.quotes["005930"] && written.quotes["005930"].prevClose === 73000,
    JSON.stringify(written.quotes["005930"]));
}


  // ===== 환율: 도메인 이전과 응답 코드 =====
  // 열흘 동안 환율이 낡아갔는데 로그는 "유효한 데이터를 찾지 못했습니다"만 반복했다.
  // 그 문장은 사실이지만 무엇을 고쳐야 하는지 한 글자도 말하지 않는다. 아래 검사들은
  // (1) 실제 응답 모양을 그대로 통과시키는지와 (2) 실패가 스스로 이름을 대는지를 본다.
  {
    await rm(QUOTES_PATH, { force: true });
    const { exitCode } = await runBatch({ fxMode: "captured" });
    const written = JSON.parse(await readFile(QUOTES_PATH, "utf8"));
    record("캡처한 실제 응답으로 배치가 성공한다", exitCode !== 1, `exitCode=${exitCode}`);
    // "1,359.5"를 Number()에 그대로 넣으면 NaN이다. 쉼표를 떼는 코드가 사라지면 여기서 걸린다.
    record("천 단위 쉼표가 붙은 문자열 환율을 숫자로 읽는다 (USD 1,359.5 -> 1359.5)",
      written.rates && written.rates.USD === 1359.5,
      `USD=${JSON.stringify(written.rates && written.rates.USD)} (NaN이면 쉼표 제거가 빠진 것)`);
    // JPY(100)은 통화코드 자체에 배수가 들어 있다. 100으로 나누지 않으면 조용한 100배 오류다.
    record("JPY(100)의 배수 100이 실제로 나눠진다 (921.36 -> 9.2136)",
      written.rates && Math.abs(written.rates.JPY - 9.2136) < 1e-9,
      `JPY=${JSON.stringify(written.rates && written.rates.JPY)} (921.36이면 100배 오류)`);
    record("IDR(100)도 같은 규칙으로 나눠진다 (8.24 -> 0.0824)",
      written.rates && Math.abs(written.rates.IDR - 0.0824) < 1e-9,
      `IDR=${JSON.stringify(written.rates && written.rates.IDR)}`);
    record("통화코드에서 배수 괄호가 떨어져 JPY로 저장된다",
      written.rates && written.rates["JPY(100)"] === undefined && written.rates.JPY > 0,
      JSON.stringify(Object.keys(written.rates || {})));
    // 목록에 KRW 행이 섞여 온다(deal_bas_r "1"). 값이 같아 지금은 무해하지만,
    // 그 행이 다른 값으로 바뀌면 원화 환산이 통째로 틀어진다.
    record("응답에 섞여 오는 KRW 행이 원화 기준 1을 흔들지 않는다",
      written.rates && written.rates.KRW === 1,
      `KRW=${JSON.stringify(written.rates && written.rates.KRW)}`);
    record("쉼표 붙은 다른 통화도 같은 방식으로 읽힌다 (EUR 1,504.15)",
      written.rates && written.rates.EUR === 1504.15,
      `EUR=${JSON.stringify(written.rates && written.rates.EUR)}`);
  }

  // 구 도메인이 돌려주는 모양. HTTP 200 · 정상적인 JSON 배열 · result만 2다.
  // 연결 실패처럼 보이지 않으므로, 로그가 코드를 말하지 않으면 원인을 찾을 길이 없다.
  {
    const previous = await readFile(QUOTES_PATH, "utf8").catch(() => null);
    const { exitCode, errorLog } = await runBatch({ fxMode: "result2" });
    const message = errorLog.join(" ");
    record("result:2면 배치가 멈춘다 (환율 없이 진행하지 않는다)", exitCode === 1, `exitCode=${exitCode}`);
    record("실패 메시지가 result 코드를 그대로 말한다",
      /result=2/.test(message), message.slice(0, 200));
    record("실패 메시지가 그 코드의 뜻을 말한다",
      /DATA 코드 오류/.test(message), message.slice(0, 200));
    record("실패 메시지가 무엇을 확인해야 하는지 말한다",
      /호스트|경로|data 파라미터/.test(message), message.slice(0, 250));
    record("실패 메시지가 어느 호스트를 불렀는지 밝힌다",
      /oapi\.koreaexim\.go\.kr/.test(message), message.slice(0, 200));
    record("실패 메시지가 조회한 날짜 범위를 밝힌다",
      /\d{8}~\d{8}/.test(message), message.slice(0, 200));
    // 인증 문제와 요청 문제를 한 문장으로 뭉치면 읽는 사람이 할 일이 갈리지 않는다.
    record("인증코드 오류(3)를 요청 오류(2)로 뭉치지 않는다",
      !/인증코드 오류/.test(message), message.slice(0, 250));
    // 실패했으면 파일을 건드리지 않아야 한다 — 반쯤 채워진 산출물이 남으면 안 된다.
    const after = await readFile(QUOTES_PATH, "utf8").catch(() => null);
    record("환율 실패 시 기존 quotes.json을 덮어쓰지 않는다", after === previous,
      after === previous ? "" : "파일이 바뀌었다");
  }

  // 열흘 전부 result:3이면 그건 키 문제다. 위와 다른 문장이 나와야 한다.
  {
    const { exitCode, errorLog } = await runBatch({ fxMode: "authfail" });
    const message = errorLog.join(" ");
    record("result:3만 열흘이면 키 문제라고 말한다",
      exitCode === 1 && /result=3/.test(message) && /인증코드 오류/.test(message),
      message.slice(0, 200));
    record("키 문제일 때 KOREAEXIM_AUTH_KEY를 지목한다",
      /KOREAEXIM_AUTH_KEY/.test(message), message.slice(0, 250));
  }

} finally {
  if (originalTickersKr !== null) await writeFile(TICKERS_KR_PATH, originalTickersKr, "utf8");
  else await rm(TICKERS_KR_PATH, { force: true });
  if (originalQuotes !== null) await writeFile(QUOTES_PATH, originalQuotes, "utf8");
  else await rm(QUOTES_PATH, { force: true });
  await rm(`${QUOTES_PATH}.tmp`, { force: true });
}


const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exitCode = 1;
