// scripts/build-indicators.mjs
//
// OECD 경제지표 배치. 매일 돌지만 대부분의 지표는 월간·분기라 대개 바뀌는 게 없다 —
// 그건 정상이고 오류가 아니다. 그래서 "변경 없음"과 "실패"를 확실히 구분해서 찍는다.
//
// 산출물 (3단 분할)
//   data/indicators/index.json           주제·지표·국가 목록과 (지표×국가) 존재 여부.
//                                        매 화면 로드라 가볍게 유지한다. 검색은 이것만으로 된다.
//   data/indicators/latest/<주제>.json    그 주제 계열의 최신값 1개씩. 주제를 열 때 받는다.
//   data/indicators/oecd/<dataflow>.json  전 국가 10년 시계열. 지표를 펼칠 때 받는다.
//
// 왜 셋으로 나눴나: 지표가 7개에서 47개로 늘면서 최신값까지 한 파일에 담으면 250KB가
// 넘는다. 홈 화면은 헤드라인 네 줄만 필요한데 그걸 위해 매번 250KB를 받는 것은 낭비다.
// 반대로 검색은 전 계열을 훑어야 하므로 이름은 index.json에 빠짐없이 들어 있어야 한다.
//
// 지표 하나가 실패해도 나머지는 갱신한다. 대신 실패는 이름을 달고 남고, 갱신된 계열이
// 0건이면 성공으로 끝내지 않는다.

import { mkdir, writeFile, readFile, rename, readdir, rm } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { setupUtf8Console } from "./lib/data-go-kr.mjs";
import { fetchDataflow, decodeObservations, refAreaNames } from "./lib/oecd.mjs";
import { DATAFLOWS, INDICATORS, COUNTRIES, TOPICS, matchesSelector, SOURCE_OECD } from "./lib/indicators.mjs";

setupUtf8Console();

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT_DIR = resolve(ROOT, "data", "indicators");
const OECD_DIR = resolve(OUT_DIR, "oecd");
const LATEST_DIR = resolve(OUT_DIR, "latest");
const HISTORY_YEARS = 10;

const REF_AREAS = Object.keys(COUNTRIES);

function startPeriod() {
  const year = new Date().getUTCFullYear() - HISTORY_YEARS;
  return `${year}-01`;
}

// 기간 문자열은 "2026-06"(월) / "2026-Q2"(분기) / "2026"(연) 세 형태로 온다.
// 정렬과 비교를 위해 월 단위로 환산한 문자열을 따로 만든다.
function periodRank(period) {
  const quarter = /^(\d{4})-Q([1-4])$/.exec(period);
  if (quarter) return `${quarter[1]}-${String(Number(quarter[2]) * 3).padStart(2, "0")}`;
  if (/^\d{4}$/.test(period)) return `${period}-12`;
  return period;
}

async function writeAtomic(path, text) {
  const tmp = `${path}.tmp`;
  await writeFile(tmp, text, "utf8");
  await rename(tmp, path);
}

async function readJson(path) {
  try {
    return JSON.parse(await readFile(path, "utf8"));
  } catch {
    return null;
  }
}

async function main() {
  const startedAt = Date.now();
  await mkdir(OECD_DIR, { recursive: true });
  await mkdir(LATEST_DIR, { recursive: true });

  // 직전 최신값을 모아 둔다. "갱신"과 "변동 없음"을 나눠 찍기 위한 것이다.
  const previousLatest = new Map();
  for (const topic of TOPICS) {
    const previous = await readJson(resolve(LATEST_DIR, `${topic.id}.json`));
    Object.entries(previous?.series || {}).forEach(([id, entry]) => {
      previousLatest.set(id, `${entry.period}:${entry.value}`);
    });
  }

  // dataflow 단위로 한 번만 부른다. 같은 계열을 쓰는 지표가 여럿이라 호출이 크게 준다
  // (CLI·BCI·CCI가 DF_CLI 하나, KEI 계열 지표 아홉 개가 DF_KEI 하나).
  const needed = [...new Set(INDICATORS.map((indicator) => indicator.dataflow))];
  const decoded = new Map();
  const failures = [];
  const timings = [];

  for (const key of needed) {
    const flow = DATAFLOWS[key];
    const flowStarted = Date.now();
    try {
      const json = await fetchDataflow(`OECD ${flow.file}`, flow.id, {
        refAreas: REF_AREAS,
        keyLength: flow.keyLength,
        startPeriod: startPeriod(),
      });
      const { rows } = decodeObservations(json);
      // 데이터가 없어도 HTTP 200이 온다. 0건을 성공으로 넘기면 지표가 조용히 사라진다.
      if (!rows.length) throw new Error("관측 0건 (요청은 성공했으나 데이터가 비어 있음)");
      decoded.set(key, { rows, areaNames: refAreaNames(json) });
      const seconds = Number(((Date.now() - flowStarted) / 1000).toFixed(1));
      timings.push({ flow: flow.file, seconds, rows: rows.length });
      console.log(`[${flow.file}] 관측 ${rows.length.toLocaleString("ko-KR")}건 · ${seconds}초`);
    } catch (error) {
      // 한 계열이 죽어도 나머지는 계속 간다. 실패는 어느 엔드포인트인지 이름을 달고 남는다.
      failures.push({ dataflow: flow.file, endpoint: flow.id, message: error.message });
      timings.push({ flow: flow.file, seconds: Number(((Date.now() - flowStarted) / 1000).toFixed(1)), rows: 0 });
      console.error(`[실패] ${flow.file}: ${error.message}`);
    }
  }

  const byTopic = new Map(TOPICS.map((topic) => [topic.id, {}]));
  const byFile = new Map();
  const availability = {};           // 지표 id -> 값이 있는 국가 코드 배열
  const countryNames = new Map();
  const skippedAreas = new Set();    // 한글명이 없어 건너뛴 코드
  let changed = 0;
  let unchanged = 0;
  let seriesTotal = 0;

  for (const indicator of INDICATORS) {
    const bundle = decoded.get(indicator.dataflow);
    if (!bundle) {
      failures.push({ indicator: indicator.id, endpoint: DATAFLOWS[indicator.dataflow].id, message: "상위 dataflow 조회 실패로 건너뜀" });
      continue;
    }
    const picked = bundle.rows.filter((row) => matchesSelector(row, indicator.selector));
    if (!picked.length) {
      failures.push({ indicator: indicator.id, endpoint: DATAFLOWS[indicator.dataflow].id, message: "선택자에 맞는 관측이 없음 — OECD가 코드 체계를 바꿨는지 확인하세요" });
      console.error(`[실패] ${indicator.id}: 선택자에 맞는 관측 0건`);
      continue;
    }

    const byCountry = new Map();
    picked.forEach((row) => {
      const country = row.REF_AREA;
      if (!COUNTRIES[country]) { skippedAreas.add(country); return; }
      if (!byCountry.has(country)) byCountry.set(country, []);
      byCountry.get(country).push([row.TIME_PERIOD, Number(row.value)]);
    });

    const file = DATAFLOWS[indicator.dataflow].file;
    if (!byFile.has(file)) byFile.set(file, []);
    const latestBucket = byTopic.get(indicator.topic);
    const codes = [];

    [...byCountry.entries()].forEach(([country, observations]) => {
      observations.sort((a, b) => periodRank(a[0]).localeCompare(periodRank(b[0])));
      const last = observations.at(-1);
      if (!last || !Number.isFinite(last[1])) return;

      const id = `${indicator.source.toLowerCase()}:${indicator.id}:${country}`;
      const fingerprint = `${last[0]}:${last[1]}`;
      if (previousLatest.get(id) === fingerprint) unchanged += 1;
      else changed += 1;

      countryNames.set(country, bundle.areaNames[country] || country);
      codes.push(country);
      seriesTotal += 1;
      // 관측 기간은 배치 실행 시각과 별개다. 3개월 전 값도 낡은 게 아니라 그 시점의 값이다.
      latestBucket[id] = { indicator: indicator.id, country, period: last[0], value: last[1] };
      byFile.get(file).push({ id, indicator: indicator.id, country, freq: indicator.freq, observations });
    });

    if (codes.length) availability[indicator.id] = codes.sort();
  }

  // 갱신된 계열이 0이면 성공으로 쓰지 않는다 — 기존 파일을 그대로 둔다.
  if (!seriesTotal) {
    console.error("[검증 실패] 수집된 계열이 0건입니다. 기존 파일을 보존하고 종료합니다.");
    failures.forEach((failure) => console.error(`  - ${failure.dataflow || failure.indicator}: ${failure.message}`));
    throw new Error(`계열 0건 (실패 ${failures.length}건)`);
  }

  const allPeriods = [];
  byTopic.forEach((bucket) => Object.values(bucket).forEach((entry) => allPeriods.push(periodRank(entry.period))));
  const newestPeriod = allPeriods.sort().at(-1);

  const usedCountries = [...countryNames.keys()].sort();
  const index = {
    source: SOURCE_OECD,
    sources: { OECD: "https://sdmx.oecd.org/public/rest" },
    attribution: "OECD (2026), OECD Data Explorer, https://data-explorer.oecd.org — CC BY 4.0",
    updatedAt: new Date().toISOString(),
    // 카탈로그에서 가장 최신인 관측 기간. 화면은 이 값이 지나치게 오래되면 경고한다.
    newestPeriod,
    topics: TOPICS.map((topic) => ({
      ...topic,
      file: `latest/${topic.id}.json`,
      count: Object.keys(byTopic.get(topic.id)).length,
    })),
    indicators: INDICATORS.filter((indicator) => availability[indicator.id]).map((indicator) => ({
      id: indicator.id, source: indicator.source, topic: indicator.topic,
      nameKo: indicator.nameKo, nameEn: indicator.nameEn,
      unitKo: indicator.unitKo, freq: indicator.freq, headline: Boolean(indicator.headline),
      file: `oecd/${DATAFLOWS[indicator.dataflow].file}.json`,
      countries: availability[indicator.id],
    })),
    countries: Object.fromEntries(usedCountries.map((code) => [code, { ko: COUNTRIES[code], en: countryNames.get(code) }])),
    // 홈 카드가 주제 파일을 받지 않아도 되도록 headline 지표의 값만 여기 얹는다.
    // 네 줄을 위해 주제 파일 네 개(120KB)를 받는 것은 낭비다. 어느 지표를 올릴지는
    // 여전히 indicators.mjs의 headline 플래그가 정한다.
    headlineSeries: Object.fromEntries(
      INDICATORS.filter((indicator) => indicator.headline)
        .flatMap((indicator) => ["KOR", "USA"].map((country) => {
          const id = `${indicator.source.toLowerCase()}:${indicator.id}:${country}`;
          const entry = byTopic.get(indicator.topic)[id];
          return entry ? [id, entry] : null;
        }))
        .filter(Boolean)
    ),
    failures,
  };

  await writeAtomic(resolve(OUT_DIR, "index.json"), `${JSON.stringify(index)}\n`);

  for (const topic of TOPICS) {
    await writeAtomic(resolve(LATEST_DIR, `${topic.id}.json`), `${JSON.stringify({
      source: SOURCE_OECD, topic: topic.id, updatedAt: index.updatedAt, series: byTopic.get(topic.id),
    })}\n`);
  }

  for (const [file, entries] of byFile) {
    if (!entries.length) continue;
    await writeAtomic(resolve(OECD_DIR, `${file}.json`), `${JSON.stringify({
      source: SOURCE_OECD,
      dataflow: DATAFLOWS[Object.keys(DATAFLOWS).find((key) => DATAFLOWS[key].file === file)].id,
      fetchedAt: index.updatedAt,
      historyYears: HISTORY_YEARS,
      series: entries,
    })}\n`);
  }

  // 예전 단일 파일은 지운다 — 남겨 두면 화면이 어느 쪽을 보는지 헷갈린다.
  await rm(resolve(OUT_DIR, "catalog.json"), { force: true });

  const indexBytes = Buffer.byteLength(JSON.stringify(index), "utf8");
  const topicFiles = (await readdir(LATEST_DIR)).filter((name) => name.endsWith(".json")).length;
  const wall = ((Date.now() - startedAt) / 1000).toFixed(1);
  console.log(`계열 ${seriesTotal.toLocaleString("ko-KR")}건 · 지표 ${index.indicators.length}개 · 국가 ${usedCountries.length}개`);
  console.log(`갱신 ${changed} · 변동 없음 ${unchanged} (월간·분기 지표라 대부분 그대로인 것이 정상)`);
  console.log(`최신 관측 기간 ${newestPeriod} · index.json ${(indexBytes / 1024).toFixed(1)}KB · 주제 파일 ${topicFiles}개`);
  console.log(`전체 소요 ${wall}초 — ${timings.map((t) => `${t.flow} ${t.seconds}s`).join(" · ")}`);
  if (skippedAreas.size) {
    console.warn(`[경고] 한글명이 없어 건너뛴 지역 코드 ${skippedAreas.size}개: ${[...skippedAreas].sort().join(", ")}`);
    console.warn("       필요하면 scripts/lib/indicators.mjs의 COUNTRIES에 추가하세요. 기계번역으로 지어내지 않습니다.");
  }
  if (failures.length) {
    console.warn(`[경고] 실패 ${failures.length}건 — 나머지는 정상 갱신했습니다:`);
    failures.forEach((failure) => console.warn(`  - ${failure.dataflow || failure.indicator} (${failure.endpoint}): ${failure.message}`));
  }
}

// 실행이 끝났다는 신호. import 시점에 main()을 띄우고 바로 반환하므로 회귀 테스트가
// "언제 끝났는지" 알 방법이 이것뿐이다. 고정 시간만 기다리면 재시도가 붙은 실패 경로가
// 테스트의 정리 코드보다 늦게 끝나면서 산출물을 덮어쓴다(실제로 그렇게 당했다).
main()
  .catch((error) => {
    console.error(`[지표 배치 실패] ${error.message}`);
    process.exitCode = 1;
  })
  .finally(() => {
    globalThis.__indicatorsBatchRuns = (globalThis.__indicatorsBatchRuns || 0) + 1;
  });
