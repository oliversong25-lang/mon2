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
const FX_DAY = shift(TODAY, -3); // FX lags by one more day than equities, on purpose

function envelope(items, totalCount) {
  return { response: { header: { resultCode: "00", resultMsg: "OK" }, body: { items: { item: items }, totalCount } } };
}

const STOCK_COUNT = 4200;
// 4200 synthetic stock rows so pagination (numOfRows=1000) actually kicks in (5 pages).
const STOCK_ROWS = Array.from({ length: STOCK_COUNT }, (_, i) => ({
  basDt: TRADING_DAY,
  srtnCd: String(600000 + i),
  isinCd: `KR7${String(600000 + i)}00`,
  itmsNm: `테스트종목${i}`,
  clpr: String(10000 + i),
  mrktTotAmt: String(1000000 + i),
}));
STOCK_ROWS[0].srtnCd = "005930"; // 삼성전자 자리에 대표 케이스 하나 심어둔다
STOCK_ROWS[0].isinCd = "KR7005930003";
STOCK_ROWS[0].clpr = "73400";

const ETF_ROWS = [{ basDt: TRADING_DAY, srtnCd: "069500", isinCd: "KR7069500007", itmsNm: "KODEX 200", clpr: "42350", mrktTotAmt: "64253000000" }];
const ETN_ROWS = [];
const GOLD_ROWS = [{ basDt: TRADING_DAY, isuNm: "금99.99_1g", itmsNm: "금99.99_1g", clpr: "151200" }];

// 실측: 데이터 없는 날짜에도 result:3 한 건짜리 배열이 온다 (빈 배열이 아니다).
function fxEnvelopeForDate(date) {
  if (date !== FX_DAY) return [{ result: 3, cur_unit: null, cur_nm: null, ttb: null, tts: null, deal_bas_r: null, bkpr: null, yy_efee_r: null, ten_dd_efee_r: null, kftc_bkpr: null, kftc_deal_bas_r: null }];
  const padCodes = ["AED", "AUD", "BHD", "BND", "CAD", "CHF", "CNH", "DKK", "EUR", "GBP", "HKD", "IDR", "KWD", "MYR", "NOK", "NZD", "SAR", "SEK", "SGD", "THB"];
  return [
    { result: 1, cur_unit: "USD", deal_bas_r: "1,380.50" },
    { result: 1, cur_unit: "JPY(100)", deal_bas_r: "925.00" },
    ...padCodes.map((code) => ({ result: 1, cur_unit: code, deal_bas_r: "100.00" })),
  ];
}

// 실측: 코인게코는 잘못된 ID를 줘도 HTTP 200에 빈 객체 {}를 돌려준다. cryptoMode로
// 그 경로들을 재현한다.
const CRYPTO_BODY = { bitcoin: { krw: 91530787 }, ethereum: { krw: 2691101 }, tether: { krw: 1416.7 } };
function cryptoResponseFor(mode) {
  if (mode === "empty") return jsonResponse({}); // HTTP 200 + {} — 성공으로 오인되던 형태
  if (mode === "error-envelope") return jsonResponse({ status: { error_code: 429, error_message: "rate limited" } });
  if (mode === "partial") return jsonResponse({ bitcoin: CRYPTO_BODY.bitcoin, ethereum: CRYPTO_BODY.ethereum });
  if (mode === "http-500") return { ok: false, status: 500, json: async () => ({}), text: async () => "" };
  return jsonResponse(CRYPTO_BODY);
}

function makeFetchMock({ stockRows = STOCK_ROWS, cryptoMode = "ok" } = {}) {
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
        const rows = basDt === TRADING_DAY ? stockRows : [];
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
      return jsonResponse(fxEnvelopeForDate(searchdate));
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
async function runBatch({ stockRows, cryptoMode } = {}) {
  const { fn, fetchCallLog } = makeFetchMock({ stockRows, cryptoMode });
  global.fetch = fn;
  process.exitCode = undefined;
  await import(`./build-quotes.mjs?t=${Date.now()}-${Math.random()}`);
  await new Promise((r) => setTimeout(r, 300));
  const exitCode = process.exitCode;
  process.exitCode = undefined;
  return { fetchCallLog, exitCode };
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
  // 하드코딩된 BTC 158,200,000원을 지운 이유가 여기 있다. 시세를 못 받았는데 배치가
  // 성공하면 앱이 옛 값이나 0으로 총자산을 계산해 버린다. 못 받으면 죽어야 한다.
  await writeFile(TICKERS_KR_PATH, JSON.stringify(tickerFixture), "utf8");
  for (const [mode, label] of [
    ["empty", "빈 객체 {} (HTTP 200이지만 실패)"],
    ["error-envelope", "status.error_code 응답"],
    ["http-500", "HTTP 500"],
  ]) {
    await writeFile(QUOTES_PATH, seededQuotes, "utf8");
    const failed = await runBatch({ cryptoMode: mode });
    record(`가상자산 ${label} -> 배치 실패(exit 1)`, failed.exitCode === 1, `exitCode=${failed.exitCode}`);
    record(`가상자산 ${label} -> 기존 quotes.json 보존`, (await readFile(QUOTES_PATH, "utf8")) === seededQuotes, "quotes.json이 덮어써짐");
  }

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
