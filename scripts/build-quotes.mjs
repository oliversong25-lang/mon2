// scripts/build-quotes.mjs
//
// Daily batch: fetch domestic stock/ETF/ETN prices, KRX gold, FX rates (and, if an
// ECOS key is configured, an international silver reference) and bake them into
// data/quotes.json. The app fetches this static file at runtime — no API key ever
// ships to the client, and there is no server: this script is meant to be run once
// a day by scripts/../.github/workflows/quotes.yml.
//
// Field names for 주식시세정보/증권상품시세정보/수출입은행 환율 are the well-established
// public shapes for these APIs. 일반상품시세정보(금시세) and ECOS(은) are new
// integrations for this project — on the FIRST real run with a live key, check the
// [raw-sample] console output for those two and adjust the `pick*` functions below
// if the field names differ from what's assumed here.

import { mkdir, writeFile, readFile, rename } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { fetchAll, kstToday, shiftDate, resolveLatestBasDt, normalizeKrCode, setupUtf8Console, previousBusinessDay } from "./lib/data-go-kr.mjs";
import { fetchCryptoQuotes, COINGECKO_IDS } from "./lib/crypto.mjs";
import { fetchWithRetry } from "./lib/http.mjs";

setupUtf8Console();

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DATA_DIR = resolve(ROOT, "data");

const DATA_GO_KR_KEY = process.env.DATA_GO_KR_KEY;
const KOREAEXIM_AUTH_KEY = process.env.KOREAEXIM_AUTH_KEY;
const ECOS_AUTH_KEY = process.env.ECOS_AUTH_KEY; // optional — silver is skipped (not failed) without it
const COINGECKO_API_KEY = process.env.COINGECKO_API_KEY;
const COINGECKO_PLAN = process.env.COINGECKO_PLAN || "demo"; // 유료 전환 시 "pro"

const STOCK_URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo";
const ETF_URL = "https://apis.data.go.kr/1160100/service/GetSecuritiesProductInfoService/getETFPriceInfo";
const ETN_URL = "https://apis.data.go.kr/1160100/service/GetSecuritiesProductInfoService/getETNPriceInfo";
const GOLD_URL = "https://apis.data.go.kr/1160100/service/GetGeneralProductInfoService/getGoldPriceInfo";
const KOREAEXIM_FX_URL = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON";
// ECOS 통계표코드는 실제 키 발급 후 StatisticItemList(통계코드검색)로 "귀금속" 또는 "은"
// 키워드로 조회해 확정해야 한다 — 아래 값은 미확정 자리표시자다.
const ECOS_SILVER_STAT_CODE = process.env.ECOS_SILVER_STAT_CODE || "";
const ECOS_URL_BASE = "https://ecos.bok.or.kr/api";

// ─────────────────────────────────────────────────────────────────────────────
// 전일 종가
//
// 응답에는 전일 종가 필드가 없다. 있는 것은 종가(clpr)와 전일 대비(vs)뿐이라
// 빼서 만든다:  prevClose = clpr - vs
//
// 문제는 vs가 정말 "전일 대비"인지를 문서로만 믿어야 한다는 점이다. 이 프로젝트에서
// 필드명을 잘못 짚으면 예외가 아니라 **조용한 0**으로 끝난다(전일 대비가 0이면 변동
// 없음으로 보인다). 그래서 문서를 믿는 대신 매 실행마다 응답 스스로 증명하게 한다.
//
// 등락률(fltRt)이 같은 행에 함께 온다. 세 필드가 문서대로라면 이 항등식이 성립한다:
//
//     vs / (clpr - vs) * 100  ≈  fltRt
//
// 한 번 눈으로 확인하는 것보다 강하다 — 매 실행마다 의미를 검증하고, 제공처가 나중에
// 조용히 의미를 바꿔도 잡힌다.
//
// 허용 오차: fltRt는 소수 둘째 자리에서 반올림돼 온다(실제 응답이 ".7" "-.24" 같은
// 모양이다). 반올림만으로 최대 ±0.005%p가 벌어진다. 2026-08-13 실측으로 네 출처의
// 최대 오차가 정확히 그 경계였다 — 주식 0.00495 · ETF 0.00500 · ETN 0.00500 ·
// 금 0.00059 %p. 절대 오차 0.02%p(측정 최대치의 4배)와 상대 오차 1% 중 느슨한 쪽을
// 쓴다. 반올림은 넉넉히 덮으면서, 필드가 뒤바뀌거나 의미가 달라지는 경우는
// 오차가 자릿수로 벌어지므로 확실히 잡힌다.
const FLT_RT_ABS_TOLERANCE = 0.02;   // %p
const FLT_RT_REL_TOLERANCE = 0.01;   // 1%

// 검사할 표본 수. 전 종목을 다 보는 것은 낭비이고, 한두 건만 보면 우연히 맞을 수 있다.
const IDENTITY_SAMPLE = 200;

class QuoteFieldError extends Error {
  constructor(message, rows) {
    super(message);
    this.name = "QuoteFieldError";
    this.rows = rows;
  }
}

// 한 행이 항등식을 만족하는지. 검사할 수 없는 행은 null(표본에서 제외).
function checkIdentity(row) {
  const clpr = Number(row.clpr);
  const vs = Number(row.vs);
  const fltRt = Number(row.fltRt);
  if (!Number.isFinite(clpr) || !Number.isFinite(vs) || !Number.isFinite(fltRt)) return null;
  const prev = clpr - vs;
  // 전일 종가가 0이면 신규 상장 등으로 등락률이 정의되지 않는다.
  if (!(prev > 0)) return null;
  // 상한가·하한가 없이 변동이 0인 행은 항등식이 0=0이라 정보가 없다.
  if (vs === 0 && fltRt === 0) return null;

  const derived = (vs / prev) * 100;
  const gap = Math.abs(derived - fltRt);
  const tolerance = Math.max(FLT_RT_ABS_TOLERANCE, Math.abs(fltRt) * FLT_RT_REL_TOLERANCE);
  return { ok: gap <= tolerance, derived, gap, tolerance, prev };
}

// 표본을 검사한다. 하나라도 어긋나면 배치를 멈추고 원본 행을 통째로 찍는다 —
// 기본값으로 때우거나 0을 쓰면 화면에는 "변동 없음"으로 보인다.
function assertPreviousCloseFields(label, rows) {
  const sample = rows.slice(0, IDENTITY_SAMPLE);
  const checked = [];
  const failed = [];
  sample.forEach((row) => {
    const result = checkIdentity(row);
    if (!result) return;
    checked.push(result);
    if (!result.ok) failed.push({ row, result });
  });

  // 던지기 전에 원본 행을 통째로 찍는다. 예외 메시지만으로는 무엇이 달라졌는지
  // 알 수 없고, 이 검사가 걸리는 날은 대개 제공처가 조용히 무언가를 바꾼 날이다.
  const dump = (rows) => rows.forEach((row, index) => {
    console.error(`  [${label}] 원본 행 ${index + 1}: ${JSON.stringify(row)}`);
  });

  if (!checked.length) {
    // 검사할 수 있는 행이 하나도 없으면 필드가 없거나 이름이 바뀐 것이다.
    const first = sample[0];
    const message = `[${label}] 전일 종가 검증 불가: clpr·vs·fltRt를 모두 가진 행이 표본 ${sample.length}건 중 0건입니다. 필드명이 바뀌었는지 아래 원본 행을 확인하세요.`;
    console.error(message);
    dump(first ? [first] : []);
    throw new QuoteFieldError(message, first ? [first] : []);
  }

  if (failed.length) {
    const rows = failed.slice(0, 5).map((entry) => entry.row);
    const message = `[${label}] 전일 대비(vs)·등락률(fltRt) 항등식이 깨졌습니다: 검사 ${checked.length}건 중 ${failed.length}건 불일치. vs가 '전일 대비'가 아니거나 제공처가 의미를 바꿨을 수 있습니다. 전일 종가를 만들지 않고 중단합니다.`;
    console.error(message);
    failed.slice(0, 5).forEach((entry, index) => {
      console.error(`  [${label}] 불일치 ${index + 1}: 계산 ${entry.result.derived.toFixed(4)}%p vs 응답 ${entry.row.fltRt}%p (오차 ${entry.result.gap.toFixed(4)}, 허용 ${entry.result.tolerance.toFixed(4)})`);
    });
    dump(rows);
    throw new QuoteFieldError(message, rows);
  }

  const worst = checked.reduce((max, entry) => (entry.gap > max.gap ? entry : max), checked[0]);
  console.log(`[${label}] 전일 종가 항등식 통과: 표본 ${sample.length}건 중 검사 ${checked.length}건 · 최대 오차 ${worst.gap.toFixed(4)}%p (허용 ${worst.tolerance.toFixed(4)}%p)`);
  return { checked: checked.length, sampled: sample.length, worstGap: worst.gap };
}

// 검증을 통과한 행에서 전일 종가를 만든다. 못 만들면 null — 0이 아니다.
function previousCloseOf(row) {
  const clpr = Number(row.clpr);
  const vs = Number(row.vs);
  if (!Number.isFinite(clpr) || !Number.isFinite(vs)) return null;
  const prev = clpr - vs;
  return prev > 0 ? prev : null;
}

async function fetchStockQuotes(basDt) {
  const rows = await fetchAll(STOCK_URL, { basDt }, DATA_GO_KR_KEY);
  if (rows[0]) console.log("[raw-sample] stock:", JSON.stringify(rows[0]));
  assertPreviousCloseFields("주식시세정보", rows);
  const quotes = {};
  rows.forEach((row) => {
    const code = normalizeKrCode(row.srtnCd, row.isinCd);
    const price = Number(row.clpr);
    if (!code || !Number.isFinite(price) || price <= 0) return;
    const prevClose = previousCloseOf(row);
    // 전일 종가를 못 만들면 넣지 않는다. 0으로 채우면 화면에 "변동 없음"으로 보인다.
    quotes[code] = prevClose === null
      ? { price, currency: "KRW" }
      : { price, currency: "KRW", prevClose };
  });
  return quotes;
}

async function fetchEtfEtnQuotes(basDt) {
  const [etf, etn] = await Promise.all([
    fetchAll(ETF_URL, { basDt }, DATA_GO_KR_KEY).catch((error) => {
      console.warn(`ETF 시세 조회 실패, 스킵: ${error.message}`);
      return [];
    }),
    fetchAll(ETN_URL, { basDt }, DATA_GO_KR_KEY).catch((error) => {
      console.warn(`ETN 시세 조회 실패, 스킵: ${error.message}`);
      return [];
    }),
  ]);
  if (etf[0]) console.log("[raw-sample] etf:", JSON.stringify(etf[0]));
  if (etn[0]) console.log("[raw-sample] etn:", JSON.stringify(etn[0]));
  // 출처마다 따로 검증한다 — 필드가 같으리라 넘겨짚지 않는다.
  if (etf.length) assertPreviousCloseFields("증권상품시세정보(ETF)", etf);
  if (etn.length) assertPreviousCloseFields("증권상품시세정보(ETN)", etn);
  const quotes = {};
  [...etf, ...etn].forEach((row) => {
    const code = normalizeKrCode(row.srtnCd, row.isinCd);
    const price = Number(row.clpr);
    if (!code || !Number.isFinite(price) || price <= 0) return;
    const prevClose = previousCloseOf(row);
    quotes[code] = prevClose === null
      ? { price, currency: "KRW" }
      : { price, currency: "KRW", prevClose };
  });
  return quotes;
}

// 일반상품시세정보 > 금시세(KRX 금시장). 표준 거래단위가 1g 금 지금이 아닐 수 있어
// itmsNm/isuNm에서 "1g" 단위 상품을 우선 찾고, 없으면 가장 첫 행을 그대로 쓴다.
async function fetchGoldPerGram(basDt) {
  const rows = await fetchAll(GOLD_URL, { basDt }, DATA_GO_KR_KEY).catch((error) => {
    console.warn(`금시세 조회 실패, 스킵: ${error.message}`);
    return [];
  });
  if (!rows.length) return null;
  console.log("[raw-sample] gold:", JSON.stringify(rows[0]));
  const gram = rows.find((row) => /1\s*g/i.test(row.itmsNm || row.isuNm || "")) || rows[0];
  const price = Number(gram.clpr);
  if (!Number.isFinite(price) || price <= 0) return null;
  // 금도 같은 항등식으로 따로 검증한다. 이 출처만 필드가 다를 수 있다.
  assertPreviousCloseFields("일반상품시세정보(금)", rows);
  const prevClose = previousCloseOf(gram);
  return { price: Math.round(price), prevClose: prevClose === null ? null : Math.round(prevClose) };
}

// 데이터 없는 날짜(주말 등)에도 result:3("인증코드 오류") 한 건짜리 배열이 오고,
// length만 보면 이걸 정상 응답으로 오인해 소급 재시도가 아예 안 돈다(실측 확인됨).
// result===1(정상)인 행이 하나라도 있어야 유효한 응답으로 취급한다.
async function fetchFxRates(startDate) {
  let probe = startDate || kstToday();
  for (let attempt = 0; attempt < 10; attempt += 1) {
    const url = new URL(KOREAEXIM_FX_URL);
    url.searchParams.set("authkey", KOREAEXIM_AUTH_KEY);
    url.searchParams.set("searchdate", probe);
    url.searchParams.set("data", "AP01");
    const response = await fetchWithRetry("한국수출입은행 환율", url);
    const rows = await response.json();
    const validRows = Array.isArray(rows) ? rows.filter((row) => row.result === 1) : [];
    if (validRows.length) {
      console.log("[raw-sample] fx:", JSON.stringify(validRows[0]));
      const rates = { KRW: 1 };
      validRows.forEach((row) => {
        const unit = String(row.cur_unit || "").trim();
        const match = unit.match(/^([A-Z]{3})(?:\((\d+)\))?$/);
        if (!match) return;
        const [, code, divisor] = match;
        const base = Number(String(row.deal_bas_r || "").replace(/,/g, ""));
        if (!Number.isFinite(base) || base <= 0) return;
        rates[code] = divisor ? base / Number(divisor) : base;
      });
      return { asOfDate: probe, rates };
    }
    probe = shiftDate(probe, -1);
  }
  throw new Error("환율 API: 최근 10일 내 유효한(result===1) 데이터를 찾지 못했습니다");
}

// ECOS 은 시세는 선택 사항이다 — 통계표코드가 확정되지 않았거나 키가 없으면
// silverPerGram을 null로 두고 배치는 계속 진행한다 (부분 실패로 전체를 막지 않음).
async function fetchSilverPerGram(krwPerUsd) {
  if (!ECOS_AUTH_KEY || !ECOS_SILVER_STAT_CODE) {
    console.warn("ECOS_AUTH_KEY 또는 ECOS_SILVER_STAT_CODE 미설정 — 은 시세 스킵");
    return null;
  }
  try {
    const today = kstToday();
    const url = `${ECOS_URL_BASE}/StatisticSearch/${ECOS_AUTH_KEY}/json/kr/1/10/${ECOS_SILVER_STAT_CODE}/D/${shiftDate(today, -14)}/${today}`;
    const response = await fetchWithRetry("한국은행 ECOS(은)", url);
    const json = await response.json();
    const rows = json?.StatisticSearch?.row;
    if (!Array.isArray(rows) || !rows.length) return null;
    console.log("[raw-sample] silver:", JSON.stringify(rows[rows.length - 1]));
    const last = rows[rows.length - 1];
    const usdPerOunce = Number(last.DATA_VALUE);
    if (!Number.isFinite(usdPerOunce) || !krwPerUsd) return null;
    const GRAMS_PER_TROY_OUNCE = 31.1035;
    return Math.round((usdPerOunce * krwPerUsd) / GRAMS_PER_TROY_OUNCE);
  } catch (error) {
    console.warn(`은 시세 조회 실패, 스킵: ${error.message}`);
    return null;
  }
}

// 가상자산은 앱이 지원하는 심볼 전부를 매일 받는다(종목 수가 적어 한 번의 호출로 끝난다).
// 시세를 하나도 못 받으면 배치 실패다 — 예전에 MOCK에 BTC 158,200,000원이 박혀 있었고,
// 그런 하드코딩이 남아 있으면 호출 실패가 가짜 값으로 조용히 대체된다.
async function fetchCrypto() {
  const symbols = Object.keys(COINGECKO_IDS);
  if (!COINGECKO_API_KEY) {
    console.warn("COINGECKO_API_KEY 미설정 — 키 없이 공개 엔드포인트로 시도합니다(운영에서는 키를 설정하세요)");
  }
  const result = await fetchCryptoQuotes(symbols, COINGECKO_API_KEY, { plan: COINGECKO_PLAN });
  const got = Object.keys(result.quotes);
  if (got.length) console.log("[raw-sample] crypto:", JSON.stringify({ [got[0]]: result.quotes[got[0]] }));
  return { requested: symbols, ...result };
}

async function loadKrTickerCodes() {
  try {
    const raw = await readFile(resolve(DATA_DIR, "tickers-kr.json"), "utf8");
    const rows = JSON.parse(raw);
    const byType = { stock: new Set(), etf: new Set(), etn: new Set() };
    rows.forEach((row) => {
      if (byType[row.t] && row.c) byType[row.t].add(String(row.c));
    });
    return byType;
  } catch {
    return null; // tickers-kr.json이 아직 없으면 매칭률은 계산하지 않는다(빌드 순서 문제일 뿐 실패는 아님).
  }
}

// 매칭률의 분자는 반드시 "종목 목록과 시세의 교집합"이어야 한다. 시세 API가 돌려준
// 행 수를 그대로 분자로 쓰면, 목록에 없는 코드(실측 114건)까지 세어 104.1% 같은 값이
// 나온다. 그건 표기 오류가 아니라 검증의 무력화다 — 코드 접두어 버그가 재발해
// 교집합이 0이 되어도 시세 행 수는 그대로라 이 지표는 100%를 넘긴 채 통과한다.
function intersectionRate(tickerCodes, quoteKeys) {
  if (!tickerCodes || !tickerCodes.size) return null;
  let matched = 0;
  tickerCodes.forEach((code) => {
    if (quoteKeys.has(code)) matched += 1;
  });
  return { matched, total: tickerCodes.size, rate: matched / tickerCodes.size };
}

function formatMatch(label, match) {
  if (!match) return `${label} 시세 매칭: (tickers-kr.json 없어 미계산)`;
  return `${label} 시세 매칭: ${match.matched.toLocaleString("ko-KR")} / ${match.total.toLocaleString("ko-KR")} (${(match.rate * 100).toFixed(1)}%)`;
}

async function loadPrevious() {
  try {
    const raw = await readFile(resolve(DATA_DIR, "quotes.json"), "utf8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

// 실패(exit 1) 조건은 임계값 미만이면 절대 게시하면 안 되는 것들 — 인증이 죽었거나
// 코드 조인이 깨졌는데도 "성공"으로 끝나는 걸 막는다(이번에 실제로 두 번 겪었다).
// 경고는 게시는 하되 사람이 확인해야 하는 이상 신호다.
function validateQuotes({ quoteCount, matches, rateCount, goldPerGram, asOfIso, previousQuoteCount, unlistedQuoteCount, crypto, basDt, expectedBasDt }) {
  const failures = [];
  const warnings = [];

  // 주식·ETF·ETN을 각각 본다. 셋은 같은 코드 정규화 경로를 타므로 조인이 깨지면
  // 함께 무너지지만, 한쪽 엔드포인트만 죽는 경우를 합산 지표는 가려버린다.
  [["주식", matches.stock], ["ETF", matches.etf], ["ETN", matches.etn]].forEach(([label, match]) => {
    if (match && match.rate < 0.9) {
      failures.push(`${label} 시세 매칭률 ${(match.rate * 100).toFixed(1)}% (${match.matched}/${match.total}) — 90% 미만`);
    }
  });
  // 목록에 없는 시세 코드는 그 자체로는 정상이다(신규 상장 등 종목 목록이 하루 뒤진
  // 경우). 다만 급증하면 종목 목록 갱신이 멈췄다는 신호라 눈에 띄게 남긴다.
  if (unlistedQuoteCount > 300) {
    warnings.push(`종목 목록에 없는 시세 코드 ${unlistedQuoteCount}건 — 종목 목록(build-tickers.mjs) 갱신이 밀렸는지 확인하세요`);
  }
  if (rateCount < 10) failures.push(`환율 통화 수 ${rateCount}개 — 10개 미만`);

  // 가상자산은 국내 시세를 막지 않는다. 코인게코 한 곳이 흔들린다고 그날 국내 시세
  // 전체를 버리는 건 손실이 더 크다 — 직전 값을 그대로 들고 가고 경고만 남긴다.
  // 대신 그 값이 언제 것인지를 산출물에 적어 앱이 "며칠 전 값"이라고 말할 수 있게 한다.
  if (crypto) {
    if (crypto.stale) warnings.push(`가상자산 시세 갱신 실패 — 직전 값(${crypto.asOfDate || "날짜 미상"})을 유지합니다. 국내 시세는 정상 갱신했습니다`);
    else if (crypto.missing.length) warnings.push(`가상자산 시세 미확보 ${crypto.missing.length}종목: ${crypto.missing.join(", ")} — 해당 자산은 앱에서 "시세 확인 불가"로 표시됩니다`);
    if (crypto.unmapped.length) warnings.push(`코인게코 ID 매핑이 없는 심볼 ${crypto.unmapped.length}건: ${crypto.unmapped.join(", ")} — lib/crypto.mjs의 COINGECKO_IDS에 추가하세요`);
    // 직전 값조차 없으면 보여줄 게 없다. 그때만 실패로 본다.
    if (!Object.keys(crypto.quotes).length) failures.push(`가상자산 시세 0건 (요청 ${crypto.requested.length}종목, 보존할 직전 값도 없음)`);
  }
  if (!goldPerGram) failures.push("금 시세 확보 실패");
  const asOfAgeDays = Math.floor((Date.now() - new Date(asOfIso).getTime()) / 86400000);
  if (asOfAgeDays > 7) failures.push(`asOf가 ${asOfAgeDays}일 전 — 7일 초과`);

  // 배치는 데이터가 있는 날짜를 찾을 때까지 소급 조회하므로, 제공이 밀려도 실패하지
  // 않고 조용히 하루 뒤처진 값을 쓴다. 그게 정상인지(공휴일) 아닌지(제공 지연) 여기서는
  // 알 수 없으므로 실패가 아니라 경고로 남긴다 — 매일 뜨면 배치 시각을 다시 봐야 한다.
  if (basDt && expectedBasDt && basDt < expectedBasDt) {
    warnings.push(`시세 기준일이 ${basDt} — 직전 영업일(${expectedBasDt})보다 이릅니다. 공휴일이면 정상이지만, 매일 반복되면 제공 시각(다음 영업일 13시 이후)보다 배치가 이른지 확인하세요`);
  }
  if (quoteCount < 3000) failures.push(`전체 시세 건수 ${quoteCount}건 — 3,000건 미만`);

  if (previousQuoteCount) {
    const delta = Math.abs(quoteCount - previousQuoteCount) / previousQuoteCount;
    if (delta > 0.3) warnings.push(`시세 건수가 직전 산출물 대비 ${(delta * 100).toFixed(1)}% 변동 (${previousQuoteCount} -> ${quoteCount})`);
  }

  return { failures, warnings };
}

async function main() {
  if (!DATA_GO_KR_KEY) throw new Error("DATA_GO_KR_KEY 환경변수가 필요합니다");
  if (!KOREAEXIM_AUTH_KEY) throw new Error("KOREAEXIM_AUTH_KEY 환경변수가 필요합니다");

  await mkdir(DATA_DIR, { recursive: true });

  const stockBasDt = await resolveLatestBasDt(STOCK_URL, {}, DATA_GO_KR_KEY);
  const previous = await loadPrevious();
  const previousQuoteCount = previous ? Object.keys(previous.quotes || {}).length : null;
  const [stockQuotes, etfEtnQuotes, gold, fx, tickerCodes, crypto] = await Promise.all([
    fetchStockQuotes(stockBasDt),
    fetchEtfEtnQuotes(stockBasDt),
    fetchGoldPerGram(stockBasDt),
    fetchFxRates(stockBasDt),
    loadKrTickerCodes(),
    // 코인게코가 죽어도 국내 시세까지 버리지 않는다. 직전 산출물의 가상자산 값을
    // 그대로 들고 가고, 그게 언제 값인지(asOfDate)를 함께 남긴다.
    fetchCrypto().catch((error) => {
      console.warn(`가상자산 시세 조회 실패, 직전 값 유지: ${error.message}`);
      const kept = previous?.crypto || {};
      return {
        requested: Object.keys(COINGECKO_IDS),
        quotes: kept,
        unmapped: [],
        missing: Object.keys(COINGECKO_IDS),
        stale: true,
        asOfDate: previous?.cryptoAsOf || (previous?.asOf ? String(previous.asOf).slice(0, 10) : null),
      };
    }),
  ]);
  const silverPerGram = await fetchSilverPerGram(fx.rates.USD);

  const quotes = { ...stockQuotes, ...etfEtnQuotes };
  const quoteCount = Object.keys(quotes).length;
  const quoteKeys = new Set(Object.keys(quotes));

  const matches = {
    stock: intersectionRate(tickerCodes?.stock, quoteKeys),
    etf: intersectionRate(tickerCodes?.etf, quoteKeys),
    etn: intersectionRate(tickerCodes?.etn, quoteKeys),
  };
  const listedCodes = tickerCodes ? new Set([...tickerCodes.stock, ...tickerCodes.etf, ...tickerCodes.etn]) : null;
  const unlistedQuoteCount = listedCodes ? [...quoteKeys].filter((code) => !listedCodes.has(code)).length : 0;

  // asOf는 배치 실행 시각이 아니라 "이 데이터가 실제로 어느 날짜 기준인지"다.
  // 국내 시세 기준일(stockBasDt)과 환율 기준일이 다를 수 있어(공휴일 차이 등) 더 이른
  // 쪽을 화면 표기 기준으로 삼는다 — 더 늦은 쪽을 쓰면 아직 안 나온 데이터를
  // "반영됐다"고 주장하는 셈이 된다.
  const olderBasDt = stockBasDt < fx.asOfDate ? stockBasDt : fx.asOfDate;
  const asOfIso = `${olderBasDt.slice(0, 4)}-${olderBasDt.slice(4, 6)}-${olderBasDt.slice(6, 8)}T00:00:00+09:00`;

  // 전일 종가가 "어느 날"인지. 응답은 그걸 말해 주지 않고 clpr과 vs만 준다.
  // 주말만 건너뛰는 계산으로는 공휴일에서 틀리므로, 기준일 하루 전부터 데이터가
  // 있는 날까지 실제로 물어봐 직전 거래일을 확정한다. 화면이 "8월 12일 대비"라고
  // 적으려면 그 날짜가 추정이 아니라 데이터에서 나온 값이어야 한다.
  const prevBasDt = await resolveLatestBasDt(STOCK_URL, {}, DATA_GO_KR_KEY, {
    startDate: shiftDate(stockBasDt, -1),
    maxLookback: 5,
  }).catch((error) => {
    console.warn(`직전 거래일 확인 실패: ${error.message}`);
    return null;
  });
  const prevCloseDate = prevBasDt
    ? `${prevBasDt.slice(0, 4)}-${prevBasDt.slice(4, 6)}-${prevBasDt.slice(6, 8)}`
    : null;

  const payload = {
    asOf: asOfIso,
    // 전일 종가가 가리키는 날짜. 못 확정하면 null — 화면은 그때 "직전 거래일 대비"로
    // 적고 날짜를 지어내지 않는다.
    prevCloseDate,
    sources: {
      equity: "금융위원회_주식시세정보",
      etf: "금융위원회_증권상품시세정보",
      gold: gold ? "금융위원회_일반상품시세정보" : null,
      silver: silverPerGram ? "한국은행 ECOS" : null,
      fx: "한국수출입은행",
      // 코인게코 약관이 요구하는 출처 표기. 화면에도 "Data provided by CoinGecko"로 뜬다.
      crypto: Object.keys(crypto.quotes).length ? "CoinGecko" : null,
    },
    quotes,
    // 국내 종목 코드와 섞지 않고 따로 둔다 — 심볼 체계가 다르고, 섞으면 종목 목록
    // 교집합 검증에서 "목록에 없는 코드"로 잡혀 지표가 흐려진다.
    crypto: crypto.quotes,
    // 가상자산만 날짜가 다를 수 있다(코인게코 실패 시 직전 값 유지). 날짜가 섞인 데이터를
    // 한 시점의 스냅샷인 것처럼 내보내면 안 되므로 그룹별 기준일을 따로 적는다.
    // 앱은 이 값이 asOf보다 이르면 화면에 "며칠 전 값"이라고 밝힌다.
    cryptoAsOf: crypto.stale ? crypto.asOfDate : kstToday().replace(/(\d{4})(\d{2})(\d{2})/, "$1-$2-$3"),
    rates: fx.rates,
    commodities: {
      // 전일 종가도 함께 싣는다. 없으면 키 자체를 넣지 않는다 — 0은 "변동 없음"이 된다.
      ...(gold ? { goldPerGram: gold.price } : {}),
      ...(gold && gold.prevClose ? { goldPerGramPrev: gold.prevClose } : {}),
      ...(silverPerGram ? { silverPerGram } : {}),
    },
  };

  const rateCount = Object.keys(fx.rates).length;
  const { failures, warnings } = validateQuotes({
    quoteCount,
    matches,
    rateCount,
    goldPerGram: gold ? gold.price : null,
    asOfIso,
    previousQuoteCount,
    unlistedQuoteCount,
    crypto,
    basDt: stockBasDt,
    expectedBasDt: previousBusinessDay(kstToday()),
  });

  console.log(`asOf: ${payload.asOf} · 전일 종가 기준일: ${payload.prevCloseDate || "확인 불가"}`);
  console.log(formatMatch("국내 주식", matches.stock));
  console.log(formatMatch("ETF", matches.etf));
  console.log(formatMatch("ETN", matches.etn));
  console.log(`국내 주식·ETF·ETN 시세 확보: ${quoteCount.toLocaleString("ko-KR")}건 (종목 목록에 없는 코드 ${unlistedQuoteCount.toLocaleString("ko-KR")}건 포함)`);
  console.log(`금: ${gold ? `${gold.price.toLocaleString("ko-KR")}원/g (전일 ${gold.prevClose ? gold.prevClose.toLocaleString("ko-KR") + "원" : "확인 불가"})` : "확인 불가"}`);
  console.log(`은: ${silverPerGram ? `${silverPerGram.toLocaleString("ko-KR")}원/g` : "확인 불가"}`);
  console.log(`환율: ${rateCount}개 통화`);
  console.log(
    `가상자산: ${Object.keys(crypto.quotes).length} / ${crypto.requested.length}종목` +
      `${crypto.stale ? ` · 갱신 실패, ${crypto.asOfDate || "날짜 미상"} 값 유지` : ""}` +
      `${crypto.missing.length ? ` · 시세 미확보 ${crypto.missing.join(", ")}` : ""}` +
      `${crypto.unmapped.length ? ` · ID 매핑 없음 ${crypto.unmapped.join(", ")}` : ""}`
  );
  warnings.forEach((warning) => console.warn(`[경고] ${warning}`));

  if (failures.length) {
    console.error(`[검증 실패] 아래 조건에 걸려 data/quotes.json을 갱신하지 않습니다 (기존 파일 보존):`);
    failures.forEach((failure) => console.error(`  - ${failure}`));
    throw new Error(`검증 실패 ${failures.length}건`);
  }

  // 임시 파일에 먼저 쓰고 검증까지 통과한 뒤에만 원자적으로 교체한다 — 나쁜 배치가
  // 기존 정상 데이터를 덮어쓰지 않는다.
  const outPath = resolve(DATA_DIR, "quotes.json");
  const tmpPath = `${outPath}.tmp`;
  await writeFile(tmpPath, `${JSON.stringify(payload, null, 0)}\n`, "utf8");
  await rename(tmpPath, outPath);
}

// 실행이 끝났다는 신호. 이 스크립트는 import 시점에 main()을 띄우고 바로 반환하므로,
// 회귀 테스트가 "언제 끝났는지"를 알 방법이 이것밖에 없다. 고정 시간만 기다리게 하면
// 재시도가 붙은 실패 경로가 다음 실행과 겹쳐 exitCode와 콘솔이 뒤섞인다(실제로 겪었다).
// 운영에서는 아무 일도 하지 않는 카운터다.
main()
  .catch((error) => {
    console.error(`[시세 배치 실패] ${error.message}`);
    process.exitCode = 1;
  })
  .finally(() => {
    globalThis.__quotesBatchRuns = (globalThis.__quotesBatchRuns || 0) + 1;
  });
