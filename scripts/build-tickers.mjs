import { mkdir, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";

const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const DATA_DIR=resolve(ROOT,"data");
const USER_AGENT="AssetInputBeta ticker-builder contact: oliversong25-lang@users.noreply.github.com";
const KIND_URL="https://kind.krx.co.kr/corpgeneral/corpList.do";
const NASDAQ_URL="https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt";
const OTHER_US_URL="https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt";
const MARKET_CURRENCY={KOSPI:"KRW",KOSDAQ:"KRW",KONEX:"KRW",NASDAQ:"USD",NYSE:"USD",AMEX:"USD","NYSE Arca":"USD",BATS:"USD",TSE:"JPY",HKEX:"HKD",SSE:"CNY",SZSE:"CNY",TWSE:"TWD",LSE:"GBP",XETRA:"EUR","Euronext Paris":"EUR",NSE:"INR",TSX:"CAD",ASX:"AUD",SGX:"SGD",HOSE:"VND"};
const KIND_MARKETS=[["stockMkt","KOSPI"],["kosdaqMkt","KOSDAQ"],["konexMkt","KONEX"]];
const EXCHANGE={N:"NYSE",P:"NYSE Arca",A:"AMEX",Z:"BATS",Q:"NASDAQ",G:"NASDAQ",S:"NASDAQ"};

// 공식 목록을 자동 수집하기 어려운 시장은 대표 종목 seed를 유지한다.
// 실제 배포 빌드에서는 각 거래소의 상위 200종목 번들을 이 배열에 갱신한다.
const GLOBAL_STATIC=[
  {c:"7203",n:"도요타자동차",e:"Toyota Motor Corporation",m:"TSE",cur:"JPY",t:"stock",x:1000000000000},
  {c:"6758",n:"소니그룹",e:"Sony Group Corporation",m:"TSE",cur:"JPY",t:"stock",x:800000000000},
  {c:"0700",n:"텐센트",e:"Tencent Holdings",m:"HKEX",cur:"HKD",t:"stock",x:900000000000},
  {c:"9988",n:"알리바바",e:"Alibaba Group",m:"HKEX",cur:"HKD",t:"stock",x:700000000000},
  {c:"2330",n:"TSMC",e:"Taiwan Semiconductor Manufacturing",m:"TWSE",cur:"TWD",t:"stock",x:1200000000000},
  {c:"SHEL",n:"Shell plc",m:"LSE",cur:"GBP",t:"stock"},
  {c:"SAP",n:"SAP SE",m:"XETRA",cur:"EUR",t:"stock"},
  {c:"RELIANCE",n:"Reliance Industries",m:"NSE",cur:"INR",t:"stock"},
  {c:"SHOP",n:"Shopify",m:"TSX",cur:"CAD",t:"stock"},
  {c:"BHP",n:"BHP Group",m:"ASX",cur:"AUD",t:"stock"}
];

function decodeEntities(value){return value.replace(/&nbsp;/gi," ").replace(/&amp;/gi,"&").replace(/&lt;/gi,"<").replace(/&gt;/gi,">").replace(/&quot;/gi,'"').replace(/&#39;/gi,"'").trim();}
function cells(row){return [...row.matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi)].map(match=>decodeEntities(match[1].replace(/<[^>]+>/g," ").replace(/\s+/g," ")));}
async function fetchText(url,headers={}){const response=await fetch(url,{headers:{"User-Agent":USER_AGENT,...headers}});if(!response.ok)throw new Error(`${url}: ${response.status}`);return response.text();}

async function fetchKrxStocks(marketType,market){const url=new URL(KIND_URL);url.searchParams.set("method","download");url.searchParams.set("searchType","13");url.searchParams.set("marketType",marketType);const response=await fetch(url,{headers:{"User-Agent":USER_AGENT,Referer:"https://kind.krx.co.kr/"}});if(!response.ok)throw new Error(`KIND ${market}: ${response.status}`);const html=new TextDecoder("euc-kr").decode(await response.arrayBuffer());return [...html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)].map(match=>cells(match[1])).filter(row=>/^\d{6}$/.test(row[1]||"")).map((row,r)=>({c:row[1],n:row[0],m:market,cur:"KRW",t:"stock",r:r+1}));}

async function fetchKrxProduct(type,bld){const body=new URLSearchParams({bld,locale:"ko_KR",trdDd:"",share:"1",money:"1",csvxls_isNo:"false"});const response=await fetch("https://data.krx.co.kr/comm/bldAttendant/getJsonData.cmd",{method:"POST",headers:{"User-Agent":USER_AGENT,"Content-Type":"application/x-www-form-urlencoded; charset=UTF-8",Referer:"https://data.krx.co.kr/"},body});if(!response.ok)throw new Error(`KRX ${type}: ${response.status}`);const json=await response.json();return (json.OutBlock_1||[]).map((row,r)=>({c:row.ISU_SRT_CD||row.ISU_CD,n:row.ISU_ABBRV||row.ISU_NM,m:row.MKT_NM||"KOSPI",cur:"KRW",t:type,r:r+1})).filter(row=>row.c&&row.n);}

function parsePipe(text,kind){const lines=text.trim().split(/\r?\n/),headers=lines.shift().split("|");return lines.map(line=>Object.fromEntries(line.split("|").map((value,index)=>[headers[index],value]))).filter(row=>row["File Creation Time"]===undefined&&row["Test Issue"]!=="Y").map((row,r)=>{const code=(row["Symbol"]||row["ACT Symbol"]||"").trim();const exchange=kind==="nasdaq"?"NASDAQ":EXCHANGE[row.Exchange]||"US";return {c:code,n:(row["Security Name"]||code).replace(/ - .+$/,"").trim(),m:exchange,cur:"USD",t:row.ETF==="Y"?"etf":"stock",r:r+1};}).filter(row=>row.c&&row.n);}
async function fetchUs(){const [nasdaq,other]=await Promise.all([fetchText(NASDAQ_URL),fetchText(OTHER_US_URL)]);return [...parsePipe(nasdaq,"nasdaq"),...parsePipe(other,"other")];}

function fetchKrxProductsWithPykrx(){const code=`import json\nfrom pykrx import stock\nrows=[]\nfor t,kind in [(stock.get_etf_ticker_list(), 'etf'),(stock.get_etn_ticker_list(), 'etn')]:\n  for i,c in enumerate(t):\n    name=stock.get_etf_ticker_name(c) if kind=='etf' else c\n    rows.append({'c':c,'n':name,'m':'KOSPI','cur':'KRW','t':kind,'r':i+1})\nprint(json.dumps(rows,ensure_ascii=False))`;const localPython=resolve(ROOT,".venv","Scripts","python.exe"),python=process.env.PYTHON||(existsSync(localPython)?localPython:"python"),result=spawnSync(python,["-c",code],{encoding:"utf8",windowsHide:true});if(result.status!==0)throw new Error(result.stderr.trim()||"pykrx 실행 실패");return JSON.parse(result.stdout);}
async function collectKrxProducts(){const results=await Promise.allSettled([fetchKrxProduct("etf","dbms/MDC/STAT/standard/MDCSTAT04601"),fetchKrxProduct("etn","dbms/MDC/STAT/standard/MDCSTAT06801")]);const rows=results.flatMap(result=>result.status==="fulfilled"?result.value:[]);if(results.every(result=>result.status==="fulfilled")&&rows.some(row=>row.t==="etf"))return rows;results.filter(result=>result.status==="rejected").forEach(result=>console.warn(result.reason.message));try{const fallback=fetchKrxProductsWithPykrx();if(!fallback.some(row=>row.t==="etf"))throw new Error("pykrx 결과가 비어 있습니다.");return fallback;}catch(error){console.error(`국내 ETF 수집 실패: ${error.message}`);process.exitCode=1;throw error;}}
function dedupe(rows){return [...new Map(rows.map(row=>[`${row.m}:${row.c}`,{...row,cur:row.cur||MARKET_CURRENCY[row.m]||"USD"}])).values()];}
async function write(name,rows){await writeFile(resolve(DATA_DIR,name),`${JSON.stringify(dedupe(rows))}\n`,"utf8");console.log(`${name}: ${rows.length.toLocaleString("ko-KR")}건`);}

async function main(){await mkdir(DATA_DIR,{recursive:true});const [stocks,products,us]=await Promise.all([Promise.all(KIND_MARKETS.map(([type,market])=>fetchKrxStocks(type,market))).then(rows=>rows.flat()),collectKrxProducts(),fetchUs()]);await Promise.all([write("tickers-kr.json",[...stocks,...products]),write("tickers-us.json",us),write("tickers-global.json",GLOBAL_STATIC)]);}

await main();
