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
const JPX_URL="https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls";
const NAVER_ETF_URL="https://finance.naver.com/api/sise/etfItemList.nhn";
const NAVER_ETN_URL="https://finance.naver.com/api/sise/etnItemList.nhn";
const NAVER_MARKET_URL="https://m.stock.naver.com/api/stocks/marketValue";
const MARKET_CURRENCY={KOSPI:"KRW",KOSDAQ:"KRW",KONEX:"KRW",NASDAQ:"USD",NYSE:"USD",AMEX:"USD","NYSE Arca":"USD",BATS:"USD",TSE:"JPY",HKEX:"HKD",SSE:"CNY",SZSE:"CNY",TWSE:"TWD",LSE:"GBP",XETRA:"EUR","Euronext Paris":"EUR",NSE:"INR",TSX:"CAD",ASX:"AUD",SGX:"SGD",HOSE:"VND"};
const KIND_MARKETS=[["stockMkt","KOSPI"],["kosdaqMkt","KOSDAQ"],["konexMkt","KONEX"]];
const EXCHANGE={N:"NYSE",P:"NYSE Arca",A:"AMEX",Z:"BATS",Q:"NASDAQ",G:"NASDAQ",S:"NASDAQ"};
const MINIMUMS={kr:2500,us:5000,global:1000};

// 공식 전 종목 파일을 자동 수집하기 어려운 시장은 대표 종목 seed를 유지한다.
// 일본은 JPX 전 종목을 수집하며, 아래는 홍콩·대만·유럽·인도·캐나다·호주·싱가포르·베트남의 검색 안전망이다.
const GLOBAL_STATIC=[
  {c:"0700",n:"텐센트",e:"Tencent Holdings",m:"HKEX",cur:"HKD",t:"stock",x:900000000000},
  {c:"9988",n:"알리바바",e:"Alibaba Group",m:"HKEX",cur:"HKD",t:"stock",x:700000000000},
  {c:"2330",n:"TSMC",e:"Taiwan Semiconductor Manufacturing",m:"TWSE",cur:"TWD",t:"stock",x:1200000000000},
  {c:"SHEL",n:"Shell plc",m:"LSE",cur:"GBP",t:"stock"},{c:"SAP",n:"SAP SE",m:"XETRA",cur:"EUR",t:"stock"},
  {c:"RELIANCE",n:"Reliance Industries",m:"NSE",cur:"INR",t:"stock"},{c:"SHOP",n:"Shopify",m:"TSX",cur:"CAD",t:"stock"},
  {c:"BHP",n:"BHP Group",m:"ASX",cur:"AUD",t:"stock"},{c:"D05",n:"DBS Group",m:"SGX",cur:"SGD",t:"stock"},
  {c:"VNM",n:"Vinamilk",m:"HOSE",cur:"VND",t:"stock"}
];

function decodeEntities(value){return value.replace(/&nbsp;/gi," ").replace(/&amp;/gi,"&").replace(/&lt;/gi,"<").replace(/&gt;/gi,">").replace(/&quot;/gi,'"').replace(/&#39;/gi,"'").trim();}
function cells(row){return [...row.matchAll(/<t[dh][^>]*>([\s\S]*?)<\/t[dh]>/gi)].map(match=>decodeEntities(match[1].replace(/<[^>]+>/g," ").replace(/\s+/g," ")));}
async function fetchText(url,headers={}){const response=await fetch(url,{headers:{"User-Agent":USER_AGENT,...headers}});if(!response.ok)throw new Error(`${url}: ${response.status}`);return response.text();}
async function fetchBuffer(url,headers={}){const response=await fetch(url,{headers:{"User-Agent":USER_AGENT,...headers}});if(!response.ok)throw new Error(`${url}: ${response.status}`);return Buffer.from(await response.arrayBuffer());}

async function fetchKrxStocks(marketType,market){const url=new URL(KIND_URL);url.searchParams.set("method","download");url.searchParams.set("searchType","13");url.searchParams.set("marketType",marketType);const response=await fetch(url,{headers:{"User-Agent":USER_AGENT,Referer:"https://kind.krx.co.kr/"}});if(!response.ok)throw new Error(`KIND ${market}: ${response.status}`);const html=new TextDecoder("euc-kr").decode(await response.arrayBuffer());return [...html.matchAll(/<tr[^>]*>([\s\S]*?)<\/tr>/gi)].map(match=>cells(match[1])).filter(row=>/^\d{6}$/.test(row[1]||"")).map((row,r)=>({c:row[1],n:row[0],m:market,cur:"KRW",t:"stock",r:r+1}));}
async function fetchNaverMarket(market){const load=async page=>{const url=`${NAVER_MARKET_URL}/${market}?page=${page}&pageSize=100`,response=await fetch(url,{headers:{"User-Agent":USER_AGENT,Referer:"https://m.stock.naver.com/"}});if(!response.ok)throw new Error(`Naver ${market}: ${response.status}`);return response.json();},first=await load(1),pages=Math.ceil(Number(first.totalCount||0)/100),rest=pages>1?await Promise.all(Array.from({length:pages-1},(_,index)=>load(index+2))):[],all=[first,...rest].flatMap(page=>page.stocks||[]);return all.map((row,r)=>({c:row.itemCode,n:row.stockName,m:market,cur:"KRW",t:row.stockEndType==="etf"?"etf":row.stockEndType==="etn"?"etn":"stock",x:Number(row.marketValueRaw||0),r:r+1})).filter(row=>row.c&&row.n);}
async function collectKoreanStocks(){const kind=await Promise.all(KIND_MARKETS.map(([type,market])=>fetchKrxStocks(type,market))).then(rows=>rows.flat());if(kind.length>=2500)return kind;console.warn(`KIND 주식 목록 ${kind.length}건, 공개 시장 목록으로 재시도`);return Promise.all([fetchNaverMarket("KOSPI"),fetchNaverMarket("KOSDAQ")]).then(rows=>rows.flat());}

function pythonExecutable(){const local=resolve(ROOT,".venv","Scripts","python.exe");return process.env.PYTHON||(existsSync(local)?local:"python");}
function runPython(code,input){const result=spawnSync(pythonExecutable(),["-c",code],{input,encoding:null,windowsHide:true,maxBuffer:64*1024*1024,env:{...process.env,PYTHONIOENCODING:"utf-8"}});if(result.status!==0)throw new Error(Buffer.from(result.stderr||"").toString("utf8").trim()||"Python 수집기 실행 실패");return Buffer.from(result.stdout||"").toString("utf8");}

function fetchKrxProductsWithPykrx(){const code=`import json\nfrom pykrx import stock\nrows=[]\nfor kind,tickers,name_fn in [('etf',stock.get_etf_ticker_list(),stock.get_etf_ticker_name),('etn',stock.get_etn_ticker_list(),stock.get_etn_ticker_name)]:\n  for i,c in enumerate(tickers):\n    rows.append({'c':c,'n':name_fn(c),'m':'KOSPI','cur':'KRW','t':kind,'r':i+1})\nprint(json.dumps(rows,ensure_ascii=False))`;return JSON.parse(runPython(code));}
async function fetchNaverProducts(){const headers={Referer:"https://finance.naver.com/"},[etfResponse,etnResponse]=await Promise.all([fetch(NAVER_ETF_URL,{headers:{"User-Agent":USER_AGENT,...headers}}),fetch(NAVER_ETN_URL,{headers:{"User-Agent":USER_AGENT,...headers}})]);if(!etfResponse.ok||!etnResponse.ok)throw new Error(`ETF ${etfResponse.status} / ETN ${etnResponse.status}`);const decode=async response=>JSON.parse(new TextDecoder("euc-kr").decode(await response.arrayBuffer())),[etfJson,etnJson]=await Promise.all([decode(etfResponse),decode(etnResponse)]),map=(rows,type)=>(rows||[]).map((row,r)=>({c:row.itemcode,n:row.itemname,m:"KOSPI",cur:"KRW",t:type,r:r+1})).filter(row=>row.c&&row.n);return [...map(etfJson.result?.etfItemList,"etf"),...map(etnJson.result?.etnItemList,"etn")];}
async function collectKrxProducts(){try{const rows=fetchKrxProductsWithPykrx();if(!rows.some(row=>row.t==="etf"))throw new Error("pykrx ETF 결과가 비어 있습니다.");return rows;}catch(error){console.warn(`pykrx 수집 실패, 공개 목록으로 재시도: ${error.message}`);try{const rows=await fetchNaverProducts();if(!rows.some(row=>row.t==="etf")||!rows.some(row=>row.t==="etn"))throw new Error("ETF 또는 ETN 결과가 비어 있습니다.");return rows;}catch(fallbackError){console.error(`국내 ETF 수집 실패: ${fallbackError.message}`);throw fallbackError;}}}

function parsePipe(text,kind){const lines=text.replace(/\r/g,"").split("\n"),headers=(lines.shift()||"").split("|");return lines.map(line=>Object.fromEntries(line.split("|").map((value,index)=>[headers[index],value]))).filter(row=>row["Test Issue"]!=="Y").map((row,r)=>{const code=(row.Symbol||row["ACT Symbol"]||"").trim();if(!code||code.startsWith("File Creation Time"))return null;const exchange=kind==="nasdaq"?"NASDAQ":EXCHANGE[row.Exchange]||"US",name=(row["Security Name"]||code).replace(/ - .+$/," ").trim();return {c:code,n:name,m:exchange,cur:"USD",t:row.ETF==="Y"?"etf":"stock",r:r+1};}).filter(Boolean);}
async function fetchUs(){const [nasdaq,other]=await Promise.all([fetchText(NASDAQ_URL),fetchText(OTHER_US_URL)]);return [...parsePipe(nasdaq,"nasdaq"),...parsePipe(other,"other")];}

async function fetchJpx(){const binary=await fetchBuffer(JPX_URL),code=`import sys,json,xlrd\nbook=xlrd.open_workbook(file_contents=sys.stdin.buffer.read())\nsheet=book.sheet_by_index(0)\nheaders=[str(v).strip() for v in sheet.row_values(0)]\nrows=[]\nfor i in range(1,sheet.nrows):\n  values=sheet.row_values(i)\n  row=dict(zip(headers,values))\n  code=str(row.get('コード','')).replace('.0','').strip()\n  name=str(row.get('銘柄名','')).strip()\n  category=str(row.get('市場・商品区分','')).strip()\n  if not code or not name: continue\n  kind='etf' if 'ETF' in category else ('etn' if 'ETN' in category else 'stock')\n  rows.append({'c':code,'n':name,'m':'TSE','cur':'JPY','t':kind,'r':i})\nprint(json.dumps(rows,ensure_ascii=False))`;return JSON.parse(runPython(code,binary));}

function dedupe(rows){return [...new Map(rows.map(row=>[`${row.m}:${row.c}`,{...row,cur:row.cur||MARKET_CURRENCY[row.m]||"USD"}])).values()];}
function stats(rows){return {count:rows.length,etf:rows.filter(row=>row.t==="etf"||row.t==="etn").length};}
function validate(scope,rows){const summary=stats(rows);console.log(`${scope}: ${summary.count.toLocaleString("ko-KR")}건 · ETF/ETN ${summary.etf.toLocaleString("ko-KR")}건`);if(summary.count<MINIMUMS[scope])throw new Error(`${scope} 종목 수집 결과 ${summary.count}건: 최소 ${MINIMUMS[scope]}건 미달`);return rows;}
async function write(name,rows){await writeFile(resolve(DATA_DIR,name),`${JSON.stringify(rows)}\n`,"utf8");}

async function main(){await mkdir(DATA_DIR,{recursive:true});const [stocks,products,us,jpx]=await Promise.all([collectKoreanStocks(),collectKrxProducts(),fetchUs(),fetchJpx()]);const scopes={kr:dedupe([...stocks,...products]),us:dedupe(us),global:dedupe([...jpx,...GLOBAL_STATIC])};Object.entries(scopes).forEach(([scope,rows])=>validate(scope,rows));await Promise.all([write("tickers-kr.json",scopes.kr),write("tickers-us.json",scopes.us),write("tickers-global.json",scopes.global)]);console.log(`전체 ETF/ETN: ${Object.values(scopes).flat().filter(row=>row.t==="etf"||row.t==="etn").length.toLocaleString("ko-KR")}건`);}

main().catch(error=>{console.error(`[종목 빌드 실패] ${error.message}`);process.exitCode=1;});
