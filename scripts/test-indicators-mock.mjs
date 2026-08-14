// scripts/test-indicators-mock.mjs
// build-indicators.mjs의 파싱·실패 격리 로직을 실제 OECD 호출 없이 검증한다.
// SDMX-JSON 픽스처는 실제 응답에서 관찰한 모양 그대로다 — 차원은 dataflow마다 순서가
// 다르고, 관측 키는 차원 값 배열의 인덱스를 콜론으로 이은 문자열이며, 데이터가 없어도
// HTTP 200에 observations만 {}로 빈다.

import { readFile, writeFile, rm, mkdir } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { INDICATORS, DATAFLOWS, matchesSelector, TOPICS } from "./lib/indicators.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT_DIR = resolve(ROOT, "data", "indicators");
const INDEX = resolve(OUT_DIR, "index.json");
const LATEST_DIR = resolve(OUT_DIR, "latest");

// mock fetch로 도는 테스트가 실제 스로틀(8초)과 429 백오프(15초)를 기다릴
// 이유가 없다. dataflow가 13개라 그대로 두면 한 번 도는 데 5분을 넘긴다.
process.env.OECD_MIN_GAP_MS = "0";
process.env.OECD_RETRY_BASE_MS = "0";

// oecd.mjs는 import 시점에 스로틀 값을 읽으므로 env를 세운 뒤에 부른다.
const { decodeObservations, refAreaNames } = await import("./lib/oecd.mjs");

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
const originalIndex = existsSync(INDEX) ? await readFile(INDEX, "utf8") : null;
const originalFiles = new Map();
for (const key of Object.keys(DATAFLOWS)) {
  const path = resolve(OUT_DIR, "oecd", `${DATAFLOWS[key].file}.json`);
  if (existsSync(path)) originalFiles.set(path, await readFile(path, "utf8"));
}
for (const topic of TOPICS) {
  const path = resolve(LATEST_DIR, `${topic.id}.json`);
  if (existsSync(path)) originalFiles.set(path, await readFile(path, "utf8"));
}

function restore() {
  const jobs = [];
  jobs.push(originalIndex === null ? rm(INDEX, { force: true }) : writeFile(INDEX, originalIndex, "utf8"));
  for (const [path, text] of originalFiles) jobs.push(writeFile(path, text, "utf8"));
  return Promise.all(jobs);
}

try {
  await mkdir(resolve(OUT_DIR, "oecd"), { recursive: true });
  await mkdir(LATEST_DIR, { recursive: true });
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
  // 실행이 끝났다는 신호를 기다리지 않으면 아래 복원이 배치보다 먼저 돌아
  // 산출물이 픽스처인 채로 남는다(실제로 그렇게 당했다).
  const deadline = Date.now() + 60000;
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
  record("실패한 엔드포인트 이름이 로그에 남는다",
    /DF_PRICES_ALL|DF_FINMARK|DF_BOP/.test(joined) && /OECD\.SDD/.test(joined), joined.slice(0, 240));

  const written = JSON.parse(await readFile(INDEX, "utf8"));
  const cycle = JSON.parse(await readFile(resolve(LATEST_DIR, "cycle.json"), "utf8"));
  const cliSeries = Object.values(cycle.series).filter((entry) => entry.indicator === "cli");
  record("살아남은 계열은 정상 기록된다 (CLI)", cliSeries.length === 2, `${cliSeries.length}건`);
  record("실패가 index.failures에 남는다", written.failures.length > 0, JSON.stringify(written.failures.slice(0, 2)));
  record("실패한 지표는 목록에서 빠진다", !written.indicators.some((entry) => entry.id === "cpi"), "cpi가 남아 있음");
  record("관측 기간이 값과 함께 저장된다",
    cliSeries.every((entry) => /^\d{4}-\d{2}$/.test(entry.period)), JSON.stringify(cliSeries.map((entry) => entry.period)));

  // 3.2: 카탈로그는 인덱스와 상세로 나뉜다. 인덱스는 매 화면 로드라 가벼워야 한다.
  const indexBytes = Buffer.byteLength(JSON.stringify(written), "utf8");
  record("index.json이 화면마다 받아도 될 만큼 가볍다 (<64KB)", indexBytes < 64 * 1024, `${(indexBytes / 1024).toFixed(1)}KB`);
  record("인덱스에는 값이 아니라 이름과 존재 여부만 담긴다",
    written.indicators.every((entry) => Array.isArray(entry.countries) && entry.file),
    JSON.stringify(written.indicators[0] || null).slice(0, 160));
} catch (error) {
  record("실패 격리 경로", false, error.message);
} finally {
  await restore();
}

// --- 5. 실제 산출물의 모양 (3.9 검증) ---
// 위 실패 격리는 복원까지 끝났으므로 여기서는 커밋된 진짜 산출물을 본다.
try {
  const real = JSON.parse(await readFile(INDEX, "utf8"));
  const topicIds = real.topics.map((topic) => topic.id);

  record("주제가 지표의 1차 축이다", topicIds.length >= 5 && real.indicators.every((entry) => topicIds.includes(entry.topic)),
    `주제 ${topicIds.join(",")}`);

  // 3.4: 검색은 기본 브라우즈 트리에 없는 지표도 찾아야 한다. 화면은 갈래마다
  // 지표를 접어 두므로, 첫 갈래의 첫 지표가 아닌 것을 하나 골라 이름으로 찾는다.
  const hidden = real.indicators.filter((entry) => entry.topic !== real.topics[0].id && !entry.headline);
  record("기본 화면에 안 열리는 지표가 카탈로그에 있다", hidden.length > 0, `${hidden.length}개`);
  const target = hidden.find((entry) => entry.nameKo.includes("환율")) || hidden[0];
  const needle = target.nameKo.slice(0, 3);
  const matched = real.indicators.filter((entry) => entry.nameKo.includes(needle));
  record(`검색어 "${needle}"로 기본 트리 밖의 지표가 찾힌다`, matched.some((entry) => entry.id === target.id),
    matched.map((entry) => entry.id).join(",") || "0건");

  // 3.4: 갈래 우선과 국가 우선이 같은 계열에 닿아야 한다.
  const sample = real.indicators.find((entry) => entry.countries.includes("KOR"));
  const viaTopic = real.indicators.filter((entry) => entry.topic === sample.topic && entry.countries.includes("KOR")).map((entry) => entry.id);
  const viaCountry = real.indicators.filter((entry) => entry.countries.includes("KOR") && entry.topic === sample.topic).map((entry) => entry.id);
  record("갈래 우선과 국가 우선이 같은 계열에 닿는다",
    viaTopic.length > 0 && viaTopic.join() === viaCountry.join(), `${viaTopic.length}개 / ${viaCountry.length}개`);

  // 3.4: 모든 값이 관측 기간과 주기를 갖는다.
  const latest = JSON.parse(await readFile(resolve(LATEST_DIR, `${real.topics[0].id}.json`), "utf8"));
  const entries = Object.values(latest.series);
  record("모든 값이 관측 기간을 갖는다",
    entries.length > 0 && entries.every((entry) => /^\d{4}(-\d{2}|-Q[1-4])?$/.test(String(entry.period))),
    `${entries.length}건 중 ${entries.filter((entry) => !entry.period).length}건 누락`);
  record("모든 지표가 주기를 갖는다",
    real.indicators.every((entry) => ["M", "Q", "A"].includes(entry.freq)),
    real.indicators.filter((entry) => !["M", "Q", "A"].includes(entry.freq)).map((entry) => entry.id).join(","));

  // 3.5: 홈 카드가 주제 파일 없이 그려지려면 헤드라인 값이 인덱스에 있어야 한다.
  record("헤드라인 값이 인덱스에 실려 있다",
    Object.keys(real.headlineSeries || {}).length > 0 && real.indicators.some((entry) => entry.headline),
    `${Object.keys(real.headlineSeries || {}).length}건`);

  // 3.2: 한글 이름은 지어내지 않는다 — 있는 것만 담고, 영문명도 함께 남긴다.
  record("국가는 한글·영문 이름을 함께 갖는다",
    Object.values(real.countries).every((country) => country.ko && country.en),
    JSON.stringify(Object.entries(real.countries)[0]));
} catch (error) {
  record("실제 산출물 검증", false, error.message);
}

const failed = results.filter((result) => !result.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exitCode = 1;
