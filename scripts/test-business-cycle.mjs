// scripts/test-business-cycle.mjs
// 경기국면 통합 검증. 두 가지를 본다.
//
//   1. 내보낸 데이터가 계약을 지키는가 — 국면 이름만 있고 한계가 빠진 파일을 만들지 않는다.
//   2. 두 화면이 그 계약을 실제로 화면에 옮기는가 — 홈 카드와 분석 탭.
//
// ── 왜 "한계가 실려 있는가"를 시험하는가 ──────────────────────────────────
// 이 모델은 `provisional`이고 증거 품질이 낮을 수 있으며 회복 인식이 늦을 수 있다.
// 국면 이름은 눈에 띄고 한계는 눈에 안 띄므로, 화면을 고치다 보면 한계가 먼저 빠진다.
// 그 회귀를 사람 눈이 아니라 시험이 잡게 한다.
//
// ── 왜 브라우저로 여는가 ──────────────────────────────────────────────────
// 카드가 그려지는지는 문자열을 보고 알 수 없다. 실제로 렌더해서 국면 이름과 증거
// 품질이 **함께** 보이는지 확인한다.

import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { installTestAuth, launchTestBrowser } from "./lib/test-auth.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 4336;
const DATA = resolve(ROOT, "data", "business-cycle", "us.json");

const results = [];
const record = (label, ok, detail) => {
  results.push({ label, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${ok ? "" : `  — ${detail}`}`);
};

const PHASES = ["recovery", "expansion", "slowdown", "contraction"];

// 화면에 절대 나오면 안 되는 어휘. 모델은 투자 판단을 만들지 않으므로 화면도 만들면 안 된다.
const FORBIDDEN = ["매수", "매도", "비중", "추천", "목표가", "포트폴리오 조정"];

// 면책 문구는 이 어휘들을 **쓰지 않겠다고 선언**하는 문장이라 그대로 검사하면 스스로
// 걸린다. 선언을 위반으로 세면 시험이 거꾸로 서므로, 검사 전에 선언만 걷어낸다.
const DISCLAIMERS = [
  /투자 판단[^.。\n]*지시를 (담지|제공하지|만들지) 않습니다?\.?/g,
  /투자 판단[^.。\n]*지시를 (담지|제공하지|만들지) 않는다\.?/g,
  /침체 예측[^.。\n]*투자 판단을 제공하지 않습니다\.?/g,
];

function withoutDisclaimers(text) {
  return DISCLAIMERS.reduce((acc, pattern) => acc.replace(pattern, " "), String(text));
}

function forbiddenIn(text) {
  const body = withoutDisclaimers(text);
  return FORBIDDEN.filter((word) => body.includes(word));
}

// ── 1. 데이터 계약 ────────────────────────────────────────────────────────
let payload = null;
try {
  if (!existsSync(DATA)) throw new Error(`${DATA}가 없습니다. npm run build:business-cycle 먼저 실행하세요.`);
  payload = JSON.parse(await readFile(DATA, "utf8"));

  record("현재국면이 정확히 하나다",
    PHASES.includes(payload.current.official) || payload.current.status === "withheld",
    JSON.stringify(payload.current.official));

  record("모델 상태가 provisional이다",
    payload.modelStatus === "provisional", String(payload.modelStatus));

  record("최종 검증이 아니라고 명시한다",
    payload.isFinalValidation === false, String(payload.isFinalValidation));

  record("증거 품질이 실려 있다",
    ["high", "low"].includes(payload.current.evidenceQuality), String(payload.current.evidenceQuality));

  record("한계가 비어 있지 않다",
    Array.isArray(payload.limitations) && payload.limitations.length > 0,
    `${(payload.limitations || []).length}건`);

  record("회복 인식 지연 경고가 실려 있다",
    Boolean(payload.recoveryLatencyWarning && payload.recoveryLatencyWarning.band),
    JSON.stringify(payload.recoveryLatencyWarning));

  record("주간 경로가 현재국면과 같은 주에서 끝난다",
    payload.history.length > 0 && payload.history[payload.history.length - 1].week === payload.current.asOf,
    `${payload.history.at(-1)?.week} vs ${payload.current.asOf}`);

  // 판정 보류 주에는 공식 국면을 내지 않는다. 빈 문자열로 때우지도 않는다.
  const leaking = payload.history.filter((row) => row.status === "withheld" && row.official !== null);
  record("판정 보류 주는 공식 국면을 내지 않는다", leaking.length === 0, `${leaking.length}주`);

  // 보류 주에도 원시 측정은 남는다 — 상태 판정이 경제 측정 자체를 바꾸지는 않는다.
  const withheld = payload.history.filter((row) => row.status === "withheld");
  record("판정 보류 주에도 측정값은 남아 있다",
    withheld.length > 0 && withheld.every((row) => typeof row.level === "number"),
    `${withheld.length}주`);

  // 면책 문구는 빼고 본다. 그리고 면책 문구가 **있는지**는 따로 확인한다 —
  // 두 시험을 하나로 합치면 면책이 사라져도 통과한다.
  record("투자 판단 어휘가 데이터에 없다",
    forbiddenIn(JSON.stringify(payload)).length === 0,
    forbiddenIn(JSON.stringify(payload)).join(","));

  record("데이터에 투자 판단 면책이 실려 있다",
    payload.limitations.some((item) => item.includes("투자 판단")),
    payload.limitations.join(" / ").slice(0, 60));

  // ── 해석 경계 ────────────────────────────────────────────────────────────
  // 한계와 같은 방식으로 강제한다. 비어 있으면 통과시키지 않는다 — 필드만 있고 내용이
  // 없는 상태가 가장 조용히 지나간다.
  const boundaries = payload.interpretationBoundaries || [];
  record("해석 경계가 비어 있지 않다",
    Array.isArray(boundaries) && boundaries.length >= 2, `${boundaries.length}건`);

  record("해석 경계마다 제목과 본문이 비어 있지 않다",
    boundaries.length > 0 && boundaries.every((entry) =>
      entry && entry.id && typeof entry.title === "string" && entry.title.trim().length > 0
      && typeof entry.text === "string" && entry.text.trim().length > 0),
    JSON.stringify(boundaries.map((entry) => entry && entry.id)));

  const shown = boundaries.filter((entry) => entry.surface === "app_phase_reading");
  record("화면에 띄울 해석 경계가 정해져 있다", shown.length >= 1, `${shown.length}건`);

  // 같은 두 문장이 평평한 한계 목록에도 있어야 한다. 목록에 걸린 기존 검사가 곧
  // 이 항목들의 안전망이 되기 때문이다.
  record("해석 경계가 한계 목록에도 실려 있다",
    boundaries.every((entry) => payload.limitations.some((item) => item === entry.text)),
    boundaries.filter((entry) => !payload.limitations.includes(entry.text)).length + "건 누락");

  record("폭에 집중도 부분 화면 설명이 실려 있다",
    typeof payload.current.breadth?.partial_concentration_screen === "string"
      && payload.current.breadth.partial_concentration_screen.trim().length > 0,
    String(payload.current.breadth?.partial_concentration_screen || "").slice(0, 40));
} catch (error) {
  record("데이터 계약 검증", false, error.message);
}

// ── 2. 화면 ───────────────────────────────────────────────────────────────
const server = spawn("python", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1", "--directory", ROOT], {
  stdio: "ignore",
});
await new Promise((done) => setTimeout(done, 900));

let browser;
try {
  browser = await launchTestBrowser(chromium);
  const context = await browser.newContext();
  const page = await context.newPage();
  await installTestAuth(page);

  // 분석 탭 — 모델 상세.
  await page.goto(`http://127.0.0.1:${PORT}/analysis.html`, { waitUntil: "networkidle" });
  const analysis = await page.innerText("body");

  const phaseKo = { recovery: "회복기", expansion: "확장기", slowdown: "후퇴기", contraction: "침체기" };
  const expected = phaseKo[payload?.current?.official];

  record("분석 탭이 현재 국면을 보여준다",
    Boolean(expected) && analysis.includes(expected), expected || "(국면 없음)");

  record("분석 탭이 증거 품질을 함께 보여준다",
    analysis.includes("증거 품질"), "");

  record("분석 탭이 회복 인식 지연을 공시한다",
    analysis.includes("회복 인식 지연"), "");

  record("분석 탭이 한계를 나열한다",
    payload.limitations.every((item) => analysis.includes(item.slice(0, 12))),
    payload.limitations.filter((item) => !analysis.includes(item.slice(0, 12))).length + "건 누락");

  record("분석 탭이 최종 검증이 아님을 밝힌다",
    analysis.includes("최종 검증"), "");

  // 경계 B는 한계 카드가 아니라 **국면 판독 옆**에 있어야 한다. 국면 이름만 읽고 나가는
  // 사람에게는 카드 아래로 내려간 문장이 없는 것과 같다.
  const surfaced = (payload.interpretationBoundaries || [])
    .filter((entry) => entry.surface === "app_phase_reading");
  record("분석 탭이 해석 경계를 국면 판독 옆에 보여준다",
    surfaced.length > 0 && surfaced.every((entry) => analysis.includes(entry.title)),
    surfaced.map((entry) => entry.title).join(" / "));

  const nowIndex = analysis.indexOf("현재 국면");
  const limitsIndex = analysis.indexOf("이 모델의 한계");
  const boundaryIndex = surfaced.length ? analysis.indexOf(surfaced[0].title) : -1;
  record("해석 경계가 한계 카드보다 위에 있다",
    boundaryIndex > nowIndex && (limitsIndex === -1 || boundaryIndex < limitsIndex),
    `현재국면 ${nowIndex} · 경계 ${boundaryIndex} · 한계 ${limitsIndex}`);

  record("분석 탭이 집중도 부분 화면을 밝힌다",
    analysis.includes("주의 표시"), "");

  // 데이터 필드 이름이 화면에 새면 안 된다. 모델 문장을 그대로 옮기다 보면 가장 쉽게
  // 새는 자리가 여기다.
  record("분석 탭에 데이터 필드 이름이 노출되지 않는다",
    !analysis.includes("confirming_coincident_domains"), "");

  record("분석 탭이 예측·투자 판단이 아님을 밝힌다",
    analysis.includes("투자 판단"), "");

  record("분석 탭에 투자 판단 어휘가 없다",
    forbiddenIn(analysis).length === 0, forbiddenIn(analysis).join(","));

  // 국면은 정해진 순서로 돌지 않는다는 사실을 화면이 말해야 한다 — 안 그러면
  // 후퇴기에서 확장기로 간 경로를 보고 사용자가 오류로 읽는다.
  record("분석 탭이 국면 순서 강제가 없음을 밝힌다",
    analysis.includes("정해진 순서로 돌지 않습니다"), "");

  // 홈 카드 — 자산이 있어야 중간 3열이 그려지므로 세션을 심는다.
  await page.goto(`http://127.0.0.1:${PORT}/home.html`, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    localStorage.setItem("assetInput.session", JSON.stringify({
      schema: 7,
      assets: [{ id: "t1", group: "cash", label: "현금", name: "현금", currency: "KRW", amount: 1000000, quantity: null }],
    }));
  });
  await page.goto(`http://127.0.0.1:${PORT}/home.html`, { waitUntil: "networkidle" });

  // 카드 함수를 직접 불러 확인한다. 세션 복원 경로는 이 시험의 대상이 아니고,
  // 여기서 보고 싶은 것은 **카드가 무엇을 그리는가**뿐이다.
  const card = await page.evaluate(() => (typeof cycleCard === "function" ? cycleCard() : null));
  const cardText = card ? card.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim() : "";

  record("홈에 경기국면 카드가 있다", Boolean(card) && cardText.includes("경기국면"), cardText.slice(0, 80));
  record("홈 카드가 현재 국면을 보여준다", Boolean(expected) && cardText.includes(expected), expected || "");
  record("홈 카드가 증거 품질을 함께 보여준다", cardText.includes("증거 품질"), "");
  record("홈 카드가 예측이 아님을 밝힌다", cardText.includes("예측이 아니"), "");
  record("홈 카드가 잠정 상태를 밝힌다", cardText.includes("잠정"), "");
  record("홈 카드에 투자 판단 어휘가 없다",
    forbiddenIn(cardText).length === 0, forbiddenIn(cardText).join(","));

  // 홈에서 OECD 지표 카드는 빠졌지만 경제지표 탭은 그대로 있어야 한다.
  // 내비게이션은 문자열이 아니라 DOM으로 본다. 본문을 정규식으로 훑으면 바로 옆
  // 항목("목표 관리 · 준비 중")을 물어 엉뚱하게 실패한다 — 실제로 그렇게 깨졌다.
  const nav = await page.evaluate(() => {
    const items = [...document.querySelectorAll("[data-nav-id], .nav-item")];
    return items.map((node) => ({
      text: (node.textContent || "").replace(/\s+/g, " ").trim(),
      href: node.getAttribute("href") || "",
      soon: /준비 중/.test(node.textContent || ""),
    }));
  });
  const findNav = (label) => nav.find((item) => item.text.includes(label));
  record("내비게이션에 경제지표 탭이 남아 있다",
    Boolean(findNav("경제지표")?.href.includes("indicators.html")),
    JSON.stringify(findNav("경제지표")));
  record("내비게이션의 분석 탭이 열려 있다",
    Boolean(findNav("분석")?.href.includes("analysis.html")) && findNav("분석")?.soon === false,
    JSON.stringify(findNav("분석")));

  await page.goto(`http://127.0.0.1:${PORT}/indicators.html`, { waitUntil: "networkidle" });
  const indicators = await page.innerText("body");
  record("경제지표 화면이 그대로 뜬다", indicators.includes("경제지표"), indicators.slice(0, 60));

  await context.close();
} catch (error) {
  record("화면 검증", false, error.message);
} finally {
  if (browser) await browser.close();
  server.kill();
  await once(server, "close").catch(() => {});
}

const failed = results.filter((result) => !result.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exitCode = 1;
