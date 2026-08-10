// scripts/test-tickers-mock.mjs
// Exercises the parts of build-tickers.mjs that changed in this pass — the KIND-
// fallback -> 금융위원회_KRX상장종목정보 path (including the confirmed live A-prefix
// srtnCd quirk), ETF/ETN collection + market cap via 금융위원회_증권상품시세정보, the
// stock market-cap backfill via 주식시세정보, and the DART corpCode.xml zip ->
// induty_code pipeline — against mocked fetch()/KIND responses, without a live key.
// The DART zip parsing runs through the REAL python subprocess against a real
// fixture zip (not mocked), so that part is a genuine end-to-end check of the
// unzip+XML-parse code.

import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { fetchDartCorpCodeMap, attachSectors, createRateLimiter } from "./lib/dart.mjs";
import { resolveSector } from "./lib/ksic.mjs";
import { normalizeKrCode } from "./lib/data-go-kr.mjs";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

// build-tickers.mjs reads DATA_GO_KR_KEY/DART_API_KEY into module-level consts at
// import time, so these env vars must be set BEFORE it's imported. Static `import`
// statements are hoisted above all other top-level code (they'd run before the
// `process.env...` lines above even if this file's own layout looks like it's set
// first) — a dynamic import(), run in normal program order, avoids that trap.
process.env.DATA_GO_KR_KEY = "test-key";
process.env.DART_API_KEY = "test-key";
const { fetchKrxListedInfoApi, collectKoreanStocks, collectKrxProducts, fetchStockMarketCaps, attachKrSectors } = await import("./build-tickers.mjs");

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const FIXTURE_ZIP = resolve(ROOT, "scripts", "test-fixtures-dart-corpcode.zip");

const results = [];
const record = (label, ok, detail) => {
  results.push({ label, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${ok ? "" : `  — ${detail}`}`);
};

function pythonExecutable() {
  const local = resolve(ROOT, ".venv", "Scripts", "python.exe");
  return process.env.PYTHON || (existsSync(local) ? local : "python");
}
function runPython(code, input) {
  const result = spawnSync(pythonExecutable(), ["-c", code], { input, encoding: null, windowsHide: true, maxBuffer: 64 * 1024 * 1024, env: { ...process.env, PYTHONIOENCODING: "utf-8" } });
  if (result.status !== 0) throw new Error(Buffer.from(result.stderr || "").toString("utf8").trim() || "python failed");
  return Buffer.from(result.stdout || "").toString("utf8");
}
function jsonResponse(body) {
  return { ok: true, status: 200, json: async () => body, text: async () => JSON.stringify(body) };
}
function dataGoKrEnvelope(items, totalCount) {
  return { response: { header: { resultCode: "00", resultMsg: "OK" }, body: { items: { item: items }, totalCount } } };
}
function servePaged(rows, url) {
  const numOfRows = Number(url.searchParams.get("numOfRows"));
  const pageNo = Number(url.searchParams.get("pageNo"));
  const start = (pageNo - 1) * numOfRows;
  return jsonResponse(dataGoKrEnvelope(rows.slice(start, start + numOfRows), rows.length));
}

// --- 0. normalizeKrCode: the two live-confirmed cases from the bug report ---
record("normalizeKrCode: standard KR7 ISIN recovers the 6-digit code (삼성전자)", normalizeKrCode("005930", "KR7005930003") === "005930", "");
record(
  "normalizeKrCode: A-prefixed srtnCd + non-KR ISIN (딥커머스, actual live sample) strips the prefix",
  normalizeKrCode("A900110", "HK0000057197") === "900110",
  `got "${normalizeKrCode("A900110", "HK0000057197")}"`
);
record(
  "normalizeKrCode: prefers isinCd over a mismatched/prefixed srtnCd when isinCd is standard KR7",
  normalizeKrCode("A005930", "KR7005930003") === "005930",
  `got "${normalizeKrCode("A005930", "KR7005930003")}"`
);

// --- 1. DART corpCode.xml zip -> stock_code:corp_code map (real python, real zip fixture) ---
try {
  const zipBuffer = await readFile(FIXTURE_ZIP);
  global.fetch = async () => ({ ok: true, arrayBuffer: async () => zipBuffer.buffer.slice(zipBuffer.byteOffset, zipBuffer.byteOffset + zipBuffer.byteLength) });
  const map = await fetchDartCorpCodeMap("test-key", runPython);
  record("corpCode zip parsed: 삼성전자 005930 -> corp_code 00126380", map.get("005930") === "00126380", JSON.stringify([...map.entries()]));
  record("corpCode zip parsed: SK하이닉스 000660 -> corp_code 00401731", map.get("000660") === "00401731", JSON.stringify([...map.entries()]));
  record("unlisted row (empty stock_code) excluded from map", map.size === 2, `map had ${map.size} entries, expected 2`);
} catch (error) {
  record("DART corpCode zip pipeline runs without throwing", false, error.stack);
}

// --- 2. attachSectors: per-ticker induty_code lookup, concurrency, partial failure ---
try {
  const corpCodeMap = new Map([["005930", "00126380"], ["000660", "00401731"], ["999999", "no-such-corp"]]);
  const tickers = [{ c: "005930" }, { c: "000660" }, { c: "999999" }, { c: "no-corp-code" }];
  let dartCalls = 0;
  global.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes("company.json")) {
      dartCalls += 1;
      const corpCode = url.searchParams.get("corp_code");
      if (corpCode === "00126380") return jsonResponse({ status: "000", induty_code: "264" });
      if (corpCode === "00401731") return jsonResponse({ status: "000", induty_code: "261" });
      return jsonResponse({ status: "013", message: "no data" });
    }
    throw new Error(`unmocked: ${url}`);
  };
  const { sectors, failures } = await attachSectors(tickers, "test-key", corpCodeMap, { concurrency: 4 });
  record("삼성전자 sector attached (264)", sectors.get("005930") === "264", JSON.stringify([...sectors.entries()]));
  record("SK하이닉스 sector attached (261)", sectors.get("000660") === "261", JSON.stringify([...sectors.entries()]));
  record("ticker with no corp_code mapping skipped (no DART call wasted)", !sectors.has("no-corp-code"), "");
  record("ticker whose DART lookup errors is simply absent, not thrown", !sectors.has("999999"), "");
  record("only tickers with a corp_code hit DART (3 calls, not 4)", dartCalls === 3, `${dartCalls} calls`);
  record("status 013(데이터 없음)은 실패로 기록되지 않음", failures.length === 0, JSON.stringify(failures));
} catch (error) {
  record("attachSectors runs without throwing", false, error.stack);
}

// --- 2b. 실측 재현: 요청 제한(020)을 조용히 삼키면 안 된다 ---
// 라이브 배치에서 2,758종목 중 정확히 앞 1,010건만 업종이 붙고 나머지가 전부 비었다.
// 원인은 DART 분당 한도 초과(020)를 status !== "000" 한 줄로 뭉개 "업종 없는 종목"과
// 구별 없이 null 처리한 것이었다. 이제는 재시도하고, 그래도 계속되면 실패로 남는다.
try {
  const corpCodeMap = new Map([["005930", "00126380"]]);
  let calls = 0;
  global.fetch = async (input) => {
    const url = new URL(String(input));
    if (!url.pathname.includes("company.json")) throw new Error(`unmocked: ${url}`);
    calls += 1;
    return jsonResponse({ status: "020", message: "요청 제한을 초과하였습니다." });
  };
  const { sectors, failures } = await attachSectors([{ c: "005930" }], "test-key", corpCodeMap, {
    concurrency: 1,
    limiter: async () => {},
    sleep: async () => {}, // 실제로 1분씩 기다리지 않도록 주입
  });
  record("요청 제한(020)은 재시도됨 (1회로 포기하지 않음)", calls === 3, `${calls} calls`);
  record("요청 제한이 계속되면 업종을 붙이지 않음", !sectors.has("005930"), JSON.stringify([...sectors.entries()]));
  record("요청 제한이 failures로 보고됨 (조용한 누락 아님)", failures.length === 1 && /020/.test(failures[0].message), JSON.stringify(failures));
} catch (error) {
  record("attachSectors rate-limit path runs without throwing", false, error.stack);
}

// --- 2c. 인증키 오류는 재시도해도 소용없으므로 즉시 예외로 올린다 ---
try {
  const corpCodeMap = new Map([["005930", "00126380"]]);
  global.fetch = async () => jsonResponse({ status: "011", message: "사용할 수 없는 키입니다." });
  let thrown = null;
  try {
    await attachSectors([{ c: "005930" }], "bad-key", corpCodeMap, { concurrency: 1, limiter: async () => {}, sleep: async () => {} });
  } catch (error) {
    thrown = error;
  }
  record("인증키 오류(011)는 예외로 올라옴", Boolean(thrown) && /011/.test(thrown.message), thrown ? thrown.message : "throw 안 됨");
} catch (error) {
  record("attachSectors fatal-status path runs without throwing", false, error.stack);
}

// --- 2d. createRateLimiter: 창 안에서 한도를 넘기지 않는다 ---
try {
  const acquire = createRateLimiter(3, 200);
  const started = Date.now();
  for (let i = 0; i < 4; i += 1) await acquire();
  const elapsed = Date.now() - started;
  record("createRateLimiter: 한도 초과분은 창이 지날 때까지 대기시킴", elapsed >= 200, `${elapsed}ms`);
} catch (error) {
  record("createRateLimiter runs without throwing", false, error.stack);
}

// --- 2e. resolveSector: KSIC 코드가 중분류 + 한글명으로 풀린다 ---
try {
  const samsung = resolveSector("264"); // 삼성전자 실측값
  const hyundai = resolveSector("30121"); // 현대차 실측값
  const naver = resolveSector("63120"); // NAVER 실측값
  record("resolveSector: 264 -> 중분류 26 전자·통신장비", samsung?.secDiv === "26" && samsung?.secDivName === "전자·통신장비", JSON.stringify(samsung));
  record("resolveSector: 30121 -> 중분류 30 자동차·트레일러", hyundai?.secDiv === "30" && hyundai?.secDivName === "자동차·트레일러", JSON.stringify(hyundai));
  record("resolveSector: 63120 -> 중분류 63 정보서비스", naver?.secDiv === "63" && naver?.secDivName === "정보서비스", JSON.stringify(naver));
  record("resolveSector: 세분류 원본 코드는 보존됨 (상세 화면용)", samsung?.sec === "264" && hyundai?.sec === "30121", JSON.stringify([samsung, hyundai]));
  record("resolveSector: 숫자가 아닌 값은 null", resolveSector("") === null && resolveSector("abc") === null && resolveSector(null) === null, "");
} catch (error) {
  record("resolveSector runs without throwing", false, error.stack);
}

// --- 3. fetchKrxListedInfoApi(): live-confirmed A-prefix row must normalize end to end ---
try {
  const rows = [
    { basDt: "20260807", isinCd: "KR7005930003", srtnCd: "005930", itmsNm: "삼성전자", mrktCtg: "유가증권시장" },
    { basDt: "20260807", isinCd: "HK0000057197", srtnCd: "A900110", itmsNm: "딥커머스", mrktCtg: "코스닥" },
  ];
  global.fetch = async (input) => {
    const url = new URL(String(input));
    if (!url.pathname.includes("GetKrxListedInfoService")) throw new Error(`unmocked: ${url}`);
    const numOfRows = Number(url.searchParams.get("numOfRows"));
    if (numOfRows === 1) return jsonResponse(dataGoKrEnvelope(rows.slice(0, 1), rows.length)); // resolveLatestBasDt probe
    return servePaged(rows, url);
  };
  const result = await fetchKrxListedInfoApi();
  record("fetchKrxListedInfoApi: 삼성전자 코드 그대로(005930)", result.find((r) => r.n === "삼성전자")?.c === "005930", JSON.stringify(result));
  record(
    "fetchKrxListedInfoApi: A접두어 붙은 딥커머스가 900110으로 정규화됨 (실측 버그 재현 케이스)",
    result.find((r) => r.n === "딥커머스")?.c === "900110",
    JSON.stringify(result)
  );
  record("fetchKrxListedInfoApi: 한글 시장구분(유가증권시장/코스닥)이 KOSPI/KOSDAQ로 매핑됨", result.find((r) => r.n === "삼성전자")?.m === "KOSPI" && result.find((r) => r.n === "딥커머스")?.m === "KOSDAQ", JSON.stringify(result));
} catch (error) {
  record("fetchKrxListedInfoApi runs without throwing", false, error.stack);
}

// --- 4. collectKoreanStocks(): KIND 실패 시 공식 API로 폴백하고, 그 결과도 정규화됨 ---
try {
  const apiRows = [{ basDt: "20260807", isinCd: "HK0000057197", srtnCd: "A900110", itmsNm: "딥커머스", mrktCtg: "코스닥" }];
  global.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.hostname === "kind.krx.co.kr") return { ok: false, status: 500 }; // KIND 실패 강제
    if (url.pathname.includes("GetKrxListedInfoService")) {
      const numOfRows = Number(url.searchParams.get("numOfRows"));
      if (numOfRows === 1) return jsonResponse(dataGoKrEnvelope(apiRows.slice(0, 1), apiRows.length));
      return servePaged(apiRows, url);
    }
    throw new Error(`unmocked: ${url}`);
  };
  const result = await collectKoreanStocks();
  record("collectKoreanStocks: KIND 실패 시 공식 API 폴백 동작", result.length === 1 && result[0].c === "900110", JSON.stringify(result));
} catch (error) {
  record("collectKoreanStocks fallback runs without throwing", false, error.stack);
}

// --- 5. collectKrxProducts(): mrktTotAmt -> x, 코드 정규화 ---
try {
  const etfRows = [{ basDt: "20260807", isinCd: "KR7069500007", srtnCd: "069500", itmsNm: "KODEX 200", mrktTotAmt: "64253000000" }];
  global.fetch = async (input) => {
    const url = new URL(String(input));
    if (url.pathname.includes("getETFPriceInfo")) {
      const numOfRows = Number(url.searchParams.get("numOfRows"));
      if (numOfRows === 1) return jsonResponse(dataGoKrEnvelope(etfRows.slice(0, 1), etfRows.length));
      return servePaged(etfRows, url);
    }
    if (url.pathname.includes("getETNPriceInfo")) return jsonResponse(dataGoKrEnvelope([], 0));
    throw new Error(`unmocked: ${url}`);
  };
  const result = await collectKrxProducts();
  record("collectKrxProducts: mrktTotAmt이 x 필드로 채워짐 (검색 정렬 복원)", result[0]?.x === 64253000000, JSON.stringify(result));
  record("collectKrxProducts: 코드 정규화 적용됨", result[0]?.c === "069500", JSON.stringify(result));
} catch (error) {
  record("collectKrxProducts runs without throwing", false, error.stack);
}

// --- 6. fetchStockMarketCaps(): 주식시세정보 mrktTotAmt로 맵 생성, A접두어 케이스도 정규화 ---
try {
  const stockRows = [
    { basDt: "20260807", isinCd: "KR7005930003", srtnCd: "005930", clpr: "73400", mrktTotAmt: "500000000000000" },
    { basDt: "20260807", isinCd: "HK0000057197", srtnCd: "A900110", clpr: "8500", mrktTotAmt: "20299281231" },
  ];
  global.fetch = async (input) => {
    const url = new URL(String(input));
    if (!url.pathname.includes("GetStockSecuritiesInfoService")) throw new Error(`unmocked: ${url}`);
    const numOfRows = Number(url.searchParams.get("numOfRows"));
    if (numOfRows === 1) return jsonResponse(dataGoKrEnvelope(stockRows.slice(0, 1), stockRows.length));
    return servePaged(stockRows, url);
  };
  const caps = await fetchStockMarketCaps();
  record("fetchStockMarketCaps: 삼성전자 시가총액 매핑됨", caps.get("005930") === 500000000000000, JSON.stringify([...caps.entries()]));
  record("fetchStockMarketCaps: A접두어 종목도 정규화된 코드(900110)로 매핑됨", caps.get("900110") === 20299281231, JSON.stringify([...caps.entries()]));
} catch (error) {
  record("fetchStockMarketCaps runs without throwing", false, error.stack);
}

// --- 7. attachKrSectors: 업종 매칭률이 낮으면 경고가 아니라 실패여야 한다 ---
// 예전엔 console.warn만 하고 그대로 진행했다. 그래서 63%가 비어 있는 산출물이
// "성공"으로 커밋됐다. 업종이 통째로 날아간 파일을 조용히 게시하느니 배치를 죽인다.
try {
  const zipBuffer = await readFile(FIXTURE_ZIP);
  global.fetch = async (input) => {
    const url = typeof input === "string" || input instanceof URL ? new URL(String(input)) : null;
    if (!url || url.hostname === "opendart.fss.or.kr") {
      if (url?.pathname.includes("corpCode.xml")) return { ok: true, arrayBuffer: async () => zipBuffer.buffer.slice(zipBuffer.byteOffset, zipBuffer.byteOffset + zipBuffer.byteLength) };
      if (url?.pathname.includes("company.json")) return jsonResponse({ status: "013" }); // 전부 실패 -> 매칭률 0%
    }
    throw new Error(`unmocked: ${url}`);
  };
  // 삼성전자 하나만 corpCode에 있고 나머지 9개는 매칭 안 되는 10종목 -> 10% 매칭률
  const stockRows = [{ c: "005930", n: "삼성전자", t: "stock" }, ...Array.from({ length: 9 }, (_, i) => ({ c: `99000${i}`, n: `테스트${i}`, t: "stock" }))];
  let thrown = null;
  try {
    await attachKrSectors(stockRows);
  } catch (error) {
    thrown = error;
  }
  record("attachKrSectors: 업종 매칭률 80% 미만이면 예외를 던짐 (조용한 통과 금지)", Boolean(thrown) && /업종 매칭률/.test(thrown.message), thrown ? thrown.message : "throw 안 됨");
} catch (error) {
  record("attachKrSectors low-match path runs without throwing unexpectedly", false, error.stack);
}

// --- 8. attachKrSectors: 확보된 업종은 secDiv/secDivName까지 붙어 나온다 ---
try {
  const zipBuffer = await readFile(FIXTURE_ZIP);
  global.fetch = async (input) => {
    const url = typeof input === "string" || input instanceof URL ? new URL(String(input)) : null;
    if (url?.pathname.includes("corpCode.xml")) return { ok: true, arrayBuffer: async () => zipBuffer.buffer.slice(zipBuffer.byteOffset, zipBuffer.byteOffset + zipBuffer.byteLength) };
    if (url?.pathname.includes("company.json")) return jsonResponse({ status: "000", induty_code: "264" });
    throw new Error(`unmocked: ${url}`);
  };
  const result = await attachKrSectors([{ c: "005930", n: "삼성전자", t: "stock" }]);
  const samsung = result.find((row) => row.c === "005930");
  record("attachKrSectors: 세분류 코드 보존 (sec=264)", samsung?.sec === "264", JSON.stringify(samsung));
  record("attachKrSectors: 중분류 코드·한글명 부착 (26/전자·통신장비)", samsung?.secDiv === "26" && samsung?.secDivName === "전자·통신장비", JSON.stringify(samsung));
} catch (error) {
  record("attachKrSectors sector-shape path runs without throwing", false, error.stack);
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exitCode = 1;
