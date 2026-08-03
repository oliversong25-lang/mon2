import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUTPUT = resolve(ROOT, "data", "tickers.json");
const SEC_URL = "https://www.sec.gov/files/company_tickers.json";
const KIND_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do";
const USER_AGENT = "AssetInputBeta ticker-builder contact: oliversong25-lang@users.noreply.github.com";
const MARKETS = [
  ["stockMkt", "KOSPI"],
  ["kosdaqMkt", "KOSDAQ"],
  ["konexMkt", "KONEX"]
];
const PRODUCT_ENDPOINTS = [
  ["dbms/MDC/STAT/standard/MDCSTAT04601", "etf"],
  ["dbms/MDC/STAT/standard/MDCSTAT06801", "etn"]
];
const US_EXCHANGES = { AAPL:"NASDAQ",MSFT:"NASDAQ",NVDA:"NASDAQ",AMZN:"NASDAQ",GOOGL:"NASDAQ",META:"NASDAQ",TSLA:"NASDAQ",NFLX:"NASDAQ",SPY:"NYSE",VOO:"NYSE",BRK_B:"NYSE" };
const KOREAN_NAMES = { AAPL:"애플",MSFT:"마이크로소프트",NVDA:"엔비디아",AMZN:"아마존",GOOGL:"알파벳",META:"메타",TSLA:"테슬라",NFLX:"넷플릭스" };

function decodeEntities(value) {
  return value.replace(/&nbsp;/gi," ").replace(/&amp;/gi,"&").replace(/&lt;/gi,"<").replace(/&gt;/gi,">").replace(/&quot;/gi,'"').replace(/&#39;/gi,"'").replace(/&#(\d+);/g,(_,code)=>String.fromCodePoint(Number(code))).trim();
}

function cells(row) {
  return [...row.matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi)].map(match=>decodeEntities(match[1].replace(/<[^>]+>/g," ").replace(/\s+/g," ")));
}

async function fetchKrxStocks(marketType, market) {
  const url = new URL(KIND_URL);
  url.searchParams.set("method", "download");
  url.searchParams.set("searchType", "13");
  url.searchParams.set("marketType", marketType);
  const response = await fetch(url, { headers:{ "User-Agent":USER_AGENT, Referer:"https://kind.krx.co.kr/corpgeneral/corpList.do?method=loadInitPage" } });
  if (!response.ok) throw new Error(`KIND ${market}: ${response.status}`);
  const html = new TextDecoder("euc-kr").decode(await response.arrayBuffer());
  return [...html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)].map(match=>cells(match[1])).filter(row=>/^\d{6}$/.test(row[1]||"")).map((row,index)=>({ c:row[1], n:row[0], m:market, t:"stock", r:index+1 }));
}

async function fetchKrxProducts(bld, type) {
  const body = new URLSearchParams({ bld, locale:"ko_KR", trdDd:"", share:"1", money:"1", csvxls_isNo:"false" });
  const response = await fetch("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd", { method:"POST", headers:{ "User-Agent":USER_AGENT, "Content-Type":"application/x-www-form-urlencoded; charset=UTF-8", Referer:"https://data.krx.co.kr/" }, body });
  if (!response.ok) throw new Error(`KRX ${type}: ${response.status}`);
  const json = await response.json();
  return (json.OutBlock_1||[]).map((row,index)=>({ c:row.ISU_SRT_CD||row.ISU_CD, n:row.ISU_ABBRV||row.ISU_NM, m:row.MKT_NM||"KOSPI", t:type, r:index+1 })).filter(row=>row.c&&row.n);
}

async function fetchSecCompanies() {
  const response = await fetch(SEC_URL, { headers:{ "User-Agent":USER_AGENT, Accept:"application/json" } });
  if (!response.ok) throw new Error(`SEC: ${response.status}`);
  const json = await response.json();
  return Object.values(json).map((row,index)=>{const code=String(row.ticker).toUpperCase();const english=String(row.title).trim();const korean=KOREAN_NAMES[code];return { c:code, n:korean||english, ...(korean?{e:english}:{}), m:US_EXCHANGES[code]||"US", t:"stock", r:index+1 };});
}

async function main() {
  const results = await Promise.allSettled([
    ...MARKETS.map(([marketType,market])=>fetchKrxStocks(marketType,market)),
    ...PRODUCT_ENDPOINTS.map(([bld,type])=>fetchKrxProducts(bld,type)),
    fetchSecCompanies()
  ]);
  const failures = results.filter(result=>result.status==="rejected");
  if (failures.length) failures.forEach(result=>console.warn(result.reason.message));
  const rows = results.flatMap(result=>result.status==="fulfilled"?result.value:[]);
  if (rows.length<1000) throw new Error(`종목 수집 결과가 너무 적습니다: ${rows.length}건`);
  const merged = new Map();
  rows.forEach(row=>merged.set(`${row.m}:${row.c}`,row));
  const output = [...merged.values()].sort((a,b)=>Number(!["KOSPI","KOSDAQ","KONEX"].includes(a.m))-Number(!["KOSPI","KOSDAQ","KONEX"].includes(b.m))||a.r-b.r||a.c.localeCompare(b.c));
  await mkdir(dirname(OUTPUT), { recursive:true });
  await writeFile(OUTPUT, `${JSON.stringify(output)}\n`, "utf8");
  console.log(`data/tickers.json 생성 완료: ${output.length.toLocaleString("ko-KR")}건`);
}

await main();
