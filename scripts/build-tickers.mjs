import { mkdir, writeFile, rename } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import { fetchAll, resolveLatestBasDt, normalizeKrCode, setupUtf8Console } from "./lib/data-go-kr.mjs";
import { fetchDartCorpCodeMap, attachSectors } from "./lib/dart.mjs";
import { resolveSector } from "./lib/ksic.mjs";

setupUtf8Console();

const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const DATA_DIR=resolve(ROOT,"data");
const USER_AGENT="AssetInputBeta ticker-builder contact: oliversong25-lang@users.noreply.github.com";
const KIND_URL="https://kind.krx.co.kr/corpgeneral/corpList.do";
const NASDAQ_URL="https://www.nasdaqtrader.com/dynamic/symdir/nasdaqlisted.txt";
const OTHER_US_URL="https://www.nasdaqtrader.com/dynamic/symdir/otherlisted.txt";
const JPX_URL="https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xls";
// 금융위원회_KRX상장종목정보(15094775) — KIND 스크래핑이 너무 적은 결과를 주면 이걸로
// 재시도한다(예전엔 네이버 모바일 API로 재시도했음). 금융위원회_증권상품시세정보(15094806)
// — ETF·ETN 목록(예전엔 pykrx→네이버 순으로 스크래핑했음).
const KRX_LISTED_URL="https://apis.data.go.kr/1160100/service/GetKrxListedInfoService/getItemInfo";
const ETF_URL="https://apis.data.go.kr/1160100/service/GetSecuritiesProductInfoService/getETFPriceInfo";
const ETN_URL="https://apis.data.go.kr/1160100/service/GetSecuritiesProductInfoService/getETNPriceInfo";
// 시가총액(mrktTotAmt)은 상장종목정보엔 없고 시세 엔드포인트에만 있다 — 검색 정렬용
// x 필드를 채우려고 주식시세정보를 한 번 더 부른다(가격 자체는 버림, 그건 매일
// build-quotes.mjs가 따로 한다).
const STOCK_URL="https://apis.data.go.kr/1160100/service/GetStockSecuritiesInfoService/getStockPriceInfo";
const DATA_GO_KR_KEY=process.env.DATA_GO_KR_KEY;
const DART_API_KEY=process.env.DART_API_KEY;
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

// KIND가 부족하면 공식 API(금융위원회_KRX상장종목정보)로 재시도한다. 시장구분값이
// "유가증권시장"/"코스닥"/"코넥스"처럼 한글일 수 있어 매핑해준다.
const KRX_MARKET_LABELS={"유가증권시장":"KOSPI","코스닥":"KOSDAQ","코넥스":"KONEX",KOSPI:"KOSPI",KOSDAQ:"KOSDAQ",KONEX:"KONEX"};
export async function fetchKrxListedInfoApi(){
  const basDt=await resolveLatestBasDt(KRX_LISTED_URL,{},DATA_GO_KR_KEY);
  const rows=await fetchAll(KRX_LISTED_URL,{basDt},DATA_GO_KR_KEY);
  if(rows[0])console.log("[raw-sample] krx-listed:",JSON.stringify(rows[0]));
  return rows.map((row,r)=>{
    const market=KRX_MARKET_LABELS[row.mrktCtg]||KRX_MARKET_LABELS[row.mrktCls];
    const code=normalizeKrCode(row.srtnCd,row.isinCd);
    const name=row.itmsNm;
    if(!market||!code||!name)return null;
    return {c:code,n:name,m:market,cur:"KRW",t:"stock",r:r+1};
  }).filter(Boolean);
}
export async function collectKoreanStocks(){
  const kind=await Promise.all(KIND_MARKETS.map(([type,market])=>fetchKrxStocks(type,market))).then(rows=>rows.flat()).catch(error=>{console.warn(`KIND 조회 실패, 공식 API로 재시도: ${error.message}`);return[];});
  if(kind.length>=2500)return kind;
  console.warn(`KIND 주식 목록 ${kind.length}건, 금융위원회_KRX상장종목정보 API로 재시도`);
  return fetchKrxListedInfoApi();
}

function pythonExecutable(){const local=resolve(ROOT,".venv","Scripts","python.exe");return process.env.PYTHON||(existsSync(local)?local:"python");}
function runPython(code,input){const result=spawnSync(pythonExecutable(),["-c",code],{input,encoding:null,windowsHide:true,maxBuffer:64*1024*1024,env:{...process.env,PYTHONIOENCODING:"utf-8"}});if(result.status!==0)throw new Error(Buffer.from(result.stderr||"").toString("utf8").trim()||"Python 수집기 실행 실패");return Buffer.from(result.stdout||"").toString("utf8");}

// 국내 ETF·ETN 목록: 금융위원회_증권상품시세정보(시세 엔드포인트를 그날의 "전 종목
// 목록"으로도 쓴다 — 가격은 버리고 코드·이름만 취한다). pykrx/네이버 스크래핑을
// 대체한다.
export async function collectKrxProducts(){
  const basDt=await resolveLatestBasDt(ETF_URL,{},DATA_GO_KR_KEY);
  const [etf,etn]=await Promise.all([
    fetchAll(ETF_URL,{basDt},DATA_GO_KR_KEY),
    fetchAll(ETN_URL,{basDt},DATA_GO_KR_KEY).catch(error=>{console.warn(`ETN 목록 조회 실패, 스킵: ${error.message}`);return[];}),
  ]);
  if(etf[0])console.log("[raw-sample] etf-listing:",JSON.stringify(etf[0]));
  if(etn[0])console.log("[raw-sample] etn-listing:",JSON.stringify(etn[0]));
  const map=(rows,type)=>rows.map((row,r)=>{const code=normalizeKrCode(row.srtnCd,row.isinCd),name=row.itmsNm;if(!code||!name)return null;return {c:code,n:name,m:"KOSPI",cur:"KRW",t:type,r:r+1,x:Number(row.mrktTotAmt||0)};}).filter(Boolean);
  const rows=[...map(etf,"etf"),...map(etn,"etn")];
  if(!rows.some(row=>row.t==="etf"))throw new Error("증권상품시세정보 ETF 결과가 비어 있습니다.");
  return rows;
}

function parsePipe(text,kind){const lines=text.replace(/\r/g,"").split("\n"),headers=(lines.shift()||"").split("|");return lines.map(line=>Object.fromEntries(line.split("|").map((value,index)=>[headers[index],value]))).filter(row=>row["Test Issue"]!=="Y").map((row,r)=>{const code=(row.Symbol||row["ACT Symbol"]||"").trim();if(!code||code.startsWith("File Creation Time"))return null;const exchange=kind==="nasdaq"?"NASDAQ":EXCHANGE[row.Exchange]||"US",name=(row["Security Name"]||code).replace(/ - .+$/," ").trim();return {c:code,n:name,m:exchange,cur:"USD",t:row.ETF==="Y"?"etf":"stock",r:r+1};}).filter(Boolean);}
async function fetchUs(){const [nasdaq,other]=await Promise.all([fetchText(NASDAQ_URL),fetchText(OTHER_US_URL)]);return [...parsePipe(nasdaq,"nasdaq"),...parsePipe(other,"other")];}

async function fetchJpx(){const binary=await fetchBuffer(JPX_URL),code=`import sys,json,xlrd\nbook=xlrd.open_workbook(file_contents=sys.stdin.buffer.read())\nsheet=book.sheet_by_index(0)\nheaders=[str(v).strip() for v in sheet.row_values(0)]\nrows=[]\nfor i in range(1,sheet.nrows):\n  values=sheet.row_values(i)\n  row=dict(zip(headers,values))\n  code=str(row.get('コード','')).replace('.0','').strip()\n  name=str(row.get('銘柄名','')).strip()\n  category=str(row.get('市場・商品区分','')).strip()\n  if not code or not name: continue\n  kind='etf' if 'ETF' in category else ('etn' if 'ETN' in category else 'stock')\n  rows.append({'c':code,'n':name,'m':'TSE','cur':'JPY','t':kind,'r':i})\nprint(json.dumps(rows,ensure_ascii=False))`;return JSON.parse(runPython(code,binary));}

// 주식은 KIND든 KRX상장종목정보든 시가총액을 안 준다 — 검색 정렬이 API 반환 순서
// 그대로 나오는(예: 삼성전자보다 훨씬 작은 회사가 먼저 뜨는) 원인이었다. 주식시세정보
// 응답의 mrktTotAmt로 별도 채워 넣는다.
export async function fetchStockMarketCaps(){
  const basDt=await resolveLatestBasDt(STOCK_URL,{},DATA_GO_KR_KEY);
  const rows=await fetchAll(STOCK_URL,{basDt},DATA_GO_KR_KEY);
  const caps=new Map();
  rows.forEach(row=>{
    const code=normalizeKrCode(row.srtnCd,row.isinCd);
    const cap=Number(row.mrktTotAmt||0);
    if(code&&cap>0)caps.set(code,cap);
  });
  return caps;
}

function dedupe(rows){return [...new Map(rows.map(row=>[`${row.m}:${row.c}`,{...row,cur:row.cur||MARKET_CURRENCY[row.m]||"USD"}])).values()];}
function stats(rows){return {count:rows.length,etf:rows.filter(row=>row.t==="etf"||row.t==="etn").length};}
function validate(scope,rows){const summary=stats(rows);console.log(`${scope}: ${summary.count.toLocaleString("ko-KR")}건 · ETF/ETN ${summary.etf.toLocaleString("ko-KR")}건`);if(summary.count<MINIMUMS[scope])throw new Error(`${scope} 종목 수집 결과 ${summary.count}건: 최소 ${MINIMUMS[scope]}건 미달`);return rows;}
// 임시 파일에 먼저 쓰고 원자적으로 이름을 바꾼다 — 쓰는 도중 프로세스가 죽어도
// 기존 파일이 반쯤 쓰인 상태로 남지 않는다.
async function write(name,rows){const target=resolve(DATA_DIR,name),tmp=`${target}.tmp`;await writeFile(tmp,`${JSON.stringify(rows)}\n`,"utf8");await rename(tmp,target);}

// 업종은 매일 바뀌지 않으므로 이 1회성 빌드에서만 채워 넣는다 — 앱은 종목 등록
// 시점에 이미 로드돼 있는 이 필드를 그대로 자산 레코드에 복사해 저장할 뿐, 등록할
// 때마다 DART를 실시간 호출하지 않는다(클라이언트에 DART 키를 넣지 않기 위함).
export async function attachKrSectors(krRows){
  if(!DART_API_KEY){console.warn("DART_API_KEY 미설정 — 업종 정보 없이 진행");return krRows;}
  console.log("DART 업종 조회 시작...");
  const corpCodeMap=await fetchDartCorpCodeMap(DART_API_KEY,runPython);
  const stockRows=krRows.filter(row=>row.t==="stock");
  const notInDart=stockRows.filter(row=>!corpCodeMap.has(row.c)).length;
  const {sectors,failures}=await attachSectors(stockRows,DART_API_KEY,corpCodeMap,{onProgress:(done,total)=>console.log(`  업종 조회 ${done}/${total}`)});
  const matchRate=stockRows.length?sectors.size/stockRows.length:0;
  console.log(`업종 확보: ${sectors.size.toLocaleString("ko-KR")}/${stockRows.length.toLocaleString("ko-KR")}건 (${(matchRate*100).toFixed(1)}%)`);
  // 미확보분을 "corpCode에 아예 없음"과 "호출 실패"로 갈라서 찍는다 — 둘은 원인도
  // 대응도 다른데 합쳐 놓으면 어느 쪽인지 알 수 없다(1,010건에서 끊겼을 때 그랬다).
  console.log(`  업종 미확보 내역: DART corpCode 없음 ${notInDart.toLocaleString("ko-KR")}건 · 호출 실패 ${failures.length.toLocaleString("ko-KR")}건`);
  failures.slice(0,5).forEach(failure=>console.warn(`  [업종 실패] ${failure.code}: ${failure.message}`));
  if(matchRate<0.8)throw new Error(`DART 업종 매칭률 ${(matchRate*100).toFixed(1)}% — 80% 미만입니다(호출 실패 ${failures.length}건). 상장사는 원칙적으로 모두 DART에 있으므로 호출 제한이나 인증키를 확인하세요.`);
  return krRows.map(row=>{
    const induty=sectors.get(row.c);
    if(!induty)return row;
    const resolved=resolveSector(induty);
    return resolved?{...row,...resolved}:{...row,sec:induty};
  });
}

async function main(){
  if(!DATA_GO_KR_KEY)throw new Error("DATA_GO_KR_KEY 환경변수가 필요합니다");
  await mkdir(DATA_DIR,{recursive:true});
  const [stocks,products,us,jpx,stockCaps]=await Promise.all([collectKoreanStocks(),collectKrxProducts(),fetchUs(),fetchJpx(),fetchStockMarketCaps().catch(error=>{console.warn(`시가총액 조회 실패, x 없이 진행: ${error.message}`);return new Map();})]);
  const stocksWithCaps=stocks.map(row=>stockCaps.has(row.c)?{...row,x:stockCaps.get(row.c)}:row);
  let krRows=dedupe([...stocksWithCaps,...products]);
  krRows=await attachKrSectors(krRows);
  const scopes={kr:krRows,us:dedupe(us),global:dedupe([...jpx,...GLOBAL_STATIC])};
  Object.entries(scopes).forEach(([scope,rows])=>validate(scope,rows));
  await Promise.all([write("tickers-kr.json",scopes.kr),write("tickers-us.json",scopes.us),write("tickers-global.json",scopes.global)]);
  console.log(`전체 ETF/ETN: ${Object.values(scopes).flat().filter(row=>row.t==="etf"||row.t==="etn").length.toLocaleString("ko-KR")}건`);
}

// import.meta.url이 실행 진입점과 같을 때만(직접 `node build-tickers.mjs`로 실행됐을 때만)
// 돈다 — 테스트에서 개별 함수만 임포트해 쓸 때 전체 파이프라인(미국·일본 포함)이
// 딸려서 도는 걸 막는다.
if(process.argv[1]&&(import.meta.url===`file://${process.argv[1].replace(/\\/g,"/")}`||import.meta.url===`file:///${process.argv[1].replace(/\\/g,"/")}`)){
  main().catch(error=>{console.error(`[종목 빌드 실패] ${error.message}`);process.exitCode=1;});
}
