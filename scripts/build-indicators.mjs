// scripts/build-indicators.mjs
//
// OECD 경제지표 배치. 매일 돌지만 대부분의 지표는 월간·분기라 대개 바뀌는 게 없다 —
// 그건 정상이고 오류가 아니다. 그래서 "변경 없음"과 "실패"를 확실히 구분해서 찍는다.
//
// 산출물
//   data/indicators/catalog.json          모든 계열의 메타데이터(값 1개씩). 매 화면 로드.
//   data/indicators/oecd/<dataflow>.json  지표별 전 국가 10년 시계열. 필요할 때만 로드.
//
// 지표 하나가 실패해도 나머지는 갱신한다. 대신 실패는 이름을 달고 남고, 갱신된 계열이
// 0건이면 성공으로 끝내지 않는다.

import { mkdir, writeFile, readFile, rename } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { setupUtf8Console } from "./lib/data-go-kr.mjs";
import { fetchDataflow, decodeObservations, refAreaNames } from "./lib/oecd.mjs";
import { DATAFLOWS, INDICATORS, COUNTRIES, matchesSelector, SOURCE_OECD } from "./lib/indicators.mjs";

setupUtf8Console();

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const OUT_DIR = resolve(ROOT, "data", "indicators");
const OECD_DIR = resolve(OUT_DIR, "oecd");
const HISTORY_YEARS = 10;

const REF_AREAS = Object.keys(COUNTRIES);

function startPeriod() {
  const year = new Date().getUTCFullYear() - HISTORY_YEARS;
  return `${year}-01`;
}

// 기간 문자열은 "2026-06"(월) / "2026-Q2"(분기) / "2026"(연) 세 형태로 온다.
// 정렬과 비교를 위해 사전순으로 다룰 수 있게 그대로 두되, 최신 판별만 따로 한다.
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
  await mkdir(OECD_DIR, { recursive: true });

  const previousCatalog = await readJson(resolve(OUT_DIR, "catalog.json"));
  const previousLatest = new Map(
    (previousCatalog?.series || []).map((entry) => [entry.id, `${entry.period}:${entry.value}`])
  );

  // dataflow 단위로 한 번만 부른다. CLI·CCI·BCI가 같은 계열이라 호출이 세 번이 아니라 한 번이다.
  const needed = [...new Set(INDICATORS.map((indicator) => indicator.dataflow))];
  const decoded = new Map();
  const failures = [];

  for (const key of needed) {
    const flow = DATAFLOWS[key];
    try {
      const json = await fetchDataflow(`OECD ${flow.file}`, flow.id, {
        refAreas: REF_AREAS,
        keyLength: flow.keyLength,
        startPeriod: startPeriod(),
      });
      const { rows } = decodeObservations(json);
      // 데이터가 없어도 HTTP 200이 온다. 0건을 성공으로 넘기면 지표가 조용히 사라진다.
      if (!rows.length) throw new Error(`관측 0건 (요청은 성공했으나 데이터가 비어 있음)`);
      decoded.set(key, { rows, areaNames: refAreaNames(json) });
      console.log(`[${flow.file}] 관측 ${rows.length.toLocaleString("ko-KR")}건`);
    } catch (error) {
      // 한 계열이 죽어도 나머지는 계속 간다. 실패는 어느 엔드포인트인지 이름을 달고 남는다.
      failures.push({ dataflow: flow.file, endpoint: flow.id, message: error.message });
      console.error(`[실패] ${flow.file}: ${error.message}`);
    }
  }

  const series = [];
  const countryNames = new Map();
  const byFile = new Map();
  let changed = 0;
  let unchanged = 0;

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
      if (!COUNTRIES[country]) return;
      if (!byCountry.has(country)) byCountry.set(country, []);
      byCountry.get(country).push([row.TIME_PERIOD, Number(row.value)]);
    });

    const file = DATAFLOWS[indicator.dataflow].file;
    if (!byFile.has(file)) byFile.set(file, []);

    [...byCountry.entries()].forEach(([country, observations]) => {
      observations.sort((a, b) => periodRank(a[0]).localeCompare(periodRank(b[0])));
      const last = observations.at(-1);
      if (!last || !Number.isFinite(last[1])) return;

      const id = `${SOURCE_OECD.toLowerCase()}:${indicator.id}:${country}`;
      const fingerprint = `${last[0]}:${last[1]}`;
      if (previousLatest.get(id) === fingerprint) unchanged += 1;
      else changed += 1;

      // 카탈로그는 매 화면 로드라 한 줄을 최대한 짧게 둔다. 지표명·단위·파일 경로는
      // indicators[]에, 국가명은 countries{}에 한 번만 두고 여기서는 코드만 들고 간다.
      countryNames.set(country, bundle.areaNames[country] || country);
      series.push({
        id,
        indicator: indicator.id,
        country,
        freq: indicator.freq,
        // 관측 기간은 배치 실행 시각과 별개다. 3개월 전 값도 낡은 게 아니라 그 시점의 값이다.
        period: last[0],
        value: last[1],
      });

      byFile.get(file).push({ id, indicator: indicator.id, country, freq: indicator.freq, observations });
    });
  }

  // 갱신된 계열이 0이면 성공으로 쓰지 않는다 — 기존 파일을 그대로 둔다.
  if (!series.length) {
    console.error("[검증 실패] 수집된 계열이 0건입니다. 기존 파일을 보존하고 종료합니다.");
    failures.forEach((failure) => console.error(`  - ${failure.dataflow || failure.indicator}: ${failure.message}`));
    throw new Error(`계열 0건 (실패 ${failures.length}건)`);
  }

  const newestPeriod = series.map((entry) => periodRank(entry.period)).sort().at(-1);

  const catalog = {
    source: SOURCE_OECD,
    sources: { OECD: "https://sdmx.oecd.org/public/rest" },
    attribution: "OECD (2026), OECD Data Explorer, https://data-explorer.oecd.org — CC BY 4.0",
    updatedAt: new Date().toISOString(),
    // 카탈로그에서 가장 최신인 관측 기간. 화면은 이 값이 지나치게 오래되면 경고한다.
    newestPeriod,
    countries: Object.fromEntries([...countryNames.entries()].map(([code, en]) => [code, { ko: COUNTRIES[code], en }])),
    indicators: INDICATORS.map((indicator) => ({
      id: indicator.id, nameKo: indicator.nameKo, nameEn: indicator.nameEn,
      unitKo: indicator.unitKo, freq: indicator.freq, headline: Boolean(indicator.headline),
      file: `oecd/${DATAFLOWS[indicator.dataflow].file}.json`,
    })),
    failures,
    series,
  };

  await writeAtomic(resolve(OUT_DIR, "catalog.json"), `${JSON.stringify(catalog)}\n`);

  for (const [file, entries] of byFile) {
    if (!entries.length) continue;
    await writeAtomic(resolve(OECD_DIR, `${file}.json`), `${JSON.stringify({
      source: SOURCE_OECD,
      dataflow: DATAFLOWS[Object.keys(DATAFLOWS).find((key) => DATAFLOWS[key].file === file)].id,
      fetchedAt: new Date().toISOString(),
      historyYears: HISTORY_YEARS,
      series: entries,
    })}\n`);
  }

  const catalogBytes = Buffer.byteLength(JSON.stringify(catalog), "utf8");
  console.log(`계열 ${series.length}건 · 갱신 ${changed} · 변동 없음 ${unchanged} (월간·분기 지표라 대부분 그대로인 것이 정상)`);
  console.log(`최신 관측 기간: ${newestPeriod} · catalog.json ${(catalogBytes / 1024).toFixed(1)}KB`);
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
