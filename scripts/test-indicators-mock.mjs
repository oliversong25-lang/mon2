// scripts/test-indicators-mock.mjs
// build-indicators.mjs의 파싱·실패 격리 로직을 실제 OECD 호출 없이 검증한다.
// SDMX-JSON 픽스처는 실제 응답에서 관찰한 모양 그대로다 — 차원은 dataflow마다 순서가
// 다르고, 관측 키는 차원 값 배열의 인덱스를 콜론으로 이은 문자열이며, 데이터가 없어도
// HTTP 200에 observations만 {}로 빈다.

import { readFile, writeFile, rm, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { decodeObservations, refAreaNames } from "./lib/oecd.mjs";
import { INDICATORS, DATAFLOWS, matchesSelector } from "./lib/indicators.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT_DIR = resolve(ROOT, "data", "indicators");
const CATALOG = resolve(OUT_DIR, "catalog.json");

const results = [];
const record = (label, ok, detail) => {
  results.push({ label, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${ok ? "" : `  — ${detail}`}`);
};

// 실제 DF_CLI 응답의 축약판. 차원 순서와 관측 키 규칙을 그대로 재현한다.
function cliFixture() {
  return {
    meta: {}, errors: [],
    data: {
      structures: [{
        dimensions: {
          observation: [
            { id: "REF_AREA", name: "Reference area", values: [{ id: "KOR", name: "Korea" }, { id: "USA", name: "United States" }] },
            { id: "FREQ", name: "Frequency", values: [{ id: "M", name: "Monthly" }] },
            { id: "MEASURE", name: "Measure", values: [{ id: "LI", name: "CLI" }, { id: "CCICP", name: "CCI" }] },
            { id: "ADJUSTMENT", name: "Adjustment", values: [{ id: "AA", name: "Amplitude adjusted" }, { id: "NOR", name: "Normalized" }] },
            { id: "TRANSFORMATION", name: "Transformation", values: [{ id: "IX", name: "Index" }] },
            { id: "METHODOLOGY", name: "Methodology", values: [{ id: "H", name: "OECD harmonised" }] },
            { id: "TIME_PERIOD", name: "Time period", values: [{ id: "2026-05", name: "2026-05" }, { id: "2026-06", name: "2026-06" }] },
          ],
        },
        attributes: { observation: [] },
      }],
      dataSets: [{
        observations: {
          // KOR / M / LI / AA / IX / H
          "0:0:0:0:0:0:0": [101.5, 0], "0:0:0:0:0:0:1": [102.87, 0],
          // KOR / M / CCICP / AA — 같은 dataflow에서 다른 지표
          "0:0:1:0:0:0:1": [100.72, 0],
          // USA / M / LI / AA — 한국과 같은 계열
          "1:0:0:0:0:0:1": [100.8, 0],
          // KOR / M / LI / NOR — 선택자가 걸러내야 하는 다른 조정치
          "0:0:0:1:0:0:1": [999, 0],
        },
      }],
    },
  };
}

// 데이터가 없을 때의 실제 응답: HTTP 200, errors 없음, observations만 빈 객체.
function emptyFixture() {
  return {
    meta: {}, errors: [],
    data: {
      structures: [{ dimensions: { observation: [{ id: "REF_AREA", values: [] }, { id: "TIME_PERIOD", values: [] }] }, attributes: { observation: [] } }],
      dataSets: [{ observations: {} }],
    },
  };
}

// --- 1. 디코딩: 관측 키가 차원 인덱스라는 것 ---
try {
  const { rows, dimensionIds } = decodeObservations(cliFixture());
  record("차원 id 목록을 순서대로 읽는다", dimensionIds[0] === "REF_AREA" && dimensionIds.at(-1) === "TIME_PERIOD", dimensionIds.join(","));
  record("관측 키가 차원 값 인덱스로 해석된다", rows.length === 5, `${rows.length}행`);
  const kor = rows.find((row) => row.REF_AREA === "KOR" && row.MEASURE === "LI" && row.ADJUSTMENT === "AA" && row.TIME_PERIOD === "2026-06");
  record("KOR CLI 최신값이 정확히 디코딩된다", kor && kor.value === 102.87, JSON.stringify(kor));
  const names = refAreaNames(cliFixture());
  record("국가 영문명을 응답에서 그대로 얻는다", names.KOR === "Korea" && names.USA === "United States", JSON.stringify(names));
} catch (error) {
  record("디코딩이 예외 없이 동작", false, error.message);
}

// --- 2. 선택자: 같은 dataflow에서 지표를 갈라낸다 ---
try {
  const { rows } = decodeObservations(cliFixture());
  const cli = INDICATORS.find((indicator) => indicator.id === "cli");
  const cci = INDICATORS.find((indicator) => indicator.id === "cci");
  const cliRows = rows.filter((row) => matchesSelector(row, cli.selector));
  const cciRows = rows.filter((row) => matchesSelector(row, cci.selector));
  record("CLI 선택자가 다른 조정치(NOR)를 걸러낸다", cliRows.every((row) => row.ADJUSTMENT === "AA") && !cliRows.some((row) => row.value === 999), JSON.stringify(cliRows.map((row) => row.value)));
  record("CLI·CCI가 같은 dataflow에서 갈라진다", cliRows.length === 3 && cciRows.length === 1, `CLI ${cliRows.length} / CCI ${cciRows.length}`);
  record("한국과 미국이 같은 계열에서 나온다", new Set(cliRows.map((row) => row.REF_AREA)).size === 2, JSON.stringify(cliRows.map((row) => row.REF_AREA)));
} catch (error) {
  record("선택자 동작", false, error.message);
}

// --- 3. 빈 응답을 성공으로 넘기지 않는다 ---
try {
  const { rows } = decodeObservations(emptyFixture());
  record("데이터 없는 200 응답은 관측 0건으로 잡힌다", rows.length === 0, `${rows.length}행`);
} catch (error) {
  record("빈 응답 처리", false, error.message);
}

// --- 4. 실패 격리: 한 dataflow가 죽어도 나머지는 갱신된다 ---
// 실제 배치를 mock fetch로 돌린다. DF_CLI만 살리고 나머지는 네트워크 오류로 만든다.
const originalCatalog = existsSync(CATALOG) ? await readFile(CATALOG, "utf8") : null;
const originalFiles = new Map();
for (const key of Object.keys(DATAFLOWS)) {
  const path = resolve(OUT_DIR, "oecd", `${DATAFLOWS[key].file}.json`);
  if (existsSync(path)) originalFiles.set(path, await readFile(path, "utf8"));
}

function restore() {
  const jobs = [];
  jobs.push(originalCatalog === null ? rm(CATALOG, { force: true }) : writeFile(CATALOG, originalCatalog, "utf8"));
  for (const [path, text] of originalFiles) jobs.push(writeFile(path, text, "utf8"));
  return Promise.all(jobs);
}
process.on("exit", () => {
  // 동기 복원이 필요하지만 fs/promises라 여기서는 최선만 한다 — 정상 경로는 아래 finally다.
});

try {
  await mkdir(resolve(OUT_DIR, "oecd"), { recursive: true });
  const logs = [];
  const originalError = console.error;
  const originalWarn = console.warn;
  const originalLog = console.log;
  console.error = (...args) => logs.push(args.join(" "));
  console.warn = (...args) => logs.push(args.join(" "));
  console.log = () => {};

  global.fetch = async (input) => {
    const url = String(input);
    if (url.includes("DF_CLI")) {
      return { ok: true, status: 200, json: async () => cliFixture(), text: async () => JSON.stringify(cliFixture()) };
    }
    // 나머지 계열은 네트워크 오류
    const error = new TypeError("fetch failed");
    error.cause = Object.assign(new Error("getaddrinfo ENOTFOUND sdmx.oecd.org"), { code: "ENOTFOUND" });
    throw error;
  };

  process.exitCode = undefined;
  const before = globalThis.__indicatorsBatchRuns || 0;
  await import(`./build-indicators.mjs?t=${Date.now()}`);
  // 실패 경로는 재시도 때문에 몇 초 걸린다. 실행이 끝났다는 신호를 기다리지 않으면
  // 아래 복원이 배치보다 먼저 돌아 산출물이 픽스처인 채로 남는다.
  const deadline = Date.now() + 30000;
  while ((globalThis.__indicatorsBatchRuns || 0) === before && Date.now() < deadline) {
    await new Promise((done) => setTimeout(done, 50));
  }
  const exitCode = process.exitCode;
  process.exitCode = undefined;

  console.error = originalError;
  console.warn = originalWarn;
  console.log = originalLog;

  const joined = logs.join(" | ");
  record("한 계열이 실패해도 배치가 죽지 않는다", exitCode !== 1, `exitCode=${exitCode}`);
  record("실패한 엔드포인트 이름이 로그에 남는다", /DF_PRICES_ALL|DF_INDSERV|DF_IALFS_UNE_M/.test(joined) && /OECD/.test(joined), joined.slice(0, 200));

  const written = JSON.parse(await readFile(CATALOG, "utf8"));
  const cliSeries = written.series.filter((entry) => entry.indicator === "cli");
  record("살아남은 계열은 정상 기록된다 (CLI)", cliSeries.length === 2, `${cliSeries.length}건`);
  record("실패가 catalog.failures에 남는다", written.failures.length > 0, JSON.stringify(written.failures.slice(0, 2)));
  record("실패한 지표는 계열에 포함되지 않는다", !written.series.some((entry) => entry.indicator === "cpi"), "cpi가 남아 있음");
  record("관측 기간이 값과 함께 저장된다", cliSeries.every((entry) => /^\d{4}-\d{2}$/.test(entry.period)), JSON.stringify(cliSeries.map((entry) => entry.period)));
} catch (error) {
  record("실패 격리 경로", false, error.message);
} finally {
  await restore();
}

const failed = results.filter((result) => !result.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exitCode = 1;
