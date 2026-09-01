// scripts/build-business-cycle.mjs
// 미국 4국면 모델의 출하 번들을 앱이 읽을 수 있는 정적 JSON 하나로 옮긴다.
// 지금 실리는 것은 **v1.1 엔진 + persist17w 경계**이며, 그 사실이 payload.variant에 있다.
//
// ── 왜 내보내기인가 ───────────────────────────────────────────────────────
// 모델은 파이썬이고 앱은 정적 파일이다. 브라우저에서 모델을 돌릴 수 없으므로,
// 다른 배치(build-indicators, build-quotes)와 같은 방식으로 **결과만** 옮긴다.
// 여기서 값을 다시 계산하지 않는다 — 계산이 두 곳에 있으면 언젠가 갈라진다.
//
// ── 왜 모델 저장소를 가리키나 ─────────────────────────────────────────────
// 모델은 같은 저장소의 `model/*` 브랜치에서 개발됐고 앱은 main에 있다. 산출물을
// main에 커밋해 두면 앱은 브랜치를 몰라도 되고, 모델을 다시 돌린 날에만 이 스크립트를
// 실행하면 된다. 경로는 `--model-outputs`로 바꿀 수 있다(기본값은 옆 워크트리).
//
// ── 무엇을 반드시 함께 옮기나 ─────────────────────────────────────────────
// 국면 이름만 옮기면 안 된다. 이 모델은 `provisional`이고 증거 품질이 낮을 수 있으며
// 회복 인식이 늦을 수 있다는 사실이 결론과 **같은 무게**로 붙어 있어야 한다.
// 그래서 `modelStatus`, `evidenceQuality`, `recoveryLatencyWarning`, `limitations`를
// 선택 항목이 아니라 필수 항목으로 두고, 하나라도 없으면 빌드를 실패시킨다.

import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const HERE = dirname(fileURLToPath(import.meta.url));
const ROOT = resolve(HERE, "..");

// 기본값은 형제 워크트리다. 모델 브랜치를 따로 체크아웃해 두는 지금 구조를 그대로 따른다.
const DEFAULT_MODEL_OUTPUTS = resolve(ROOT, "..", "mon2-bc", "model", "outputs");
const OUT_DIR = join(ROOT, "data", "business-cycle");
const OUT_FILE = join(OUT_DIR, "us.json");

// 4국면. 순서는 순환 순서이며, 화면이 임의로 재정렬하지 않도록 여기서 정한다.
const PHASES = ["recovery", "expansion", "slowdown", "contraction"];

const PHASE_KO = {
  recovery: "회복기",
  expansion: "확장기",
  slowdown: "후퇴기",
  contraction: "침체기",
};

const DOMAIN_KO = {
  production: "생산",
  employment: "고용",
  real_income: "실질소득",
  consumption: "소비·실질판매",
  labor_stress: "노동시장 스트레스",
};

// 화면이 그대로 쓰는 상태 어휘. 영어 키를 화면에 노출하지 않기 위해 여기서 한 번만 옮긴다.
const STATUS_KO = {
  official: "확정",
  preliminary: "예비",
  withheld: "판정 보류",
};

const ALERT_KO = {
  none: "없음",
  watch: "주시",
  elevated: "높아짐",
  high: "높음",
};

function arg(name, fallback) {
  const hit = process.argv.find((value) => value.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : fallback;
}

function readJson(path) {
  return JSON.parse(readFileSync(path, "utf8"));
}

// CSV는 모델이 쓴 것이고 따옴표가 없다(전부 숫자·짧은 식별자). 그래서 단순 분해로 충분하다.
// 만약 따옴표가 생기면 값이 조용히 어긋나므로, 그 경우를 감지해 즉시 멈춘다.
function readCsv(path) {
  const text = readFileSync(path, "utf8").trim();
  if (text.includes('"')) throw new Error(`${path}에 따옴표가 있습니다. 파서를 손봐야 합니다.`);
  const [head, ...rows] = text.split(/\r?\n/);
  const columns = head.split(",");
  return rows.map((line) => {
    const cells = line.split(",");
    const row = {};
    columns.forEach((name, index) => { row[name] = cells[index]; });
    return row;
  });
}

// 소수점은 6자리에서 끊는다. 모델이 쓴 부동소수 전체 자릿수를 그대로 실으면
// 688주 파일이 두 배가 되는데, 화면은 그 아래 자리를 쓰지 않는다.
function num(value, digits = 6) {
  if (value === undefined || value === "" || value === "nan") return null;
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return null;
  return Number(parsed.toFixed(digits));
}

function bool(value) {
  return value === "True" || value === "true" || value === "1";
}

// 판정 보류 주에는 공식 국면이 없다. 빈 문자열을 "국면 없음"으로 바꾸지 않고 null로 둔다 —
// 화면이 빈칸과 보류를 다르게 그려야 하기 때문이다.
function phaseOrNull(value) {
  return PHASES.includes(value) ? value : null;
}

function main() {
  const outputs = resolve(arg("model-outputs", DEFAULT_MODEL_OUTPUTS));
  if (!existsSync(outputs)) {
    throw new Error(
      `모델 산출물 폴더가 없습니다: ${outputs}\n` +
      `--model-outputs=<경로> 로 지정하세요.`
    );
  }

  // 출하 번들. 여기서 나온 것이 앱에 실리며, 동결 v1.1 산출물은 읽기만 한다.
  const ship = join(outputs, "ship");
  const currentPath = join(ship, "current_state_output.json");
  const variantPath = join(ship, "variant.json");
  const maturityPath = join(ship, "maturity.json");
  const variancePath = join(ship, "variance_distribution.json");
  const verificationPath = join(ship, "verification.json");
  const pathCsv = join(ship, "weekly_path.csv");
  const manifestPath = join(outputs, "state_semantics", "operational_manifest.json");
  const decisionPath = join(outputs, "state_semantics", "state_semantics_decision.json");
  for (const path of [currentPath, variantPath, maturityPath, variancePath, verificationPath, pathCsv, manifestPath, decisionPath]) {
    if (!existsSync(path)) throw new Error(`모델 산출물이 없습니다: ${path}`);
  }

  const current = readJson(currentPath);
  const manifest = readJson(manifestPath);
  const decision = readJson(decisionPath);
  const variant = readJson(variantPath);
  const maturity = readJson(maturityPath);
  const variance = readJson(variancePath);
  const verification = readJson(verificationPath);

  // ── 변형 식별 ──────────────────────────────────────────────────────────
  // 버전 문자열 하나에 기대지 않는다. "v1.1"만 보고 v1.1 숫자라고 읽는 사람이 생기면
  // 그 오해는 조용하고 되돌리기 어렵다. 그래서 변형을 별도 항목으로 싣고, 없으면 멈춘다.
  if (!variant.id) throw new Error("변형 식별자가 없습니다.");
  if (variant.transition_gate_applied !== false) {
    throw new Error(`트랙 16 전이 게이트가 적용된 번들입니다: ${variant.transition_gate_applied}`);
  }
  // 모델 쪽이 이미 검증했지만 여기서 한 번 더 본다 — 번들과 내보내기가 다른 날 돌 수 있다.
  if (!verification.agrees) throw new Error(`모델 검증이 통과하지 않은 번들입니다: ${JSON.stringify(verification)}`);

  // ── 성숙도 검증 범위 ──────────────────────────────────────────────────
  // 범위가 데이터에 없으면 화면은 네 국면 모두에 신호를 붙인다. 산문이 아니라 항목으로
  // 실려야 하고, 비어 있으면 멈춘다.
  if (!Array.isArray(maturity.validation_scope) || maturity.validation_scope.length !== PHASES.length) {
    throw new Error("성숙도 검증 범위가 네 국면을 덮지 않습니다.");
  }
  if (!Array.isArray(maturity.validated_phases) || !maturity.validated_phases.length) {
    throw new Error("성숙도에서 검증된 국면이 하나도 없습니다.");
  }
  // 검증되지 않은 국면에 문구가 붙어 있으면 검증된 것처럼 보인다.
  if (maturity.current && !maturity.current.validated && maturity.current.wording) {
    throw new Error("검증되지 않은 국면에 성숙도 문구가 붙어 있습니다.");
  }

  // ── 분산 분포 ─────────────────────────────────────────────────────────
  // 두 묶음이 기본이다. 네 숫자만 있으면 화면이 순위를 만들어 버린다.
  if (!Array.isArray(variance.groups) || variance.groups.length !== 2) {
    throw new Error("분산 분포가 두 묶음이 아닙니다.");
  }
  if (!Array.isArray(variance.detail_by_phase) || variance.detail_by_phase.some((row) => row.episodes === undefined)) {
    throw new Error("분산 분포 상세에 에피소드 수가 없습니다.");
  }

  // 결론과 한계를 함께 싣지 않으면 내보내지 않는다. 화면에서 빠뜨릴 수 있는 것을
  // 애초에 데이터에서 뺄 수 없게 만든다.
  for (const key of [
    "model_status",
    "evidence_quality",
    "recovery_latency_warning",
    "known_limitations",
    // 해석 경계는 평평한 한계 목록에도 들어 있지만, 화면이 어느 것을 국면 판독 옆에
    // 붙여야 하는지 고르려면 구조가 있어야 한다. 목록만 남고 구조가 빠지면 B가 조용히
    // 다른 한계 다섯 줄 사이에 묻히므로, 같은 무게로 필수 항목에 둔다.
    "interpretation_boundaries",
  ]) {
    if (current[key] === undefined) throw new Error(`현재상태 산출물에 ${key}가 없습니다.`);
  }
  if (!Array.isArray(current.interpretation_boundaries) || !current.interpretation_boundaries.length) {
    throw new Error("해석 경계가 비어 있습니다.");
  }
  // 화면에 떠야 하는 경계가 실제로 하나 있는지 본다. `surface`가 전부 documentation이면
  // 데이터는 갖춰졌는데 사용자는 아무것도 못 보는 상태가 된다.
  const surfaced = current.interpretation_boundaries.filter((entry) => entry.surface === "app_phase_reading");
  if (!surfaced.length) throw new Error("국면 판독 옆에 띄울 해석 경계가 없습니다.");
  for (const entry of current.interpretation_boundaries) {
    if (!entry.id || !entry.title || !entry.text) {
      throw new Error(`해석 경계에 id·title·text가 모두 있어야 합니다: ${JSON.stringify(entry)}`);
    }
  }
  // 폭은 집중도의 부분적 화면이다. 그 사실이 데이터에서 빠지면 화면이 좁은 폭을
  // 아무 뜻 없는 숫자로 보여주게 된다.
  if (!current.breadth || !current.breadth.partial_concentration_screen) {
    throw new Error("폭에 집중도 부분 화면 설명이 없습니다.");
  }
  if (current.model_status !== "provisional") {
    throw new Error(`모델 상태가 provisional이 아닙니다: ${current.model_status}`);
  }

  // 주간 경로. 엄격 실시간(ALFRED) 재구성이며, 화면에 필요한 열만 남긴다.
  const history = readCsv(pathCsv).map((row) => ({
    week: row.as_of,
    official: phaseOrNull(row.official_phase),
    raw: phaseOrNull(row.raw_phase),
    status: row.phase_status,
    level: num(row.activity_level),
    momentum: num(row.activity_momentum),
    separation: num(row.phase_separation),
    evidenceQuality: bool(row.evidence_quality_high) ? "high" : "low",
    alert: row.recession_alert,
    confirmingDomains: num(row.confirming_domains),
  }));
  if (!history.length) throw new Error("주간 경로가 비어 있습니다.");

  const last = history[history.length - 1];
  if (last.week !== current.as_of_date) {
    throw new Error(
      `현재상태(${current.as_of_date})와 주간 경로 마지막 주(${last.week})가 다릅니다. ` +
      `두 산출물이 같은 실행에서 나왔는지 확인하세요.`
    );
  }

  // 공식 국면이 실제로 바뀐 지점. 국면 시계를 강요하지 않으므로 인접 여부를 따지지 않는다.
  const transitions = [];
  for (let i = 1; i < history.length; i += 1) {
    const before = history[i - 1].official;
    const after = history[i].official;
    if (before && after && before !== after) {
      transitions.push({ week: history[i].week, from: before, to: after });
    }
  }

  const counts = {};
  PHASES.forEach((name) => { counts[name] = 0; });
  let withheld = 0;
  let preliminary = 0;
  history.forEach((row) => {
    if (row.status === "withheld") withheld += 1;
    else if (row.status === "preliminary") preliminary += 1;
    if (row.official) counts[row.official] += 1;
  });

  const payload = {
    model: "us_four_phase_v1",
    // 엔진과 임계값은 동결 v1.1 그대로이고 바뀐 것은 관측층의 후퇴기 경계 하나다.
    // 그래서 버전에 둘을 다 적고, 그것과 별개로 `variant`를 따로 싣는다.
    version: `v1.1+${variant.id}`,
    baseVersion: "v1.1",
    variant: variant,
    region: "US",
    modelStatus: current.model_status,
    // 잠금은 "운영에 쓸 수 있다"는 뜻이지 "검증이 끝났다"는 뜻이 아니다. 둘을 같이 싣는다.
    stateSemanticsDecision: decision.classification,
    isFinalValidation: false,
    developmentStopped: manifest.us_four_phase_model_development === "stopped",
    generatedAt: new Date().toISOString(),
    provenance: {
      sourceCommit: current.provenance.source_commit,
      configHash: current.provenance.frozen_config_sha256,
      evidenceSource: current.provenance.evidence_source,
      semanticDigest: decision.semantic_digest,
    },
    labels: { phases: PHASE_KO, domains: DOMAIN_KO, status: STATUS_KO, alert: ALERT_KO },
    phaseOrder: PHASES,
    current: {
      asOf: current.as_of_date,
      official: current.official_current_phase,
      raw: current.raw_current_phase,
      status: current.phase_status,
      evidenceQuality: current.evidence_quality,
      separation: current.phase_separation,
      level: current.activity_level,
      momentum: current.activity_momentum,
      transitionWatch: current.transition_watch,
      recessionAlert: current.recession_alert,
      breadth: current.breadth,
      concentration: current.concentration,
      domains: current.domain_evidence,
      freshness: current.domain_freshness,
    },
    recoveryLatencyWarning: current.recovery_latency_warning,
    limitations: current.known_limitations,
    interpretationBoundaries: current.interpretation_boundaries,
    maturity: maturity,
    varianceDistribution: variance,
    verification: verification,
    history,
    summary: {
      weeks: history.length,
      firstWeek: history[0].week,
      lastWeek: last.week,
      phaseWeeks: counts,
      withheldWeeks: withheld,
      preliminaryWeeks: preliminary,
      transitions,
    },
  };

  const body = JSON.stringify(payload);
  payload.checksum = createHash("sha256").update(body).digest("hex").slice(0, 16);

  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(OUT_FILE, JSON.stringify(payload), "utf8");

  const kb = (Buffer.byteLength(JSON.stringify(payload)) / 1024).toFixed(1);
  console.log(`경기국면 내보내기 완료 · ${OUT_FILE} · ${kb}KB`);
  console.log(`  기준일 ${payload.current.asOf} · 공식 ${payload.current.official} · 증거 품질 ${payload.current.evidenceQuality}`);
  console.log(`  주간 경로 ${payload.summary.weeks}주 (${payload.summary.firstWeek} ~ ${payload.summary.lastWeek}) · 전환 ${transitions.length}회`);
  console.log(`  모델 상태 ${payload.modelStatus} · 최종 검증 아님`);
  console.log(`  해석 경계 ${payload.interpretationBoundaries.length}건 · 화면 노출 ${surfaced.length}건`);
  console.log(`  변형 ${variant.id} · 전이 게이트 적용 ${variant.transition_gate_applied}`);
  console.log(`  성숙도 검증 국면 ${maturity.validated_phases.join(",")} · 분산 분포 ${variance.groups.length}묶음`);
}

main();
