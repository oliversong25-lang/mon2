// scripts/lib/dart.mjs
// OPEN DART (금융감독원 전자공시시스템, opendart.fss.or.kr) 업종코드 조회.
//
// 기업개황(company.json) API는 종목코드가 아니라 DART 내부 corp_code로 조회한다.
// corp_code 목록은 corpCode.xml(zip)로 통째로 내려받는 방식뿐이라, 그 zip을 풀어
// stock_code -> corp_code 매핑을 먼저 만든 뒤에야 종목별 induty_code(업종코드)를
// 조회할 수 있다. zip 해제는 Node 표준 라이브러리에 없어 이미 JPX 엑셀 파싱에
// 쓰고 있는 것과 같은 방식(Python subprocess, stdlib zipfile/xml만 사용)으로 처리한다.

import { fetchWithRetry } from "./http.mjs";

const USER_AGENT = "AssetInputBeta ticker-builder contact: oliversong25-lang@users.noreply.github.com";
const CORP_CODE_URL = "https://opendart.fss.or.kr/api/corpCode.xml";
const COMPANY_URL = "https://opendart.fss.or.kr/api/company.json";

export async function fetchDartCorpCodeMap(dartKey, runPython) {
  const url = new URL(CORP_CODE_URL);
  url.searchParams.set("crtfc_key", dartKey);
  const response = await fetchWithRetry("OPEN DART corpCode.xml", url, { headers: { "User-Agent": USER_AGENT } });
  const zipBuffer = Buffer.from(await response.arrayBuffer());
  const code = `
import sys, io, zipfile, json
import xml.etree.ElementTree as ET
data = sys.stdin.buffer.read()
zf = zipfile.ZipFile(io.BytesIO(data))
xml_bytes = zf.read(zf.namelist()[0])
root = ET.fromstring(xml_bytes)
rows = []
for item in root.findall('list'):
    stock_code = (item.findtext('stock_code') or '').strip()
    corp_code = (item.findtext('corp_code') or '').strip()
    if stock_code:
        rows.append([stock_code, corp_code])
print(json.dumps(rows))
`;
  const rows = JSON.parse(runPython(code, zipBuffer));
  return new Map(rows);
}

export class DartError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "DartError";
    this.status = status;
  }
}

// DART 응답의 status는 "조회 결과 없음"과 "너 차단됐음"을 같은 자리에 담아 보낸다.
// 이걸 뭉뚱그려 null로 만들면 차단이 조용히 "업종 없는 종목"으로 둔갑한다 — 실제로
// 그렇게 당했다(2,758건 중 정확히 앞 1,010건만 업종이 붙고 나머지가 전부 비어 있었다).
// 013(데이터 없음)만 정상적인 빈 결과로 취급하고, 나머지는 전부 예외로 올린다.
const DART_NO_DATA = "013";
const DART_RATE_LIMITED = "020";
const DART_FATAL = {
  "010": "등록되지 않은 인증키",
  "011": "사용할 수 없는 인증키",
  "012": "접근할 수 없는 IP",
  "020": "요청 제한 초과",
  "100": "부적절한 요청 값",
  "800": "DART 시스템 점검 중",
  "900": "DART 정의되지 않은 오류",
  "901": "인증키 개인정보 보유기간 만료",
};

async function fetchInduty(corpCode, dartKey) {
  const url = new URL(COMPANY_URL);
  url.searchParams.set("crtfc_key", dartKey);
  url.searchParams.set("corp_code", corpCode);
  // 종목마다 부르는 호출이라 재시도를 1회로 줄인다 — 2,700건에 3회씩 붙이면
  // 분당 한도를 다시 건드린다.
  const response = await fetchWithRetry("OPEN DART company.json", url, { headers: { "User-Agent": USER_AGENT } }, { attempts: 2, baseDelayMs: 400 });
  const json = await response.json();
  if (json.status === DART_NO_DATA) return null;
  if (json.status !== "000") {
    throw new DartError(`DART ${json.status}: ${DART_FATAL[json.status] || json.message || "알 수 없는 오류"}`, json.status);
  }
  return json.induty_code || null;
}

// 창(windowMs) 안에서 maxPerWindow회를 넘지 않도록 호출 시각을 기록해 대기시킨다.
// DART는 분당 호출 한도를 넘기면 이후 요청을 전부 020으로 되돌려준다 — 동시성만
// 낮춰서는 막을 수 없고(응답이 빠르면 그만큼 더 빨리 한도를 채운다) 초당 유량 자체를
// 묶어야 한다.
export function createRateLimiter(maxPerWindow, windowMs) {
  const hits = [];
  return async function acquire() {
    for (;;) {
      const now = Date.now();
      while (hits.length && now - hits[0] >= windowMs) hits.shift();
      if (hits.length < maxPerWindow) {
        hits.push(now);
        return;
      }
      await new Promise((done) => setTimeout(done, windowMs - (now - hits[0]) + 50));
    }
  };
}

// 분당 한도(1,000회)보다 낮게 잡는다 — 정확히 1,000으로 맞추면 창 경계에서 넘친다.
// 2,758종목이면 약 3분이고, 종목 목록 빌드는 주 1회라 이 정도는 문제되지 않는다.
const DEFAULT_RATE = { maxPerWindow: 900, windowMs: 60_000 };

// 업종은 부가 정보라 종목 하나가 실패해도 전체 빌드를 막지 않는다. 다만 "차단당해서
// 전부 실패"와 "이 종목만 데이터 없음"은 전혀 다른 사건이므로, 한도 초과(020)는
// 한 번 쉬고 재시도하고 그래도 계속되면 예외로 올려 배치가 시끄럽게 죽게 한다.
export async function attachSectors(tickers, dartKey, corpCodeMap, { concurrency = 4, onProgress, limiter, sleep = (ms) => new Promise((done) => setTimeout(done, ms)) } = {}) {
  const acquire = limiter || createRateLimiter(DEFAULT_RATE.maxPerWindow, DEFAULT_RATE.windowMs);
  const queue = [...tickers];
  const sectors = new Map();
  const failures = [];
  let done = 0;

  async function lookup(ticker, corpCode) {
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await acquire();
      try {
        return await fetchInduty(corpCode, dartKey);
      } catch (error) {
        if (error instanceof DartError && error.status === DART_RATE_LIMITED && attempt < 2) {
          await sleep(DEFAULT_RATE.windowMs);
          continue;
        }
        if (error instanceof DartError && DART_FATAL[error.status] && error.status !== DART_RATE_LIMITED) {
          throw error; // 인증키·점검 문제는 재시도해도 소용없다.
        }
        failures.push({ code: ticker.c, message: error.message });
        return null;
      }
    }
    failures.push({ code: ticker.c, message: "DART 요청 제한(020)이 재시도 후에도 계속됨" });
    return null;
  }

  async function worker() {
    while (queue.length) {
      const ticker = queue.shift();
      done += 1;
      if (onProgress && done % 500 === 0) onProgress(done, tickers.length);
      const corpCode = corpCodeMap.get(ticker.c);
      if (!corpCode) continue;
      const induty = await lookup(ticker, corpCode);
      if (induty) sectors.set(ticker.c, induty);
    }
  }

  await Promise.all(Array.from({ length: concurrency }, worker));
  return { sectors, failures };
}
