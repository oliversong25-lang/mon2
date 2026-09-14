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
import { installJournalMock } from "./lib/test-journal-mock.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 4338;

const results = [];
const record = (label, ok, detail) => {
  results.push({ label, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${ok ? "" : `  — ${detail}`}`);
};

// 셸 내용만 바꾸고 URL을 그대로 두면 이미 방문한 사용자는 예전 내비게이션을 계속 본다.
const SHELL_VERSION = "20260901-1";
for (const pageName of ["home.html", "assets.html", "settings.html", "analysis.html", "indicators.html", "philosophy.html", "decisions.html"]) {
  const source = await readFile(resolve(ROOT, pageName), "utf8");
  record(`${pageName}이 최신 공통 내비게이션을 읽는다`,
    source.includes(`lib/shell.js?v=${SHELL_VERSION}`), "셸 버전이 다름");
}

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
  const revisionRevoke = "revoke all on table public.user_philosophy_revisions from authenticated;";
  record("원칙 이력에 수정·삭제 권한이 없다",
    sql.includes(revisionRevoke) && revisionGrant.includes("select") && revisionGrant.includes("insert")
      && !revisionGrant.includes("update") && !revisionGrant.includes("delete"),
    `${sql.includes(revisionRevoke) ? "revoke 있음" : "revoke 없음"} / ${revisionGrant}`);

  // 결정 셋. **보류가 안 하기로 함과 합쳐지면 안 된다** — 앞은 아직 결정에 이르지 못한
  // 것이고 뒤는 결정에 이른 것이라, 합치면 나중에 셀 때 분모가 틀린다.
  record("결정이 셋으로 갈린다",
    ["'executed'", "'not_executed'", "'deferred'"].every((value) => sql.includes(value)), "");

  record("이유가 목록으로 저장된다",
    /reasons_for jsonb/.test(sql) && /reasons_against jsonb/.test(sql), "");

  record("행동 한 줄이 따로 저장된다", /action_statement text/.test(sql), "");

  record("기대가 남아 있다", /expectation text/.test(sql), "");

  // 보류는 열린 고리다. 다시 돌아올 길을 자료가 받쳐야 한다.
  record("보류가 열린 채로 남는 자리가 있다",
    /resolved_at timestamptz/.test(sql) && /superseded_by uuid/.test(sql), "");
  record("열린 보류를 찾는 인덱스가 있다",
    /where decision = 'deferred' and resolved_at is null/.test(sql), "");

  record("보유 자산 없이도 기록할 수 있다",
    /holding_id text,/.test(sql) && !/holding_id text not null/.test(sql), "holding_id가 not null입니다");

  // 트랙 29의 칸들은 사라져야 한다. 남아 있으면 두 형태가 섞인다.
  record("원칙 답이 한 덩어리가 아니라 따로 담기는 칸이 있다 (트랙 40)",
    /principle_answers jsonb/.test(sql) && /add column if not exists principle_answers jsonb/.test(sql), "");
  record("옛 자유 문장 칸이 정리된다",
    /drop column if exists reasoning/.test(sql) && /drop column if exists uncertainty/.test(sql)
      && /drop column if exists falsification_text/.test(sql), "");
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

  // 트랙 40: 원칙은 고정 질문에 대한 답이 아니라 **사용자가 한 줄씩 적는 목록**이다.
  record("원칙 화면이 한 줄 입력과 추가 버튼으로 이루어진다",
    (await page.locator("#new-text").count()) === 1 && (await page.locator("#add").count()) === 1, "");
  const kindLabels = await page.evaluate(() => Array.from(document.querySelectorAll("button[data-kind]")).map((node) => node.textContent.trim()));
  record("항목 종류가 원칙·검토 질문 둘이다", JSON.stringify(kindLabels) === JSON.stringify(["원칙", "검토 질문"]), JSON.stringify(kindLabels));
  const asked = await page.innerText("body");
  record("핵심이 무엇을 바꾸는지 표시하는 자리에서 말한다",
    asked.includes("핵심") && asked.includes("기록을 마칠 수 있습니다"), "");
  record("항목을 지워도 과거 기록이 남는다고 미리 말한다",
    asked.includes("항목을 지워도 그 항목에 답한 과거 기록은 지워지지 않습니다"), "");
  // 예전의 고정 질문 여섯이 다시 등장하면 우리 질문이 사용자의 원칙 틀이 된다.
  const legacyAsked = await page.evaluate(() => Array.from(document.querySelectorAll("label"))
    .map((node) => node.textContent || "").filter((text) => /사기로 합니까|팔기로 합니까|얼마까지 넣습니까/.test(text)).length);
  record("원칙 화면이 고정 질문을 묻지 않는다", legacyAsked === 0, `${legacyAsked}개`);

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

  for (const label of ["실행함", "실행 안 함", "판단 보류"]) {
    record(`결정에 "${label}"이 있다`, journal.includes(label), "");
  }
  record("검토 중인 행동을 위에 적는다", journal.includes("검토 중인 행동"), "");
  record("두 목록이 다 있다",
    journal.includes("해야 할 이유") && journal.includes("하지 말아야 할 이유"), "");
  record("보유 자산을 고르는 영역이 없다",
    await page.locator("#holding").count() === 0 && !journal.includes("어느 자산에 대한 결정입니까"),
    "보유 자산 선택 영역이 남아 있음");

  // **두 목록은 결정보다 먼저 쓸 수 있어야 한다.** 결정 뒤로 감추면 결정을 먼저 고르고
  // 이유를 나중에 맞추게 된다 — 이 구조가 막으려던 것이 정확히 그것이다.
  const listsBeforeDecision = await page.evaluate(() => ({
    lists: document.querySelectorAll("[data-item-text]").length,
    decisionChosen: Array.from(document.querySelectorAll("[data-decision]"))
      .some((node) => node.getAttribute("aria-pressed") === "true"),
  }));
  record("결정을 고르기 전에도 목록을 쓸 수 있다",
    listsBeforeDecision.lists >= 2 && !listsBeforeDecision.decisionChosen,
    JSON.stringify(listsBeforeDecision));

  // 고정 칸의 수. 이유 줄은 늘어나는 것이 정상이므로 따로 센다.
  const fixed = await page.locator("[data-draft]").count();
  record("고정으로 치는 칸이 둘이다", fixed === 2, `${fixed}개`);

  const autofilled = await page.evaluate(() => window.DecisionContext.AUTOFILLED_FIELDS.length);
  record("앱이 채우는 칸이 고정 칸보다 많다", autofilled > fixed, `${autofilled}개`);

  // **숫자는 보여주되 해석하지 않는다.**
  record("두 목록의 개수를 보여준다", /\d+ 대 \d+/.test(journal), "");
  const judged = ["부족", "충분", "권장", "주의", "경고", "위험합니다", "바람직"]
    .filter((word) => journal.includes(word));
  record("개수에 평가를 붙이지 않는다", judged.length === 0, judged.join(" / "));

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

  // 반증 조건은 **반대 목록에서 나온다.** 따로 칸을 두지 않았으므로, 표시할 자리가
  // 반대 항목에 붙어 있는지를 본다. 두 종류의 구분은 표시한 뒤에 드러나며 왕복 검사가 본다.
  record("반증 조건을 반대 목록에서 표시한다",
    journal.includes("이게 실제로 일어나면 내 판단이 틀린 것"), "");
  const markSpots = await page.evaluate(() => ({
    against: document.querySelectorAll("[data-falsifies]").length,
    forSide: document.querySelectorAll('.cols .col:nth-child(1) [data-falsifies]').length,
  }));
  record("반증 표시가 반대 목록에만 붙는다",
    markSpots.against >= 1 && markSpots.forSide === 0, JSON.stringify(markSpots));
  record("반증 전용 칸이 따로 있지 않다",
    !journal.includes("무엇을 보면 이 판단이 틀린 것입니까"), "");

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

  // ── 3. 왕복 ──────────────────────────────────────────────────────────────
  // 화면이 그려지는 것과 저장한 것이 다시 읽히는 것은 다른 이야기다. 여기서부터는
  // 기록 테이블을 목으로 받아 저장 → 다시 읽기를 실제로 통과시킨다.
  const round = await context.newPage();
  await installTestAuth(round);
  await installJournalMock(round);

  await round.goto(`http://127.0.0.1:${PORT}/philosophy.html`, { waitUntil: "networkidle" });
  await round.waitForTimeout(700);

  record("목을 붙이면 원칙 화면이 오류 없이 뜬다",
    !(await round.innerText("body")).includes("불러오지 못했어요"), "");

  const P1 = "3년 이상 보유한다";
  const P2 = "이해하지 못하는 것에 투자하지 않는다";
  const P3 = "지금 사야 할 이유가 가격 말고 있는가";
  const addItem = async (text, kind) => {
    if (kind) await round.click(`button[data-kind="${kind}"]`);
    await round.locator("#new-text").click();
    await round.keyboard.type(text, { delay: 5 });
    await round.click("#add");
    await round.waitForTimeout(150);
  };
  const itemTexts = () => round.evaluate(() => Array.from(document.querySelectorAll("li.pitem .pitem-text"))
    .map((node) => (node.firstChild && node.firstChild.textContent || "").trim()));

  record("처음에는 빈 목록이다 (예시 항목을 넣어 두지 않는다)",
    (await round.locator("li.pitem").count()) === 0, `${await round.locator("li.pitem").count()}개`);

  // 실제 클릭으로 세 개를 더하고, 하나를 핵심으로, 하나를 지운다.
  await addItem(P1, "principle");
  await addItem(P2);
  await addItem(P3, "question");
  record("세 항목이 한 줄씩 더해진다", JSON.stringify(await itemTexts()) === JSON.stringify([P1, P2, P3]), JSON.stringify(await itemTexts()));

  await round.locator("li.pitem", { hasText: P1 }).locator("button[data-core]").click();
  await round.waitForTimeout(150);
  record("핵심 표시가 켜진다",
    (await round.locator("li.pitem", { hasText: P1 }).locator("button[data-core]").getAttribute("aria-pressed")) === "true", "");

  await round.locator("li.pitem", { hasText: P2 }).locator("button[data-delete]").click();
  await round.waitForTimeout(150);
  const afterDelete = await round.innerText("body");
  record("지우면 과거 기록이 어떻게 되는지 그 자리에서 말한다",
    afterDelete.includes("목록에서 뺐습니다") && afterDelete.includes("이 항목에 답한 기록은 없었습니다"), "");
  record("지운 항목이 목록에서 빠진다", JSON.stringify(await itemTexts()) === JSON.stringify([P1, P3]), JSON.stringify(await itemTexts()));

  // 저장 전 새로 고침: 저장하지 않은 목록이 이 기기에 남아 있어야 한다.
  await round.locator("#new-text").click();
  await round.keyboard.type("쓰다 만 줄", { delay: 5 });
  await round.waitForTimeout(200);
  await round.reload({ waitUntil: "networkidle" });
  await round.waitForTimeout(700);
  record("저장 전에 새로 고쳐도 목록이 남는다 (원칙 화면)",
    JSON.stringify(await itemTexts()) === JSON.stringify([P1, P3]) && (await round.innerText("body")).includes("되살렸습니다"),
    JSON.stringify(await itemTexts()));
  record("저장 전에 새로 고쳐도 쓰다 만 줄이 남는다",
    (await round.inputValue("#new-text")) === "쓰다 만 줄", await round.inputValue("#new-text"));
  await round.fill("#new-text", "");

  await round.fill("#reason", "처음 적음");
  await round.click("#save");
  await round.waitForTimeout(600);

  const afterSave = await round.innerText("body");
  record("저장한 원칙이 바뀐 기록에 남는다", afterSave.includes("처음 적음"), "");
  record("저장한 항목이 이력 본문에 남는다", afterSave.includes(P3), "");
  record("저장 뒤 횟수가 표시된다", afterSave.includes("1번 저장"), "");
  const savedDoc = await round.evaluate(() => window.__journalStore.user_investment_philosophy[0].answers);
  record("원칙이 v2 목록 모양으로 저장된다",
    savedDoc.version === 2 && savedDoc.items.length === 2 && savedDoc.items[0].core === true && savedDoc.items[1].kind === "question",
    JSON.stringify(savedDoc));
  record("사용자가 쓴 문구를 그대로 저장한다 (고쳐 쓰지 않는다)",
    savedDoc.items[0].text === P1 && savedDoc.items[1].text === P3, JSON.stringify(savedDoc.items.map((item) => item.text)));

  await round.reload({ waitUntil: "networkidle" });
  await round.waitForTimeout(700);
  record("새로 고쳐도 저장한 원칙이 다시 읽힌다",
    JSON.stringify(await itemTexts()) === JSON.stringify([P1, P3]), JSON.stringify(await itemTexts()));

  // ── 결정 기록 왕복 ───────────────────────────────────────────────────────
  // **쓴 목록이 같은 목록으로 돌아와야 한다.** 트랙 29가 잡은 결함이 정확히 이 부류였다 —
  // 화면은 멀쩡한데 저장되는 값이 달랐다.
  await round.goto(`http://127.0.0.1:${PORT}/decisions.html`, { waitUntil: "networkidle" });
  await round.waitForTimeout(900);

  // 빈 채로 저장하면 무엇이 비었는지 사실만 말해야 한다.
  await round.click("#save");
  await round.waitForTimeout(300);
  record("행동이 비면 그대로 말한다",
    (await round.innerText("body")).includes("검토 중인 행동이 비어 있습니다"), "");

  // 1단계. 원칙 화면에 남은 두 항목이 그대로 나와야 한다.
  const stageOne = await round.evaluate(() => Array.from(document.querySelectorAll("li[data-principle] label"))
    .map((node) => node.textContent.replace(/\s+/g, " ").trim()));
  record("1단계에 원칙 화면에 남은 두 항목이 나온다", stageOne.length === 2, JSON.stringify(stageOne));
  record("1단계가 항목을 질문으로 고쳐 쓰지 않고 그대로 보여준다",
    stageOne[0].endsWith(P1) && stageOne[1].endsWith(P3), JSON.stringify(stageOne));
  record("1단계 제목이 무엇을 묻는지 드러낸다", (await round.innerText("body")).includes("투자 전 검토할 사항"), "");
  record("핵심 항목이 핵심으로 표시된다", stageOne[0].includes("★ 핵심") && !stageOne[1].includes("★ 핵심"), JSON.stringify(stageOne));
  record("2단계가 정보에 기반한 이유라고 적혀 있다", (await round.innerText("body")).includes("2단계 · 정보에 기반하여"), "");
  const coreBox = () => round.locator("li[data-principle]", { hasText: P1 }).locator("textarea");
  const restBox = () => round.locator("li[data-principle]", { hasText: P3 }).locator("textarea");

  await round.fill("#statement", "삼성전자 매수");
  await round.fill('.cols .col:nth-child(1) [data-item-text]', "값이 내렸고 사업은 그대로다");
  await round.click("#save");
  await round.waitForTimeout(300);
  record("핵심 항목이 비면 그대로 말하고 저장하지 않는다",
    (await round.innerText("body")).includes("핵심 항목에 답이 비어 있습니다") &&
    (await round.evaluate(() => window.__journalStore.user_decision_records.length)) === 0, "");

  await coreBox().click();
  await round.keyboard.type("4년 들고 갈 생각이다", { delay: 5 });
  // 결정 기록 도중 새로 고침: 쓰던 것이 사라지면 안 된다(예전에는 통째로 사라졌다).
  await round.waitForTimeout(500);
  await round.reload({ waitUntil: "networkidle" });
  await round.waitForTimeout(900);
  record("쓰는 도중 새로 고쳐도 행동·이유·1단계 답이 남는다",
    (await round.inputValue("#statement")) === "삼성전자 매수" &&
    (await coreBox().inputValue()) === "4년 들고 갈 생각이다" &&
    (await round.locator('.cols .col:nth-child(1) [data-item-text]').first().inputValue()) === "값이 내렸고 사업은 그대로다",
    `${await round.inputValue("#statement")} / ${await coreBox().inputValue()}`);

  await round.click("#save");
  await round.waitForTimeout(300);
  record("반대 이유가 비면 그대로 말한다",
    (await round.innerText("body")).includes("하지 말아야 할 이유가 비어 있습니다"), "");

  // 반대 목록을 채운다. 두 번째 줄을 더해 **낱개로 저장되는지**를 본다.
  const againstBoxes = () => round.locator('.cols .col:nth-child(2) [data-item-text]');
  await againstBoxes().nth(0).fill("경쟁사가 같은 제품을 준비 중이다");
  await round.click('[data-add="against"]');
  await round.waitForTimeout(250);
  await againstBoxes().nth(1).fill("환율이 더 오르면 원가가 오른다");
  await round.click('[data-add="for"]');
  await round.waitForTimeout(250);
  await round.locator('.cols .col:nth-child(1) [data-item-text]').nth(1).fill("배당이 늘고 있다");
  await round.waitForTimeout(200);

  record("개수 표시가 실제 줄 수를 따라간다",
    (await round.innerText("body")).includes("2 대 2"), await round.innerText(".tally"));

  // 반증은 반대 목록의 항목에 표시해서 만든다.
  await round.locator("[data-falsifies]").nth(0).check();
  await round.waitForTimeout(250);
  await round.locator('[data-kind="machine"]').first().click();
  await round.waitForTimeout(250);
  await round.locator('[data-rule="value"]').first().fill("52000");
  await round.fill("#expectation", "2년 안에 이익이 회복되기를 기대");
  await round.click('[data-decision="executed"]');
  await round.waitForTimeout(200);
  await round.click("#save");
  await round.waitForTimeout(800);

  const saved = await round.evaluate(() => window.__journalStore.user_decision_records[0]);
  record("행동 한 줄이 그대로 저장된다", saved.action_statement === "삼성전자 매수", saved.action_statement);
  const pa = saved.principle_answers || [];
  const byText = (text) => pa.find((a) => a.text === text);
  record("원칙 답이 원칙마다 따로 저장된다", pa.length === 2 && pa.every((a) => a.principleId), JSON.stringify(pa));
  record("핵심 항목의 답이 그대로 저장된다", byText(P1) && byText(P1).answer === "4년 들고 갈 생각이다" && byText(P1).skipped === false,
    JSON.stringify(byText(P1)));
  record("핵심이 아닌 항목은 비워 둬도 저장된다", Boolean(byText(P3)), "");
  record("손대지 않은 항목은 '건너뜀'으로 저장된다 (answer:null)",
    byText(P3) && byText(P3).skipped === true && byText(P3).answer === null, JSON.stringify(byText(P3)));
  record("분류 칸은 자리만 있고 비어 있다",
    pa.every((a) => "classification" in a && a.classification === null && "classificationBy" in a && "userOverride" in a && a.userOverride === null),
    JSON.stringify(pa[0]));
  record("답에 답할 당시의 문구가 함께 저장된다", byText(P1) && byText(P1).text === P1, "");
  record("결정이 실행함으로 저장된다", saved.decision === "executed", saved.decision);
  record("기대가 그대로 저장된다",
    saved.expectation === "2년 안에 이익이 회복되기를 기대", saved.expectation);

  // 목록 왕복. 순서와 글자가 그대로여야 한다.
  record("해야 할 이유가 같은 목록으로 돌아온다",
    JSON.stringify((saved.reasons_for || []).map((item) => item.text))
      === JSON.stringify(["값이 내렸고 사업은 그대로다", "배당이 늘고 있다"]),
    JSON.stringify(saved.reasons_for));
  record("하지 말아야 할 이유가 같은 목록으로 돌아온다",
    JSON.stringify((saved.reasons_against || []).map((item) => item.text))
      === JSON.stringify(["경쟁사가 같은 제품을 준비 중이다", "환율이 더 오르면 원가가 오른다"]),
    JSON.stringify(saved.reasons_against));

  record("이유가 낱개로 자기 id를 갖는다",
    (saved.reasons_for || []).every((item) => item.id) &&
    new Set((saved.reasons_for || []).concat(saved.reasons_against || []).map((item) => item.id)).size === 4,
    JSON.stringify((saved.reasons_for || []).map((item) => item.id)));

  const marked = (saved.reasons_against || []).filter((item) => item.falsifies);
  record("표시한 반대 항목만 반증 조건이 된다", marked.length === 1, `${marked.length}건`);
  record("반증 종류가 남는다", marked[0] && marked[0].kind === "machine", marked[0] && marked[0].kind);
  record("숫자 조건이 구조로 저장된다",
    Boolean(marked[0] && marked[0].rule && marked[0].rule.value === 52000),
    JSON.stringify(marked[0] && marked[0].rule));
  record("표시하지 않은 항목에는 규칙이 붙지 않는다",
    (saved.reasons_against || []).filter((item) => !item.falsifies).every((item) => !item.rule),
    JSON.stringify(saved.reasons_against));

  const shown = await round.innerText("body");
  record("저장한 기록이 목록에 나온다", shown.includes("삼성전자 매수"), "");
  record("저장한 이유가 목록에 나온다", shown.includes("경쟁사가 같은 제품을 준비 중이다"), "");
  record("맥락이 함께 저장된다",
    Boolean(saved.context && saved.context.businessCycle && saved.context.businessCycle.phase),
    JSON.stringify(saved.context && saved.context.businessCycle));
  record("국면이 신호가 아니라 맥락으로 표시돼 저장된다",
    saved.context.businessCycle.recordedAs === "context_not_signal",
    String(saved.context.businessCycle.recordedAs));
  record("보유 없는 기록의 holding_id가 null이다", saved.holding_id === null, String(saved.holding_id));

  // ── 보류는 안 하기로 함과 다르다 ─────────────────────────────────────────
  await round.fill("#statement", "삼성전자 매도");
  await coreBox().fill("아직 4년이 안 됐다");
  // 손댔다가 비운 칸. 건너뛴 것과 달리 "빈 답"으로 남아야 한다.
  await restBox().click();
  await round.keyboard.type("x", { delay: 5 });
  await round.keyboard.press("Backspace");
  await round.locator('.cols .col:nth-child(1) [data-item-text]').nth(0).fill("비중이 커졌다");
  await round.locator('.cols .col:nth-child(2) [data-item-text]').nth(0).fill("아직 팔 이유를 못 찾았다");
  await round.click('[data-decision="deferred"]');
  await round.waitForTimeout(200);
  await round.click("#save");
  await round.waitForTimeout(800);

  const store = await round.evaluate(() => window.__journalStore.user_decision_records);
  const deferred = store.find((row) => row.decision === "deferred");
  record("판단 보류가 따로 저장된다", Boolean(deferred), JSON.stringify(store.map((r) => r.decision)));
  record("보류는 열린 채로 남는다", deferred.resolved_at == null, String(deferred && deferred.resolved_at));
  record("보류와 실행 안 함이 합쳐지지 않는다",
    deferred.decision !== "not_executed", deferred.decision);

  // 보류는 결정으로 세지 않는다. 나중에 "정한 조건을 지켰는가"의 분모에 들어가면 안 된다.
  const counted = await round.evaluate((rowsIn) =>
    rowsIn.filter((row) => window.JournalStore.countsAsDecision(row)).length, store);
  record("보류는 결정 수에 들어가지 않는다", counted === store.length - 1, `${counted}/${store.length}`);

  record("열린 보류가 화면에 표시된다",
    (await round.innerText("body")).includes("아직 열려 있습니다"), "");

  const deferredP3 = (deferred.principle_answers || []).find((a) => a.text === P3);
  record("손댔다 비운 칸은 '빈 답'으로 저장돼 건너뜀과 구별된다 (skipped:false · answer:\"\")",
    deferredP3 && deferredP3.skipped === false && deferredP3.answer === "", JSON.stringify(deferredP3));
  const recText = await round.innerText("body");
  record("지난 기록에 건너뜀과 빈 답이 다르게 보인다", recText.includes("건너뜀") && recText.includes("빈 답"), "");

  // ── 답이 걸린 원칙을 지우고 고친다 ───────────────────────────────────────
  await round.goto(`http://127.0.0.1:${PORT}/philosophy.html`, { waitUntil: "networkidle" });
  await round.waitForTimeout(800);
  // 검토 질문을 고친다. 이미 쓴 기록 1건(빈 답도 답이다)은 고치기 전 문구에 대한 답으로 남아야 한다.
  const P3b = "가격 말고 지금 사야 할 이유가 있는가";
  await round.locator("li.pitem", { hasText: P3 }).locator("button[data-edit]").click();
  await round.waitForTimeout(150);
  record("고칠 때 과거 답이 어느 문구를 가리키는지 말한다",
    (await round.innerText("body")).includes("고치기 전 문구에 대한 답"), "");
  await round.fill("#edit-text", P3b);
  await round.click("button[data-edit-ok]");
  await round.waitForTimeout(150);
  // 핵심 항목을 지운다. 이 항목에 답한 기록이 둘 있다.
  await round.locator("li.pitem", { hasText: P1 }).locator("button[data-delete]").click();
  await round.waitForTimeout(150);
  const deleteNote = await round.innerText("[data-flash]");
  record("답이 걸린 항목을 지우면 과거 기록 수와 어디 남는지 말한다",
    deleteNote.includes("과거 기록 2건은 지워지지 않고") && deleteNote.includes("지난 기록"), deleteNote);
  await round.fill("#reason", "핵심 항목을 내리고 질문을 다듬음");
  await round.click("#save");
  await round.waitForTimeout(600);
  record("두 번째 저장이 이력에 쌓이고 첫 기록이 남는다",
    (await round.innerText("body")).includes("2번 저장") && (await round.innerText("body")).includes("처음 적음"), "");
  const revisionRows = await round.evaluate(() => window.__journalStore.user_philosophy_revisions.length);
  record("이력이 덧붙기만 한다", revisionRows === 2, `${revisionRows}건`);

  const stillThere = await round.evaluate(() => window.__journalStore.user_decision_records
    .map((row) => (row.principle_answers || []).find((a) => a.text === "3년 이상 보유한다")).filter(Boolean).length);
  record("원칙을 지워도 그 원칙에 대한 과거 답은 지워지지 않는다", stillThere === 2, `${stillThere}건`);

  await round.goto(`http://127.0.0.1:${PORT}/decisions.html`, { waitUntil: "networkidle" });
  await round.waitForTimeout(900);
  const history = await round.innerText("body");
  record("지운 원칙에 대한 과거 답이 답할 당시 문구로 읽힌다",
    history.includes(P1) && history.includes("4년 들고 갈 생각이다") && history.includes("지금은 목록에 없는 항목"), "");
  record("고친 원칙에 대한 과거 답에 '지금 문구와 다름'이 붙는다", history.includes("지금 문구와 다름"), "");
  const stageNow = await round.evaluate(() => Array.from(document.querySelectorAll("li[data-principle] label"))
    .map((node) => node.textContent.replace(/\s+/g, " ").trim()));
  record("새 기록의 1단계에는 지금 목록만 나온다", stageNow.length === 1 && stageNow[0].endsWith(P3b), JSON.stringify(stageNow));

  // ── 서버에 새 칸이 아직 없을 때 ──────────────────────────────────────────
  // 앱은 push하는 순간 배포되지만 데이터베이스 전환은 사람이 적용한다. 그 사이에 기록이
  // 실패하거나 원칙 답이 버려지면 안 된다.
  await round.evaluate(() => sessionStorage.setItem("assetflow.test.missingColumn", "principle_answers"));
  await round.fill("#statement", "현대차 매수");
  await round.locator("li[data-principle]", { hasText: P3b }).locator("textarea").fill("배당이 늘고 있다");
  await round.locator('.cols .col:nth-child(1) [data-item-text]').nth(0).fill("PBR이 낮다");
  await round.locator('.cols .col:nth-child(2) [data-item-text]').nth(0).fill("노조 리스크");
  await round.click('[data-decision="not_executed"]');
  await round.waitForTimeout(200);
  await round.click("#save");
  await round.waitForTimeout(800);
  await round.evaluate(() => sessionStorage.removeItem("assetflow.test.missingColumn"));
  const fallbackRow = await round.evaluate(() => window.__journalStore.user_decision_records.find((row) => row.action_statement === "현대차 매수"));
  record("새 칸이 없어도 기록은 저장된다", Boolean(fallbackRow), "");
  record("새 칸이 없을 때 원칙 답을 맥락 칸에 담아 버리지 않는다",
    Boolean(fallbackRow && fallbackRow.context && Array.isArray(fallbackRow.context.principleAnswers) &&
      fallbackRow.context.principleAnswers[0].answer === "배당이 늘고 있다"), JSON.stringify(fallbackRow && fallbackRow.context && fallbackRow.context.principleAnswers));
  const fallbackText = await round.innerText("body");
  record("맥락 칸에 담았다는 사실을 화면이 밝힌다", fallbackText.includes("맥락 칸에 함께 저장했습니다"), "");
  record("맥락 칸에 담긴 원칙 답도 지난 기록에 그대로 보인다", fallbackText.includes("배당이 늘고 있다"), "");

  // ── 2026-09-02에 쓴 원칙과 기록 (목록이 되기 전) ─────────────────────────
  const legacy = await context.newPage();
  await installTestAuth(legacy);
  await installJournalMock(legacy);
  await legacy.goto(`http://127.0.0.1:${PORT}/philosophy.html`, { waitUntil: "networkidle" });
  const UID = "00000000-0000-0000-0000-000000000001";
  await legacy.evaluate((uid) => {
    const v1 = { buy: "내가 아는 사업이고 값이 싸 보일 때", sell: "산 이유가 사라졌을 때", position: "", horizon: "", loss: "", unknown: "" };
    sessionStorage.setItem("assetflow.test.journal", JSON.stringify({
      user_investment_philosophy: [{ id: "phil-1", user_id: uid, answers: v1, updated_at: "2026-09-02T09:00:00.000Z" }],
      user_philosophy_revisions: [{ id: "rev-1", user_id: uid, reason: "처음 적음", answers: v1, changed_at: "2026-09-02T09:00:00.000Z" }],
      // 트랙 30 모양의 기록. principle_answers 칸 자체가 없다.
      user_decision_records: [{ id: "dec-0902", user_id: uid, decided_at: "2026-09-02T10:00:00.000Z",
        action_statement: "카카오 매도", reasons_for: [{ id: "a", text: "산 이유가 사라졌다" }],
        reasons_against: [{ id: "b", text: "바닥일 수 있다", falsifies: false }], decision: "executed",
        expectation: "손실을 더 키우지 않기", holding_id: null, holding_label: "", context: {} }],
    }));
  }, UID);
  await legacy.reload({ waitUntil: "networkidle" });
  await legacy.waitForTimeout(800);
  const legacyPhil = await legacy.innerText("body");
  record("예전 여섯 질문의 답이 목록 항목으로 옮겨진다",
    legacyPhil.includes("내가 아는 사업이고 값이 싸 보일 때") && legacyPhil.includes("산 이유가 사라졌을 때") && legacyPhil.includes("목록으로 옮겼습니다"), "");
  record("옮긴 항목에 원래 질문이 우리 문구로 섞이지 않고 따로 붙는다",
    await legacy.evaluate(() => Array.from(document.querySelectorAll(".pitem-origin")).length === 2 &&
      Array.from(document.querySelectorAll("li.pitem .pitem-text")).every((node) => !(node.firstChild.textContent || "").includes("합니까"))), "");
  record("예전 모양의 이력도 그대로 읽힌다", legacyPhil.includes("무엇을 보면 사기로 합니까?"), "");

  await legacy.goto(`http://127.0.0.1:${PORT}/decisions.html`, { waitUntil: "networkidle" });
  await legacy.waitForTimeout(900);
  const legacyDec = await legacy.innerText("body");
  record("09-02에 쓴 기록이 열리고 이유가 그대로 보인다", legacyDec.includes("카카오 매도") && legacyDec.includes("산 이유가 사라졌다"), "");
  record("09-02 기록에는 1단계가 없었다고 밝힌다", legacyDec.includes("이 단계가 생기기 전에 쓴 기록입니다"), "");
  record("원칙 화면에서 저장하기 전에도 옮긴 항목이 1단계에 나온다",
    (await legacy.locator("li[data-principle]").count()) === 2, "");

  // 옮긴 항목에 답한 뒤 원칙을 v2로 저장해도 id가 같아 그 답이 같은 항목을 가리켜야 한다.
  await legacy.fill("#statement", "네이버 매수");
  await legacy.locator("li[data-principle]", { hasText: "내가 아는 사업이고" }).locator("textarea").fill("검색 광고를 이해한다");
  await legacy.locator('.cols .col:nth-child(1) [data-item-text]').nth(0).fill("값이 싸졌다");
  await legacy.locator('.cols .col:nth-child(2) [data-item-text]').nth(0).fill("AI 경쟁");
  await legacy.click('[data-decision="executed"]');
  await legacy.click("#save");
  await legacy.waitForTimeout(800);
  await legacy.goto(`http://127.0.0.1:${PORT}/philosophy.html`, { waitUntil: "networkidle" });
  await legacy.waitForTimeout(800);
  await legacy.fill("#reason", "목록으로 옮김");
  await legacy.click("#save");
  await legacy.waitForTimeout(600);
  const migratedDoc = await legacy.evaluate(() => window.__journalStore.user_investment_philosophy[0].answers);
  record("v2로 저장해도 v1 원본이 문서 안에 보존된다 (migratedFrom)",
    migratedDoc.version === 2 && migratedDoc.migratedFrom && migratedDoc.migratedFrom.version === 1 &&
      migratedDoc.migratedFrom.answers.buy === "내가 아는 사업이고 값이 싸 보일 때", JSON.stringify(migratedDoc.migratedFrom));
  const legacyRevs = await legacy.evaluate(() => window.__journalStore.user_philosophy_revisions);
  record("v1 이력 행이 지워지지 않고 v2 행이 덧붙는다",
    legacyRevs.length === 2 && !legacyRevs[0].answers.version && legacyRevs[1].answers.version === 2, `${legacyRevs.length}건`);
  await legacy.goto(`http://127.0.0.1:${PORT}/decisions.html`, { waitUntil: "networkidle" });
  await legacy.waitForTimeout(900);
  const afterMigrate = await legacy.innerText("body");
  record("옮기기 전에 쓴 답이 옮긴 뒤에도 같은 항목을 가리킨다 (id 유지)",
    afterMigrate.includes("검색 광고를 이해한다") && !afterMigrate.includes("지금은 목록에 없는 항목"), "");

  // ── 항목이 많을 때 ───────────────────────────────────────────────────────
  // 원칙이 스무 개면 결정마다 스무 번 묻는다. 핵심은 펼치고 나머지는 하나로 접되,
  // **잘라내지 않는다.** 전부 화면에 있고 몇 개가 접혀 있는지 말한다.
  await legacy.evaluate((uid) => {
    const items = Array.from({ length: 20 }, (_, i) => ({ id: `p-${i}`, text: `원칙 ${i + 1}번`, kind: "principle", core: i < 2 }));
    const store = JSON.parse(sessionStorage.getItem("assetflow.test.journal"));
    store.user_investment_philosophy[0].answers = { version: 2, items };
    sessionStorage.setItem("assetflow.test.journal", JSON.stringify(store));
  }, UID);
  await legacy.reload({ waitUntil: "networkidle" });
  await legacy.waitForTimeout(900);
  const many = await legacy.evaluate(() => ({
    boxes: document.querySelectorAll("[data-principle-answer]").length,
    // 닫힌 <details>는 크로뮴에서 content-visibility:hidden으로 그려져 offsetParent가 null이 아니다.
    // 실제로 보이는지는 checkVisibility로 본다.
    open: Array.from(document.querySelectorAll("[data-principle-answer]")).filter((node) => node.checkVisibility()).length,
    collapsed: document.querySelector("details[data-principle-rest]") ? !document.querySelector("details[data-principle-rest]").open : null,
    summary: (document.querySelector("details[data-principle-rest] summary") || {}).textContent || "",
  }));
  record("항목 20개도 잘라내지 않는다 (20칸 모두 있다)", many.boxes === 20, JSON.stringify(many));
  record("핵심은 펼치고 나머지는 접힌 수를 밝힌다", many.collapsed === true && many.open === 2 && /나머지 18개/.test(many.summary), JSON.stringify(many));

} catch (error) {
  record("화면 검증", false, error.message);
} finally {
  if (browser) await browser.close();
  server.kill();
}

const failed = results.filter((row) => !row.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
