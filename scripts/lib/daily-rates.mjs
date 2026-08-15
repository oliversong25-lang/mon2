// scripts/lib/daily-rates.mjs
// 매일 바뀌는 금리 세 출처. OECD 계열(월·분기)과 성격이 달라 배치를 따로 둔다.
//
// 실측으로 확인한 것들:
//
//  1) ECOS는 통계표 834개 중 일간(D) 주기가 7개뿐이다. 그중 금리는 두 개다 —
//     722Y001(기준금리·여수신금리), 817Y002(시장금리 일별).
//  2) 같은 배치 안에서도 계열마다 최신 관측일이 다르다. 2026-08-14 실측으로 국채는
//     08-14까지, 기준금리·콜금리는 08-13까지였다. 그래서 계열별 period를 그대로 싣는다.
//  3) 미 재무부 일별 국채 수익률은 Atom XML이고 <entry> 하나가 하루다. 키가 없고
//     미국 정부 저작물이라 저작권 표시가 붙지 않는다. FRED는 이걸 재배포할 뿐이라
//     쓰지 않는다 — 라이선스 판단을 하나 없앤다.
//  4) 뉴욕 연준 API는 EFFR과 FOMC 목표범위를 **한 행에** 준다
//     (percentRate, targetRateFrom, targetRateTo). EFFR을 산출·공표하는 주체가
//     뉴욕 연준이므로 여기가 1차 출처다.

import { fetchWithRetry } from "./http.mjs";

export const SOURCE_ECOS = "ECOS";
export const SOURCE_TREASURY = "USTREASURY";
export const SOURCE_NYFED = "NYFED";

const ECOS_BASE = "https://ecos.bok.or.kr/api";
const TREASURY_XML = "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml";
const NYFED_RATES = "https://markets.newyorkfed.org/api/rates/all/latest.json";

// ── ECOS ────────────────────────────────────────────────────────────────────
// 항목 코드는 StatisticItemList로 확인한 값이다. 추측한 코드는 예외가 아니라
// 관측 0건으로 조용히 사라진다.
export const ECOS_SERIES = [
  { id: "kr-base-rate", table: "722Y001", item: "0101000", nameKo: "한국은행 기준금리", nameEn: "BOK base rate" },
  { id: "kr-call-rate", table: "817Y002", item: "010101000", nameKo: "콜금리(1일)", nameEn: "Call rate (overnight)" },
  { id: "kr-tb-1y", table: "817Y002", item: "010190000", nameKo: "국고채 1년", nameEn: "Treasury bond 1Y" },
  { id: "kr-tb-2y", table: "817Y002", item: "010195000", nameKo: "국고채 2년", nameEn: "Treasury bond 2Y" },
  { id: "kr-tb-3y", table: "817Y002", item: "010200000", nameKo: "국고채 3년", nameEn: "Treasury bond 3Y" },
  { id: "kr-tb-5y", table: "817Y002", item: "010200001", nameKo: "국고채 5년", nameEn: "Treasury bond 5Y" },
  { id: "kr-tb-10y", table: "817Y002", item: "010210000", nameKo: "국고채 10년", nameEn: "Treasury bond 10Y" },
  { id: "kr-tb-30y", table: "817Y002", item: "010230000", nameKo: "국고채 30년", nameEn: "Treasury bond 30Y" },
  { id: "kr-corp-aa", table: "817Y002", item: "010300000", nameKo: "회사채 3년(AA−)", nameEn: "Corporate bond 3Y (AA-)" },
  { id: "kr-corp-bbb", table: "817Y002", item: "010320000", nameKo: "회사채 3년(BBB−)", nameEn: "Corporate bond 3Y (BBB-)" },
  { id: "kr-cd-91", table: "817Y002", item: "010502000", nameKo: "CD 91일", nameEn: "CD 91-day" },
  { id: "kr-cp-91", table: "817Y002", item: "010503000", nameKo: "CP 91일", nameEn: "CP 91-day" },
];

function ecosDate(offsetDays) {
  const at = new Date(Date.now() + offsetDays * 86400000);
  return at.toLocaleDateString("en-CA", { timeZone: "Asia/Seoul" }).replace(/-/g, "");
}

// ECOS는 오류를 200에 담아 보내기도 한다(RESULT.CODE). 상태 코드만 보면 성공으로 읽힌다.
function ecosError(json) {
  const result = json && json.RESULT;
  if (result && result.CODE && result.CODE !== "INFO-000") {
    return `${result.CODE}: ${result.MESSAGE || "알 수 없는 오류"}`;
  }
  return null;
}

export async function fetchEcosSeries(apiKey, { days = 400 } = {}) {
  if (!apiKey) throw new Error("ECOS 인증키가 없습니다 (ECOS_AUTH_KEY)");
  const start = ecosDate(-days);
  const end = ecosDate(1);
  const out = [];
  for (const series of ECOS_SERIES) {
    const url = `${ECOS_BASE}/StatisticSearch/${apiKey}/json/kr/1/2000/${series.table}/D/${start}/${end}/${series.item}`;
    const response = await fetchWithRetry(`ECOS ${series.id}`, url);
    const json = await response.json();
    const failure = ecosError(json);
    if (failure) throw new Error(`[ECOS ${series.id}] ${failure}`);
    const rows = json?.StatisticSearch?.row || [];
    const observations = rows
      .map((row) => [String(row.TIME), Number(row.DATA_VALUE)])
      .filter(([period, value]) => /^\d{8}$/.test(period) && Number.isFinite(value))
      .map(([period, value]) => [`${period.slice(0, 4)}-${period.slice(4, 6)}-${period.slice(6, 8)}`, value])
      .sort((a, b) => a[0].localeCompare(b[0]));
    // 0건을 성공으로 넘기면 지표가 조용히 사라진다.
    if (!observations.length) throw new Error(`[ECOS ${series.id}] 관측 0건 (통계표 ${series.table} 항목 ${series.item})`);
    out.push({ ...series, unitKo: "% (연율)", freq: "D", country: "KOR", source: SOURCE_ECOS, observations });
  }
  return out;
}

// ── 미 재무부 일별 국채 수익률 ────────────────────────────────────────────────
export const TREASURY_SERIES = [
  { id: "us-tb-1m", field: "BC_1MONTH", nameKo: "미 국채 1개월", nameEn: "US Treasury 1M" },
  { id: "us-tb-3m", field: "BC_3MONTH", nameKo: "미 국채 3개월", nameEn: "US Treasury 3M" },
  { id: "us-tb-6m", field: "BC_6MONTH", nameKo: "미 국채 6개월", nameEn: "US Treasury 6M" },
  { id: "us-tb-1y", field: "BC_1YEAR", nameKo: "미 국채 1년", nameEn: "US Treasury 1Y" },
  { id: "us-tb-2y", field: "BC_2YEAR", nameKo: "미 국채 2년", nameEn: "US Treasury 2Y" },
  { id: "us-tb-3y", field: "BC_3YEAR", nameKo: "미 국채 3년", nameEn: "US Treasury 3Y" },
  { id: "us-tb-5y", field: "BC_5YEAR", nameKo: "미 국채 5년", nameEn: "US Treasury 5Y" },
  { id: "us-tb-7y", field: "BC_7YEAR", nameKo: "미 국채 7년", nameEn: "US Treasury 7Y" },
  { id: "us-tb-10y", field: "BC_10YEAR", nameKo: "미 국채 10년", nameEn: "US Treasury 10Y" },
  { id: "us-tb-20y", field: "BC_20YEAR", nameKo: "미 국채 20년", nameEn: "US Treasury 20Y" },
  { id: "us-tb-30y", field: "BC_30YEAR", nameKo: "미 국채 30년", nameEn: "US Treasury 30Y" },
];

// Atom XML을 정규식으로 읽는다. 파서를 들이지 않는 이유는 이 프로젝트에 빌드 스텝이
// 없기 때문이고, 구조가 <entry> 안의 <d:필드>로 단순하기 때문이다.
export function parseTreasuryXml(xml) {
  const entries = xml.split("<entry>").slice(1);
  const byDate = [];
  entries.forEach((entry) => {
    const date = /<d:NEW_DATE[^>]*>([^<]+)</.exec(entry);
    if (!date) return;
    const day = date[1].slice(0, 10);
    const row = { date: day };
    TREASURY_SERIES.forEach((series) => {
      const match = new RegExp(`<d:${series.field}[^>]*>([^<]*)<`).exec(entry);
      const value = match ? Number(match[1]) : NaN;
      if (Number.isFinite(value)) row[series.id] = value;
    });
    byDate.push(row);
  });
  return byDate.sort((a, b) => a.date.localeCompare(b.date));
}

export async function fetchTreasuryYields(year) {
  const url = `${TREASURY_XML}?data=daily_treasury_yield_curve&field_tdr_date_value=${year}`;
  const response = await fetchWithRetry("미 재무부 국채수익률", url);
  const xml = await response.text();
  const rows = parseTreasuryXml(xml);
  if (!rows.length) throw new Error("미 재무부 응답에서 <entry>를 찾지 못했습니다 (피드 구조가 바뀌었는지 확인하세요)");
  return TREASURY_SERIES.map((series) => {
    const observations = rows
      .filter((row) => Number.isFinite(row[series.id]))
      .map((row) => [row.date, row[series.id]]);
    return { ...series, unitKo: "% (연율)", freq: "D", country: "USA", source: SOURCE_TREASURY, observations };
  }).filter((series) => series.observations.length);
}

// ── 뉴욕 연준 ────────────────────────────────────────────────────────────────
// EFFR을 산출·공표하는 주체가 뉴욕 연준이다. 같은 행에 FOMC 목표범위가 함께 온다.
export async function fetchFedRates() {
  const response = await fetchWithRetry("뉴욕 연준 기준금리", NYFED_RATES);
  const json = await response.json();
  const effr = (json.refRates || []).find((row) => row.type === "EFFR");
  if (!effr || !Number.isFinite(Number(effr.percentRate))) {
    throw new Error("뉴욕 연준 응답에 EFFR이 없습니다 (refRates 구조가 바뀌었는지 확인하세요)");
  }
  const date = String(effr.effectiveDate).slice(0, 10);
  const series = [{
    id: "us-effr", nameKo: "연준 실효금리(EFFR)", nameEn: "Effective Federal Funds Rate",
    unitKo: "% (연율)", freq: "D", country: "USA", source: SOURCE_NYFED,
    observations: [[date, Number(effr.percentRate)]],
  }];
  // 목표범위는 상단만 실으면 "금리가 3.75%"로 읽힌다. 상·하단을 따로 둔다.
  if (Number.isFinite(Number(effr.targetRateFrom)) && Number.isFinite(Number(effr.targetRateTo))) {
    series.push({
      id: "us-fomc-target-low", nameKo: "FOMC 목표범위 하단", nameEn: "FOMC target range (lower)",
      unitKo: "% (연율)", freq: "D", country: "USA", source: SOURCE_NYFED,
      observations: [[date, Number(effr.targetRateFrom)]],
    });
    series.push({
      id: "us-fomc-target-high", nameKo: "FOMC 목표범위 상단", nameEn: "FOMC target range (upper)",
      unitKo: "% (연율)", freq: "D", country: "USA", source: SOURCE_NYFED,
      observations: [[date, Number(effr.targetRateTo)]],
    });
  }
  return series;
}
