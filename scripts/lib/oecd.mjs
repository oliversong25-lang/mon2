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

export async function fetchDataflow(label, dataflow, { refAreas, keyLength, startPeriod }) {
  const key = buildKey(refAreas, keyLength);
  const url = new URL(`${BASE}/${dataflow}/${key}`);
  url.searchParams.set("dimensionAtObservation", "AllDimensions");
  if (startPeriod) url.searchParams.set("startPeriod", startPeriod);

  const response = await fetchWithRetry(label, url, { headers: HEADERS });
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
