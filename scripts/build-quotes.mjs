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
import { fetchAll, kstToday, shiftDate, resolveLatestBasDt, normalizeKrCode, setupUtf8Console } from "./lib/data-go-kr.mjs";

setupUtf8Console();

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DATA_DIR = resolve(ROOT, "data");

const DATA_GO_KR_KEY = process.env.DATA_GO_KR_KEY;
const KOREAEXIM_AUTH_KEY = process.env.KOREAEXIM_AUTH_KEY;
const ECOS_AUTH_KEY = process.env.ECOS_AUTH_KEY; // optional — silver is skipped (not failed) without it

const STOCK_URL = "https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo";
const ETF_URL = "https://apis.data.go.kr/1160100/service/GetSecuritiesProductInfoService/getETFPriceInfo";
const ETN_URL = "https://apis.data.go.kr/1160100/service/GetSecuritiesProductInfoService/getETNPriceInfo";
const GOLD_URL = "https://apis.data.go.kr/1160100/service/GetGeneralProductInfoService/getGoldPriceInfo";
const KOREAEXIM_FX_URL = "https://oapi.koreaexim.go.kr/site/program/financial/exchangeJSON";
// ECOS 통계표코드는 실제 키 발급 후 StatisticItemList(통계코드검색)로 "귀금속" 또는 "은"
// 키워드로 조회해 확정해야 한다 — 아래 값은 미확정 자리표시자다.
const ECOS_SILVER_STAT_CODE = process.env.ECOS_SILVER_STAT_CODE || "";
const ECOS_URL_BASE = "https://ecos.bok.or.kr/api";

async function fetchStockQuotes(basDt) {
  const rows = await fetchAll(STOCK_URL, { basDt }, DATA_GO_KR_KEY);
  if (rows[0]) console.log("[raw-sample] stock:", JSON.stringify(rows[0]));
  const quotes = {};
  rows.forEach((row) => {
    const code = normalizeKrCode(row.srtnCd, row.isinCd);
    const price = Number(row.clpr);
    if (!code || !Number.isFinite(price) || price <= 0) return;
    quotes[code] = { price, currency: "KRW" };
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
  const quotes = {};
  [...etf, ...etn].forEach((row) => {
    const code = normalizeKrCode(row.srtnCd, row.isinCd);
    const price = Number(row.clpr);
    if (!code || !Number.isFinite(price) || price <= 0) return;
    quotes[code] = { price, currency: "KRW" };
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
  return Number.isFinite(price) && price > 0 ? Math.round(price) : null;
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
    const response = await fetch(url);
    if (!response.ok) throw new Error(`환율 API HTTP ${response.status}`);
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
    const response = await fetch(url);
    if (!response.ok) throw new Error(`ECOS HTTP ${response.status}`);
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

async function loadPreviousQuoteCount() {
  try {
    const raw = await readFile(resolve(DATA_DIR, "quotes.json"), "utf8");
    const previous = JSON.parse(raw);
    return Object.keys(previous.quotes || {}).length;
  } catch {
    return null;
  }
}

// 실패(exit 1) 조건은 임계값 미만이면 절대 게시하면 안 되는 것들 — 인증이 죽었거나
// 코드 조인이 깨졌는데도 "성공"으로 끝나는 걸 막는다(이번에 실제로 두 번 겪었다).
// 경고는 게시는 하되 사람이 확인해야 하는 이상 신호다.
function validateQuotes({ quoteCount, matches, rateCount, goldPerGram, asOfIso, previousQuoteCount, unlistedQuoteCount }) {
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
  if (!goldPerGram) failures.push("금 시세 확보 실패");
  const asOfAgeDays = Math.floor((Date.now() - new Date(asOfIso).getTime()) / 86400000);
  if (asOfAgeDays > 7) failures.push(`asOf가 ${asOfAgeDays}일 전 — 7일 초과`);
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
  const [stockQuotes, etfEtnQuotes, goldPerGram, fx, tickerCodes, previousQuoteCount] = await Promise.all([
    fetchStockQuotes(stockBasDt),
    fetchEtfEtnQuotes(stockBasDt),
    fetchGoldPerGram(stockBasDt),
    fetchFxRates(stockBasDt),
    loadKrTickerCodes(),
    loadPreviousQuoteCount(),
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

  const payload = {
    asOf: asOfIso,
    sources: {
      equity: "금융위원회_주식시세정보",
      etf: "금융위원회_증권상품시세정보",
      gold: goldPerGram ? "금융위원회_일반상품시세정보" : null,
      silver: silverPerGram ? "한국은행 ECOS" : null,
      fx: "한국수출입은행",
    },
    quotes,
    rates: fx.rates,
    commodities: {
      ...(goldPerGram ? { goldPerGram } : {}),
      ...(silverPerGram ? { silverPerGram } : {}),
    },
  };

  const rateCount = Object.keys(fx.rates).length;
  const { failures, warnings } = validateQuotes({
    quoteCount,
    matches,
    rateCount,
    goldPerGram,
    asOfIso,
    previousQuoteCount,
    unlistedQuoteCount,
  });

  console.log(`asOf: ${payload.asOf}`);
  console.log(formatMatch("국내 주식", matches.stock));
  console.log(formatMatch("ETF", matches.etf));
  console.log(formatMatch("ETN", matches.etn));
  console.log(`국내 주식·ETF·ETN 시세 확보: ${quoteCount.toLocaleString("ko-KR")}건 (종목 목록에 없는 코드 ${unlistedQuoteCount.toLocaleString("ko-KR")}건 포함)`);
  console.log(`금: ${goldPerGram ? `${goldPerGram.toLocaleString("ko-KR")}원/g` : "확인 불가"}`);
  console.log(`은: ${silverPerGram ? `${silverPerGram.toLocaleString("ko-KR")}원/g` : "확인 불가"}`);
  console.log(`환율: ${rateCount}개 통화`);
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

main().catch((error) => {
  console.error(`[시세 배치 실패] ${error.message}`);
  process.exitCode = 1;
});
