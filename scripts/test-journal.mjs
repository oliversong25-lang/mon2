// 투자 원칙과 의사결정 기록의 검증. Supabase 계정 없이 확인할 수 있는 것만 여기서 본다.
// 계정 격리는 `test:journal-rls`가 실제 계정 두 개로 따로 확인한다.
//
// ── 이 시험이 지키는 두 가지 ───────────────────────────────────────────────
// 1. **원칙 화면에 선택지도 예시 답도 없다.** 보기를 주면 그 순간 우리의 관점이 사용자의
//    원칙에 들어간다. 사람 눈으로 지키면 화면을 고치다 슬그머니 들어오므로 시험이 센다.
// 2. **타자로 쳐야 하는 칸의 수가 늘지 않는다.** 마찰이 의사결정 기록이 실패하는 유일한
//    이유이고, 늘어난 칸은 눈에 잘 띄지 않는다.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { installTestAuth, launchTestBrowser } from "./lib/test-auth.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 4338;

const results = [];
const record = (label, ok, detail) => {
  results.push({ label, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${ok ? "" : `  — ${detail}`}`);
};

// ── 1. 스키마 계약 ─────────────────────────────────────────────────────────
try {
  const sql = await readFile(resolve(ROOT, "supabase", "schema.sql"), "utf8");
  const tables = ["user_investment_philosophy", "user_philosophy_revisions", "user_decision_records"];

  for (const table of tables) {
    record(`${table}에 RLS가 켜져 있다`,
      sql.includes(`alter table public.${table} enable row level security`), "");
    record(`${table}에 RLS가 강제돼 있다`,
      sql.includes(`alter table public.${table} force row level security`), "");
    record(`${table}에서 anon이 회수돼 있다`,
      sql.includes(`revoke all on table public.${table} from anon`), "");
  }

  // 정책이 auth.uid()로 잠겨 있어야 한다. 정책이 있다는 사실만으로는 격리가 아니다.
  const policyBlocks = sql.match(/create policy[\s\S]*?;/g) || [];
  const journalPolicies = policyBlocks.filter((block) => tables.some((t) => block.includes(t)));
  record("기록 정책이 모두 auth.uid()로 잠겨 있다",
    journalPolicies.length > 0 && journalPolicies.every((block) => block.includes("auth.uid()")),
    `${journalPolicies.length}개 중 ${journalPolicies.filter((b) => !b.includes("auth.uid()")).length}개 누락`);

  // 이력은 덧붙이기만 한다. update/delete 권한을 주면 나중에 손볼 수 있고, 그러면 이력이 아니다.
  const revisionGrant = (sql.match(/grant [^;]*on table public\.user_philosophy_revisions to authenticated;/) || [""])[0];
  record("원칙 이력에 수정·삭제 권한이 없다",
    revisionGrant.includes("select") && revisionGrant.includes("insert")
      && !revisionGrant.includes("update") && !revisionGrant.includes("delete"),
    revisionGrant);

  // 보류와 안 하기로 함이 1급이어야 한다. 매수·매도만 받으면 거래 기록부가 된다.
  record("action이 네 가지를 모두 받는다",
    ["'buy'", "'sell'", "'hold'", "'skip'"].every((value) => sql.includes(value)), "");

  record("보유 자산 없이도 기록할 수 있다",
    /holding_id text,/.test(sql) && !/holding_id text not null/.test(sql), "holding_id가 not null입니다");

  record("반증 조건의 두 종류가 구분돼 저장된다",
    sql.includes("falsification_kind") && sql.includes("'machine'") && sql.includes("'human'")
      && sql.includes("falsification_rule jsonb"), "");
} catch (error) {
  record("스키마 계약 검증", false, error.message);
}

// ── 2. 화면 ────────────────────────────────────────────────────────────────
const server = spawn("python", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1", "--directory", ROOT], { stdio: "ignore" });
await new Promise((done) => setTimeout(done, 900));

let browser;
try {
  browser = await launchTestBrowser(chromium);
  const context = await browser.newContext();
  const page = await context.newPage();
  await installTestAuth(page);

  // ── 원칙 화면 ────────────────────────────────────────────────────────────
  await page.goto(`http://127.0.0.1:${PORT}/philosophy.html`, { waitUntil: "networkidle" });
  await page.waitForTimeout(700);

  const questions = await page.locator("textarea[data-q]").count();
  record("원칙 질문이 여섯 가지를 덮는다", questions >= 6, `${questions}개`);

  const asked = await page.innerText("body");
  for (const topic of [
    ["사기로", "무엇을 사는가"],
    ["팔기로", "무엇을 파는가"],
    ["얼마까지", "한 종목 비중"],
    ["얼마나 들고", "보유 기간"],
    ["손실", "감내 손실"],
    ["이해하지 못하는", "모르는 것"],
  ]) {
    record(`원칙 화면이 ${topic[1]}를 묻는다`, asked.includes(topic[0]), topic[0]);
  }

  // **선택지 금지.** select·radio·checkbox·datalist가 하나라도 있으면 우리가 답을 좁힌 것이다.
  const optionish = await page.evaluate(() =>
    document.querySelectorAll("select, input[type=radio], input[type=checkbox], datalist, option").length);
  record("원칙 화면에 선택지가 없다", optionish === 0, `${optionish}개 발견`);

  // **예시 답 금지.** placeholder도 value도 비어 있어야 한다 — 흐린 글씨의 문장도 본보기로 읽힌다.
  const seeded = await page.evaluate(() =>
    Array.from(document.querySelectorAll("textarea, input[type=text]"))
      .filter((node) => (node.placeholder || "").trim() || (node.value || "").trim()).length);
  record("원칙 화면에 예시 답도 기본값도 없다", seeded === 0, `${seeded}개 발견`);

  // 질문은 "무엇을 하십니까"여야 하고 "무엇을 해야 합니다"가 아니어야 한다.
  const prescriptive = await page.evaluate(() =>
    Array.from(document.querySelectorAll("label"))
      .map((node) => node.textContent || "")
      .filter((text) => /해야|하세요|권장|추천|바람직|좋습니다/.test(text)));
  record("원칙 질문이 규범을 말하지 않는다", prescriptive.length === 0, prescriptive.join(" / "));

  record("원칙 화면에 바뀐 기록 자리가 있다", asked.includes("바뀐 기록"), "");

  // 불러오기가 실패했는데 저장이 열려 있으면, 빈 화면이 저장돼 있던 원칙을 덮어쓴다.
  // 시험 환경은 실제 Supabase 세션이 아니라 불러오기가 실패하므로 그 상태를 그대로 본다.
  const saveState = await page.evaluate(() => {
    const button = document.getElementById("save");
    const banner = document.body.innerText.includes("불러오지 못했어요");
    return { disabled: button ? button.disabled : null, banner };
  });
  record("불러오기가 실패하면 저장이 잠긴다",
    !saveState.banner || saveState.disabled === true, JSON.stringify(saveState));

  // ── 의사결정 기록 화면 ───────────────────────────────────────────────────
  await page.goto(`http://127.0.0.1:${PORT}/decisions.html`, { waitUntil: "networkidle" });
  await page.waitForTimeout(900);
  const journal = await page.innerText("body");

  for (const label of ["샀다", "팔았다", "그대로 뒀다", "안 하기로 했다"]) {
    record(`결정 종류에 "${label}"가 있다`, journal.includes(label), "");
  }

  // 타자로 쳐야 하는 칸의 수. 늘어나면 기록이 안 쌓인다.
  const typed = await page.locator("[data-f]").count();
  record("사용자가 타자로 치는 칸이 네 개다", typed === 4, `${typed}개`);
  record("화면이 그 수를 밝힌다", journal.includes("4개"), "");

  const autofilled = await page.evaluate(() => window.DecisionContext.AUTOFILLED_FIELDS.length);
  record("앱이 채우는 칸이 사용자가 치는 칸보다 많다", autofilled > 4, `${autofilled}개`);

  // 자동 채움이 실제로 값을 채웠는지. 목록만 있고 값이 비면 자동 채움이 아니다.
  const filled = await page.evaluate(async () => {
    const ctx = await window.DecisionContext.build({ holding: null, totalKrw: 0 });
    return {
      phase: ctx.businessCycle && ctx.businessCycle.phase,
      fx: ctx.market && ctx.market.fx && ctx.market.fx.usdkrw,
      rate: ctx.market && ctx.market.rates && ctx.market.rates.us10y && ctx.market.rates.us10y.value,
      equity: ctx.market && ctx.market.koreanEquityIndex && ctx.market.koreanEquityIndex.value,
      capturedAt: ctx.capturedAt,
    };
  });
  record("경기국면이 자동으로 채워진다", Boolean(filled.phase), JSON.stringify(filled));
  record("환율이 자동으로 채워진다", Number.isFinite(filled.fx), String(filled.fx));
  record("금리가 자동으로 채워진다", Number.isFinite(filled.rate), String(filled.rate));
  record("주가지수가 자동으로 채워진다", Number.isFinite(filled.equity), String(filled.equity));
  record("날짜가 자동으로 채워진다", Boolean(filled.capturedAt), String(filled.capturedAt));

  record("국면이 신호가 아니라 맥락이라고 밝힌다",
    journal.includes("맥락") && journal.includes("신호가 아닙니다"), "");

  record("보유와 무관한 결정도 고를 수 있다", journal.includes("보유와 무관"), "");

  // 반증 조건 두 종류가 화면에서 갈린다.
  record("반증 조건을 두 종류로 나눈다",
    journal.includes("사람이 판단할 조건") && journal.includes("숫자로 확인되는 조건"), "");

  // 결과 채점이 아니라는 것이 화면에 있어야 한다.
  record("결과로 채점하지 않는다고 밝힌다", journal.includes("채점하지 않습니다"), "");

  // 내비게이션에서 원칙이 기록보다 위에 있어야 한다 — 원칙이 먼저다.
  const order = await page.evaluate(() => {
    const items = Array.from(document.querySelectorAll(".nav-item"));
    return {
      philosophy: items.findIndex((node) => (node.textContent || "").includes("투자 원칙")),
      journal: items.findIndex((node) => (node.textContent || "").includes("의사결정 기록")),
    };
  });
  record("내비게이션에서 원칙이 기록보다 앞에 있다",
    order.philosophy >= 0 && order.journal > order.philosophy, JSON.stringify(order));
} catch (error) {
  record("화면 검증", false, error.message);
} finally {
  if (browser) await browser.close();
  server.kill();
}

const failed = results.filter((row) => !row.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
