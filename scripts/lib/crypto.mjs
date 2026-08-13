// scripts/lib/crypto.mjs
// CoinGecko /simple/price — 가상자산 원화 시세.
//
// 왜 코인게코인가: 업비트 Open API 이용약관 제5조(데이터 저작권은 회사에 있고 무단
// 사용·변경 금지)와 제4조 2항 ③(서비스를 이용하는 응용프로그램을 유상으로 양도·배포·
// 이용허락하면 이용 제한)이 "시세를 받아 quotes.json에 구워 배포하는" 이 구조와
// 정면으로 걸리는데, 유료 상업 라이선스를 살 경로 자체가 없다. 코인게코는 Demo 플랜에
// 상업 라이선스가 없지만 Analyst 이상 유료 플랜에 포함돼 있어, 코드는 그대로 두고 키만
// 바꾸면 합법화된다. yfinance·증권사 API를 뺐던 기준(합법화 경로의 유무)과 같은 판단이다.
//
// !! 유료 서비스로 전환할 때 반드시 Analyst 이상 플랜으로 올릴 것 (README에도 명시).

// 자산 입력 화면이 저장하는 코드(심볼)와 코인게코 ID는 다르다("BTC" vs "bitcoin").
// 심볼로 ID를 자동 추론하는 방법(coins/list 검색 등)은 쓰지 않는다 — 같은 심볼을 쓰는
// 코인이 수백 개라(BTC만 해도 사칭 토큰이 여럿) 자동 매칭은 조용히 엉뚱한 코인의 시세를
// 물어온다. 종목코드 "A" 접두어 때와 같은 종류의 사고이고, 그때는 최소한 값이 비어서
// 티가 났지만 이건 그럴듯한 숫자가 들어와 더 나쁘다. 명시적 표로만 매핑하고,
// 표에 없는 심볼은 매칭 실패로 세어 출력한다.
export const COINGECKO_IDS = Object.freeze({
  BTC: "bitcoin",
  ETH: "ethereum",
  USDT: "tether",
});

import { fetchWithRetry } from "./http.mjs";

const DEMO_BASE = "https://api.coingecko.com/api/v3";
const PRO_BASE = "https://pro-api.coingecko.com/api/v3";

export class CoinGeckoError extends Error {
  constructor(message) {
    super(message);
    this.name = "CoinGeckoError";
  }
}

// Demo 키와 Pro 키는 호스트도 헤더 이름도 다르다. 키 접두어("CG-"는 양쪽 공통이라
// 구분에 못 쓴다)로는 알 수 없으므로 플랜을 환경변수로 받는다 — 기본은 Demo다.
export function coinGeckoEndpoint(plan = "demo") {
  return plan === "pro"
    ? { base: PRO_BASE, header: "x-cg-pro-api-key" }
    : { base: DEMO_BASE, header: "x-cg-demo-api-key" };
}

// 심볼 목록 -> { quotes: {BTC:{price,currency:"KRW"}}, unmapped:[], missing:[] }
// 하루 1회 호출이라 전 종목을 한 번의 요청으로 받는다(월 30회 남짓, Demo 한도의 0.3%).
export async function fetchCryptoQuotes(symbols, apiKey, { plan = "demo", fetchImpl = fetch } = {}) {
  const wanted = [...new Set(symbols.map((symbol) => String(symbol || "").toUpperCase()).filter(Boolean))];
  const unmapped = wanted.filter((symbol) => !COINGECKO_IDS[symbol]);
  const mapped = wanted.filter((symbol) => COINGECKO_IDS[symbol]);
  if (!mapped.length) return { quotes: {}, unmapped, missing: [] };

  const { base, header } = coinGeckoEndpoint(plan);
  const ids = mapped.map((symbol) => COINGECKO_IDS[symbol]);
  const url = new URL(`${base}/simple/price`);
  url.searchParams.set("ids", ids.join(","));
  url.searchParams.set("vs_currencies", "krw"); // 원화를 직접 받는다 — 환율 재환산을 거치지 않는다.

  // 어느 API가 끊겼는지 로그에 남기고, 일시적인 네트워크 오류는 몇 번 다시 시도한다.
  const response = await fetchWithRetry("CoinGecko", url, { headers: apiKey ? { [header]: apiKey } : {} }, { fetchImpl });
  const json = await response.json();

  // 코인게코는 잘못된 ID를 줘도 HTTP 200에 빈 객체 {}를 돌려준다(실측 확인). 오류를
  // 200으로 포장하는 응답이라 status만 보면 성공으로 읽힌다 — 이 트랙에서 같은 패턴을
  // 세 번 겪었으므로 여기서 명시적으로 끊는다.
  if (json && json.status && json.status.error_code) {
    throw new CoinGeckoError(`CoinGecko ${json.status.error_code}: ${json.status.error_message || "알 수 없는 오류"}`);
  }
  if (!json || typeof json !== "object" || !Object.keys(json).length) {
    throw new CoinGeckoError("CoinGecko 응답이 비어 있습니다 (잘못된 코인 ID이거나 키가 거부됐을 수 있습니다)");
  }

  const quotes = {};
  const missing = [];
  mapped.forEach((symbol) => {
    const price = Number(json[COINGECKO_IDS[symbol]]?.krw);
    if (Number.isFinite(price) && price > 0) quotes[symbol] = { price, currency: "KRW" };
    else missing.push(symbol);
  });
  return { quotes, unmapped, missing };
}
