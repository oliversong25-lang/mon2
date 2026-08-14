// scripts/lib/oecd.mjs
// OECD SDMX API 클라이언트. 키가 필요 없는 공개 엔드포인트다.
//
// 실측으로 확인한 것들(추측이 아니라 응답을 보고 맞춘 것):
//
//  1) Accept-Language 헤더가 없으면 500 `languageTag1`이 온다. Accept만 맞춰도 안 된다.
//  2) 키는 자리 수가 정확해야 한다. 모자라면 422 "Not enough key values in query,
//     expecting 9 got 1" — 다행히 오류 메시지가 필요한 자리 수를 알려준다.
//  3) dimensionAtObservation=AllDimensions로 부르면 모든 차원이
//     data.structures[0].dimensions.observation 배열에 들어오고,
//     data.dataSets[0].observations의 키가 그 배열 인덱스를 콜론으로 이은 문자열이 된다.
//     값은 배열이고 [0]이 관측값이다. 예: "8:0:4:0:1:0:0:0:0:0" -> [101.3061,0,0,0,null]
//  4) 차원 순서는 dataflow마다 다르다. DF_CLI은 FREQ가 1번이지만 DF_IALFS_UNE_M은 8번이다.
//     그래서 위치가 아니라 id로 찾아야 한다.
//  5) 데이터가 없어도 HTTP 200에 errors:[]이고 observations만 {}로 빈다.
//     상태 코드만 보면 성공으로 읽힌다 — 0건을 성공으로 넘기지 말 것.

import { fetchWithRetry } from "./http.mjs";

const BASE = "https://sdmx.oecd.org/public/rest/data";
const HEADERS = {
  Accept: "application/vnd.sdmx.data+json;version=2",
  "Accept-Language": "en",
  "User-Agent": "AssetInputBeta indicators contact: oliversong25-lang@users.noreply.github.com",
};

export class OecdError extends Error {
  constructor(message) {
    super(message);
    this.name = "OecdError";
  }
}

// 키 자리 수는 dataflow마다 다르다. 422 메시지가 알려주는 값을 그대로 쓰되,
// 호출부가 미리 알고 있으면 왕복을 아낀다.
export function buildKey(refAreas, keyLength) {
  return `${refAreas.join("+")}${".".repeat(Math.max(keyLength - 1, 0))}`;
}

// OECD는 분당 호출 한도가 있고 넘기면 429를 돌려준다. 지표를 7개에서 47개로 늘리며
// dataflow 호출이 4번에서 13번이 됐는데, 몰아치니 다섯 번째부터 전부 429가 났다
// (실측: 13개 중 8개 실패). 재시도로 뚫는 대신 스스로 간격을 벌린다 —
// DART에서 같은 문제를 같은 방법으로 풀었다.
//
// 회귀 테스트는 mock fetch로 도므로 기다릴 이유가 없다. 환경변수로 0을 줄 수 있게 열어
// 둔다 — 안 그러면 dataflow 13개에 스로틀·백오프가 그대로 걸려 테스트가 5분을 넘긴다.
const MIN_GAP_MS = Number(process.env.OECD_MIN_GAP_MS ?? 8000);
const RETRY_BASE_MS = Number(process.env.OECD_RETRY_BASE_MS ?? 15000);
let lastCallAt = 0;
async function throttle() {
  if (!MIN_GAP_MS) return;
  const wait = lastCallAt + MIN_GAP_MS - Date.now();
  if (wait > 0) await new Promise((done) => setTimeout(done, wait));
  lastCallAt = Date.now();
}

export async function fetchDataflow(label, dataflow, { refAreas, keyLength, startPeriod }) {
  const key = buildKey(refAreas, keyLength);
  const url = new URL(`${BASE}/${dataflow}/${key}`);
  url.searchParams.set("dimensionAtObservation", "AllDimensions");
  if (startPeriod) url.searchParams.set("startPeriod", startPeriod);

  await throttle();
  // 429는 "지금 너무 빠르다"는 뜻이라 0.8초 백오프로는 풀리지 않는다. 실측으로 20초쯤
  // 물러서면 통과했다. 5xx·네트워크 오류와 같은 정책을 쓰면 여기서 통째로 실패한다.
  const response = await fetchWithRetry(label, url, { headers: HEADERS }, { attempts: 4, baseDelayMs: RETRY_BASE_MS });
  const json = await response.json();
  if (json.errors && json.errors.length) {
    throw new OecdError(`[${label}] SDMX 오류: ${JSON.stringify(json.errors).slice(0, 200)}`);
  }
  return json;
}

// SDMX-JSON v2를 평평한 행 배열로 편다. 차원은 id로 찾는다(순서에 기대지 않는다).
export function decodeObservations(json) {
  const structure = json?.data?.structures?.[0];
  const dataSet = json?.data?.dataSets?.[0];
  if (!structure || !dataSet) return { rows: [], dimensionIds: [] };

  const dims = structure.dimensions?.observation || [];
  const dimensionIds = dims.map((dim) => dim.id);
  const observations = dataSet.observations || {};

  const rows = Object.entries(observations).map(([key, value]) => {
    const indices = key.split(":").map(Number);
    const row = {};
    dims.forEach((dim, position) => {
      row[dim.id] = dim.values[indices[position]]?.id ?? null;
    });
    row.value = Array.isArray(value) ? value[0] : null;
    return row;
  });

  return { rows, dimensionIds };
}

// 국가 코드 -> 표시 이름(영문)은 응답이 직접 들고 있다. 별도 표를 만들 이유가 없다.
export function refAreaNames(json) {
  const dims = json?.data?.structures?.[0]?.dimensions?.observation || [];
  const area = dims.find((dim) => dim.id === "REF_AREA");
  const names = {};
  (area?.values || []).forEach((value) => {
    names[value.id] = value.name;
  });
  return names;
}
