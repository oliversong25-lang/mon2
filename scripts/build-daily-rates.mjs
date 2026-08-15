// scripts/build-daily-rates.mjs
//
// 매일 바뀌는 금리 배치. OECD 지표 배치(월·분기)와 **일부러 분리한다** — 데이터가
// 바뀌는 주기가 다르고, 한쪽이 죽어도 다른 쪽은 갱신돼야 한다. 시세와 종목 목록을
// 이미 그렇게 나눠 둔 것과 같은 이유다.
//
// 산출물 (OECD와 같은 카탈로그 구조에 얹는다 — 출처는 시스템이 아니라 필드다)
//   data/indicators/index-daily.json     일간 계열의 목록. 화면이 OECD 인덱스와 합쳐 읽는다.
//   data/indicators/latest/rates.json    일간 계열의 최신값
//   data/indicators/daily/<출처>.json     계열별 시계열
//
// 세 결과를 구분한다: updated / already-current / failed.
// 아무것도 안 한 실행을 성공으로 읽으면 배치가 멈춘 것을 알아챌 수 없다.

import { mkdir, writeFile, readFile, rename } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { setupUtf8Console } from "./lib/data-go-kr.mjs";
import {
  fetchEcosSeries, fetchTreasuryYields, fetchFedRates,
  SOURCE_ECOS, SOURCE_TREASURY, SOURCE_NYFED,
} from "./lib/daily-rates.mjs";

setupUtf8Console();

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT_DIR = resolve(ROOT, "data", "indicators");
const DAILY_DIR = resolve(OUT_DIR, "daily");
const LATEST_DIR = resolve(OUT_DIR, "latest");
const INDEX_PATH = resolve(OUT_DIR, "index-daily.json");
const LATEST_PATH = resolve(LATEST_DIR, "rates.json");

const ECOS_AUTH_KEY = process.env.ECOS_AUTH_KEY || process.env.ECOS_API_KEY || "";

// 금리는 전부 같은 갈래에 넣는다. 화면의 1차 축이 갈래이므로 여기서 정해 준다.
const TOPIC = { id: "rates", nameKo: "금리(일간)", nameEn: "Daily rates" };

const COUNTRY_NAMES = { KOR: { ko: "대한민국", en: "Korea" }, USA: { ko: "미국", en: "United States" } };

async function writeAtomic(path, text) {
  const tmp = `${path}.tmp`;
  await writeFile(tmp, text, "utf8");
  await rename(tmp, path);
}

async function readJson(path) {
  try { return JSON.parse(await readFile(path, "utf8")); } catch { return null; }
}

async function main() {
  const startedAt = Date.now();
  await mkdir(DAILY_DIR, { recursive: true });
  await mkdir(LATEST_DIR, { recursive: true });

  const previous = await readJson(LATEST_PATH);
  const previousLatest = new Map(
    Object.entries(previous?.series || {}).map(([id, entry]) => [id, `${entry.period}:${entry.value}`])
  );

  const failures = [];
  const collected = [];

  // 출처별로 따로 잡는다. 하나가 죽어도 나머지는 간다.
  const sources = [
    [SOURCE_ECOS, "ECOS 시장금리·기준금리", () => fetchEcosSeries(ECOS_AUTH_KEY)],
    [SOURCE_TREASURY, "미 재무부 국채수익률", () => fetchTreasuryYields(new Date().getUTCFullYear())],
    [SOURCE_NYFED, "뉴욕 연준 EFFR·목표범위", () => fetchFedRates()],
  ];

  for (const [source, label, run] of sources) {
    const at = Date.now();
    try {
      const series = await run();
      collected.push(...series);
      console.log(`[${source}] 계열 ${series.length}개 · ${((Date.now() - at) / 1000).toFixed(1)}초`);
    } catch (error) {
      failures.push({ source, label, message: error.message });
      console.error(`[실패] ${label}: ${error.message}`);
    }
  }

  if (!collected.length) {
    console.error("[검증 실패] 수집된 계열이 0건입니다. 기존 파일을 보존하고 종료합니다.");
    failures.forEach((failure) => console.error(`  - ${failure.label}: ${failure.message}`));
    throw new Error(`계열 0건 (실패 ${failures.length}건)`);
  }

  const latest = {};
  const byFile = new Map();
  let changed = 0;
  let unchanged = 0;

  collected.forEach((series) => {
    const last = series.observations.at(-1);
    if (!last) return;
    const id = `${series.source.toLowerCase()}:${series.id}:${series.country}`;
    const fingerprint = `${last[0]}:${last[1]}`;
    if (previousLatest.get(id) === fingerprint) unchanged += 1; else changed += 1;
    latest[id] = { indicator: series.id, country: series.country, period: last[0], value: last[1] };
    const file = series.source;
    if (!byFile.has(file)) byFile.set(file, []);
    byFile.get(file).push({ id, indicator: series.id, country: series.country, freq: series.freq, observations: series.observations });
  });

  const usedCountries = [...new Set(collected.map((series) => series.country))].sort();
  const index = {
    kind: "daily",
    sources: {
      ECOS: "https://ecos.bok.or.kr/api/",
      USTREASURY: "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/",
      NYFED: "https://markets.newyorkfed.org/",
    },
    attribution: "한국은행 ECOS · U.S. Department of the Treasury · Federal Reserve Bank of New York",
    updatedAt: new Date().toISOString(),
    newestPeriod: Object.values(latest).map((entry) => entry.period).sort().at(-1) || null,
    topics: [{ ...TOPIC, file: "latest/rates.json", count: Object.keys(latest).length }],
    indicators: collected.map((series) => ({
      id: series.id, source: series.source, topic: TOPIC.id,
      nameKo: series.nameKo, nameEn: series.nameEn,
      unitKo: series.unitKo, freq: series.freq, headline: series.id === "kr-tb-10y" || series.id === "us-tb-10y",
      file: `daily/${series.source}.json`,
      countries: [series.country],
    })),
    countries: Object.fromEntries(usedCountries.map((code) => [code, COUNTRY_NAMES[code] || { ko: code, en: code }])),
    failures,
  };

  await writeAtomic(INDEX_PATH, `${JSON.stringify(index)}\n`);
  await writeAtomic(LATEST_PATH, `${JSON.stringify({ kind: "daily", topic: TOPIC.id, updatedAt: index.updatedAt, series: latest })}\n`);
  for (const [file, entries] of byFile) {
    await writeAtomic(resolve(DAILY_DIR, `${file}.json`), `${JSON.stringify({
      source: file, fetchedAt: index.updatedAt, series: entries,
    })}\n`);
  }

  const outcome = changed ? "updated" : "already-current";
  const wall = ((Date.now() - startedAt) / 1000).toFixed(1);
  console.log(`계열 ${collected.length}개 · 갱신 ${changed} · 변동 없음 ${unchanged}`);
  console.log(`최신 관측 ${index.newestPeriod} · index-daily.json ${(Buffer.byteLength(JSON.stringify(index), "utf8") / 1024).toFixed(1)}KB · ${wall}초`);
  console.log(`결과: ${outcome}`);
  if (failures.length) {
    console.warn(`[경고] 실패 ${failures.length}건 — 나머지는 정상 갱신했습니다:`);
    failures.forEach((failure) => console.warn(`  - ${failure.label}: ${failure.message}`));
  }
  // 워크플로가 읽어 요약에 찍는다.
  globalThis.__dailyRatesOutcome = outcome;
}

main()
  .catch((error) => {
    console.error(`[일간 금리 배치 실패] ${error.message}`);
    process.exitCode = 1;
    globalThis.__dailyRatesOutcome = "failed";
  })
  .finally(() => {
    globalThis.__dailyRatesBatchRuns = (globalThis.__dailyRatesBatchRuns || 0) + 1;
  });
