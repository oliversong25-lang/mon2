// scripts/lib/data-go-kr.mjs
// Shared paging client for 금융위원회 계열 공공데이터포털(data.go.kr) OpenAPI services.
// All of these APIs share the same envelope shape (response.body.items[], .totalCount)
// and the same numOfRows/pageNo paging convention, so build-tickers.mjs and
// build-quotes.mjs both page through them with this one helper.

import { execSync } from "node:child_process";
import { fetchWithRetry, redactUrl } from "./http.mjs";

const USER_AGENT = "AssetInputBeta data.go.kr client contact: oliversong25-lang@users.noreply.github.com";

// 오류 메시지가 어느 서비스인지 말하게 한다. URL만으로는 사람이 못 알아본다.
const SERVICE_LABELS = [
  ["GetStockSecuritiesInfoService", "금융위원회_주식시세정보"],
  ["getETFPriceInfo", "금융위원회_증권상품시세정보(ETF)"],
  ["getETNPriceInfo", "금융위원회_증권상품시세정보(ETN)"],
  ["GetGeneralProductInfoService", "금융위원회_일반상품시세정보(금)"],
  ["GetKrxListedInfoService", "금융위원회_KRX상장종목정보"],
];
export function serviceLabel(baseUrl) {
  const hit = SERVICE_LABELS.find(([needle]) => String(baseUrl).includes(needle));
  return hit ? hit[1] : `data.go.kr(${String(baseUrl).split("/").pop()})`;
}

export class DataGoKrError extends Error {
  constructor(message, { resultCode, resultMsg, url } = {}) {
    super(message);
    this.name = "DataGoKrError";
    this.resultCode = resultCode;
    this.resultMsg = resultMsg;
    this.url = url;
  }
}

async function fetchPage(baseUrl, params, serviceKey) {
  const url = new URL(baseUrl);
  url.searchParams.set("serviceKey", serviceKey);
  url.searchParams.set("resultType", "json");
  Object.entries(params).forEach(([key, value]) => {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, String(value));
  });
  const label = serviceLabel(baseUrl);
  const safeUrl = redactUrl(url);
  // 네트워크 오류·5xx는 fetchWithRetry가 몇 번 다시 시도하고, 그래도 안 되면
  // 어느 API였는지가 메시지에 담겨 올라온다.
  const response = await fetchWithRetry(label, url, { headers: { "User-Agent": USER_AGENT } });
  const text = await response.text();
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    // data.go.kr returns an XML error envelope (not JSON) for auth/quota failures
    // even when resultType=json is requested — surface the raw body for diagnosis.
    throw new DataGoKrError(`[${label}] non-JSON response (likely an auth/quota error) (${safeUrl}): ${text.slice(0, 300)}`, { url: safeUrl });
  }
  const header = json?.response?.header;
  if (header && header.resultCode !== "00" && header.resultCode !== undefined) {
    throw new DataGoKrError(`[${label}] ${header.resultMsg || "unknown error"} (resultCode=${header.resultCode}) (${safeUrl})`, {
      resultCode: header.resultCode,
      resultMsg: header.resultMsg,
      url: safeUrl,
    });
  }
  const body = json?.response?.body;
  const items = body?.items?.item ?? body?.items ?? [];
  return { items: Array.isArray(items) ? items : [items].filter(Boolean), totalCount: Number(body?.totalCount || 0) };
}

// Pages through numOfRows-limited results until totalCount is exhausted.
// pageSize defaults to 1000 — large enough to keep call counts low but well under
// the portal's ~1 minute per-request timeout for these dataset sizes.
export async function fetchAll(baseUrl, params, serviceKey, { pageSize = 1000 } = {}) {
  const first = await fetchPage(baseUrl, { ...params, numOfRows: pageSize, pageNo: 1 }, serviceKey);
  const rows = [...first.items];
  const totalPages = Math.ceil(first.totalCount / pageSize);
  for (let page = 2; page <= totalPages; page += 1) {
    const next = await fetchPage(baseUrl, { ...params, numOfRows: pageSize, pageNo: page }, serviceKey);
    rows.push(...next.items);
  }
  return rows;
}

// Cheap existence check — a single numOfRows=1 request, NOT a full page-through.
// Use this (not fetchAll) when all you need is "does this basDt have any data at
// all", e.g. walking backward over a date to find the latest trading day. Calling
// fetchAll with a tiny pageSize for that purpose is a trap: fetchAll's totalPages
// math (totalCount / pageSize) means a small pageSize against a 4,000+ row day
// pages through the ENTIRE dataset just to answer a yes/no question.
export async function peek(baseUrl, params, serviceKey) {
  const { totalCount } = await fetchPage(baseUrl, { ...params, numOfRows: 1, pageNo: 1 }, serviceKey);
  return totalCount > 0;
}

export function kstToday() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" })
    .format(new Date())
    .replace(/-/g, "");
}

export function shiftDate(yyyymmdd, days) {
  const y = Number(yyyymmdd.slice(0, 4));
  const m = Number(yyyymmdd.slice(4, 6)) - 1;
  const d = Number(yyyymmdd.slice(6, 8));
  const date = new Date(Date.UTC(y, m, d));
  date.setUTCDate(date.getUTCDate() + days);
  return `${date.getUTCFullYear()}${String(date.getUTCMonth() + 1).padStart(2, "0")}${String(date.getUTCDate()).padStart(2, "0")}`;
}

// 금융위원회 시세 API는 기준일 다음 영업일 13시 이후 갱신되고 주말·공휴일은 데이터가
// 없다. 오늘부터 최대 maxLookback일 역순으로 실제 데이터가 있는 날짜를 찾는다.
export async function resolveLatestBasDt(baseUrl, extraParams, serviceKey, { maxLookback = 10, startDate } = {}) {
  let probe = startDate || kstToday();
  for (let attempt = 0; attempt < maxLookback; attempt += 1) {
    if (await peek(baseUrl, { ...extraParams, basDt: probe }, serviceKey)) return probe;
    probe = shiftDate(probe, -1);
  }
  throw new Error(`${baseUrl}: 최근 ${maxLookback}일 내 데이터를 찾지 못했습니다 (마지막 시도: ${probe})`);
}

// 직전 영업일(주말 제외). 공휴일은 알 수 없으므로 여기서 나온 날짜와 실제 기준일이
// 다르다고 해서 실패로 보지 않는다 — 경고의 근거로만 쓴다.
export function previousBusinessDay(yyyymmdd) {
  let probe = shiftDate(yyyymmdd, -1);
  for (let attempt = 0; attempt < 7; attempt += 1) {
    const date = new Date(Date.UTC(Number(probe.slice(0, 4)), Number(probe.slice(4, 6)) - 1, Number(probe.slice(6, 8))));
    const day = date.getUTCDay();
    if (day !== 0 && day !== 6) return probe;
    probe = shiftDate(probe, -1);
  }
  return probe;
}

// 금융위원회_KRX상장종목정보(15094775)의 srtnCd는 "A005930"처럼 접두어가 붙지만
// 주식시세정보(15094808)의 srtnCd는 "005930"처럼 붙지 않는다 — 같은 종목인데 두
// 엔드포인트의 코드 형식이 다르다(실제 라이브 응답으로 확인됨). 접두어를 무조건
// 잘라내는 대신, 국내 표준 ISIN(KR7 + 6자리코드 + 검사숫자)이면 isinCd에서 6자리를
// 복원해 쓴다 — 이게 두 엔드포인트 모두에서 항상 같은 값을 준다는 걸 보장하는
// 유일한 방법이다. 외국적 상장사처럼 KR7 형식이 아닌 isinCd(홍콩 상장사의
// HK... 등)는 여기서 6자리를 복원할 수 없으므로, 그 경우에만 srtnCd의 앞자리
// 알파벳 접두어를 제거하는 것으로 대체한다(확인된 전용 케이스).
export function normalizeKrCode(srtnCd, isinCd) {
  const krIsin = /^KR7(\d{6})\d$/.exec(String(isinCd || ""));
  if (krIsin) return krIsin[1];
  return String(srtnCd || "").replace(/^[A-Z]+/, "");
}

// Node는 Windows에서 콘솔 코드페이지를 자동으로 UTF-8(65001)로 바꾸지 않아 PowerShell
// 등에서 한글 로그가 깨진다. 대화형 콘솔에서만, 그리고 실패해도 배치 자체를 막지
// 않도록 조용히 시도한다 — CI(리눅스)에서는 아무 일도 하지 않는다.
export function setupUtf8Console() {
  if (process.platform !== "win32" || !process.stdout.isTTY) return;
  try {
    execSync("chcp 65001", { stdio: "ignore" });
  } catch {
    // 콘솔 코드페이지를 못 바꿔도 배치 자체는 계속 진행한다 — 로그 가독성 문제일 뿐이다.
  }
}
