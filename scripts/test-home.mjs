// scripts/test-home.mjs
// 홈 화면(home.html) 검증. 자산을 JS로 주입하지 않고 자산 입력 화면에서 실제로
// 클릭·타이핑해 등록한 뒤 홈으로 이동해서 확인한다 — 총자산은 사용자가 가장 먼저
// 보는 숫자라, 계산이 맞는지를 실제 등록 경로로 확인하지 않으면 의미가 없다.
//
// data/quotes.json은 시작할 때 백업하고 끝나면 복원한다(중간에 실패해도).

import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { readFile, writeFile } from "node:fs/promises";
import { writeFileSync } from "node:fs";
import { installTestAuth, launchTestBrowser } from "./lib/test-auth.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 4332;
const INPUT_URL = `http://127.0.0.1:${PORT}/asset-input.html`;
const HOME_URL = `http://127.0.0.1:${PORT}/home.html`;
const QUOTES_PATH = resolve(ROOT, "data", "quotes.json");

// 실제 배치 산출물과 같은 형태의 고정 픽스처. 값을 고정해야 총자산을 손으로 계산해
// 대조할 수 있다.
const ASOF_DATE = "2026-08-07";
const QUOTES_FIXTURE = {
  asOf: `${ASOF_DATE}T00:00:00+09:00`,
  sources: { equity: "금융위원회_주식시세정보", fx: "한국수출입은행", gold: "금융위원회_일반상품시세정보", crypto: "CoinGecko" },
  // 배치는 종가와 함께 전일 종가를 싣는다(응답의 clpr - vs). 총자산 변화는 이 값으로
  // 계산하므로 픽스처도 같은 모양이어야 한다.
  prevCloseDate: "2026-08-06",
  quotes: {
    "005930": { price: 73400, currency: "KRW", prevClose: 72400 }, // 삼성전자 · +1,000
    "035720": { price: 52300, currency: "KRW", prevClose: 52800 }, // 카카오 · -500
    "000660": { price: 474000, currency: "KRW", prevClose: 474000 }, // SK하이닉스 — 축·열 확인용
  },
  crypto: { BTC: { price: 91000000, currency: "KRW" }, ETH: { price: 2700000, currency: "KRW" } },
  rates: { USD: 1380.5, JPY: 9.25 },
  commodities: { goldPerGram: 151200, goldPerGramPrev: 150000 },
};

// 손으로 계산한 기대값 — 코드가 아니라 사람이 계산한 값과 대조하기 위해 분리해 둔다.
const EXPECT = {
  samsung: 10 * 73400, //      734,000
  kakao: 20 * 52300, //      1,046,000
  savings: 5000000, //      5,000,000
  realestate: 800000000 * 0.5, // 공동명의 50% -> 400,000,000
  btc: 0.5 * 91000000, //     45,500,000
};
EXPECT.total = EXPECT.samsung + EXPECT.kakao + EXPECT.savings + EXPECT.realestate + EXPECT.btc;
const EXPECT_PHYSICAL = EXPECT.realestate; // 실물자산 = 부동산만 (원자재 미등록)

// 자산군 8개가 모두 있는 포트폴리오. 색 구분과 성장 가능성 축 확인용이다.
const SAMPLE_ASSETS = [
  { id: "s1", group: "cash", fields: { currency: "KRW", amount: 3000000 }, autoFields: {} },
  { id: "s2", group: "savings", fields: { productType: "예금", balance: 12000000 }, autoFields: {} },
  { id: "s3", group: "equity", fields: { productName: "삼성전자", productCode: "005930", quantity: 100, averagePrice: 60000 }, autoFields: { currency: "KRW" } },
  { id: "s4", group: "crypto", fields: { productName: "비트코인", productCode: "BTC", quantity: 0.1, averagePrice: 80000000 }, autoFields: { currency: "KRW" } },
  { id: "s5", group: "fund", fields: { valuation: 8000000 }, autoFields: { fundType: "주식형", currency: "KRW" } },
  { id: "s6", group: "bond", fields: { valuation: 6000000 }, autoFields: { bondType: "국채", currency: "KRW" } },
  { id: "s7", group: "commodity", fields: { assetKind: "금", holdingMethod: "KRX 금시장", quantity: 60 }, autoFields: {} },
  { id: "s8", group: "realestate", fields: { propertyType: "아파트", valuation: 1200000000, joint: true, ownershipRate: 50 }, autoFields: {} },
];

// 축 전환 확인용. 원화 현금과 외화 현금을 함께 둬야 원금 보장·통화 축이 실제로 갈리는지 볼 수 있다.
const AXIS_ASSETS = [
  { id: "c1", group: "cash", fields: { currency: "KRW", amount: 3000000 }, autoFields: {} },
  { id: "c2", group: "cash", fields: { currency: "USD", amount: 2000 }, autoFields: {} },
  { id: "s1", group: "savings", fields: { productType: "예금", balance: 12000000 }, autoFields: {} },
  { id: "e1", group: "equity", fields: { productName: "SK하이닉스", productCode: "000660", quantity: 30, averagePrice: 300000 }, autoFields: { currency: "KRW" } },
  { id: "k1", group: "crypto", fields: { productName: "비트코인", productCode: "BTC", quantity: 0.05, averagePrice: 80000000 }, autoFields: { currency: "KRW" } },
  { id: "m1", group: "commodity", fields: { assetKind: "금", holdingMethod: "KRX 금시장", quantity: 50 }, autoFields: {} },
  { id: "r1", group: "realestate", fields: { propertyType: "아파트", valuation: 1200000000, joint: true, ownershipRate: 50 }, autoFields: {} },
];

async function startServer() {
  const server = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1"], {
    cwd: ROOT,
    stdio: "pipe",
  });
  await Promise.race([once(server.stdout, "data"), once(server.stderr, "data"), new Promise((r) => setTimeout(r, 800))]);
  return server;
}

const results = [];
const record = async (label, fn) => {
  try {
    results.push({ label, ...(await fn()) });
  } catch (error) {
    results.push({ label, ok: false, reason: error.message });
  }
  const last = results[results.length - 1];
  console.log(`${last.ok ? "PASS" : "FAIL"}  ${label}${last.ok ? "" : `  — ${last.reason}`}`);
};

async function selectGroups(page, groups) {
  await page.goto(INPUT_URL);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.evaluate((wanted) => {
    document.querySelectorAll("[data-group-check]").forEach((cb) => {
      const shouldCheck = wanted.includes(cb.dataset.groupCheck);
      if (cb.checked !== shouldCheck) {
        cb.checked = shouldCheck;
        cb.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
  }, groups);
  await page.click("[data-start]");
}

async function searchAndPick(page, fieldId, query) {
  await page.locator(`#${fieldId}`).click();
  await page.keyboard.type(query, { delay: 20 });
  await page.waitForSelector(".search-result", { timeout: 5000 });
  await page.locator(".search-result").first().click();
  await page.waitForFunction(() => !document.querySelector(".search-host:not([hidden])"), { timeout: 5000 });
}

async function type(page, selector, text) {
  await page.locator(selector).click();
  await page.keyboard.type(text, { delay: 15 });
}

// 계정 저장이 붙은 뒤로는 localStorage만 심어서는 안 된다. 앱이 원격 사본을 내려받아
// 로컬을 덮어쓰므로, 목 원격(sessionStorage)에도 같은 값을 넣어야 그 세션이 살아남는다.
async function seedSession(page, assets, extra = {}) {
  await page.evaluate(({ assets: seeded, extra: rest }) => {
    const session = Object.assign({ schema: 7, snapshots: [], assets: seeded }, rest);
    localStorage.setItem("assetInput.session", JSON.stringify(session));
    sessionStorage.setItem("assetflow.test.remote", JSON.stringify(session));
  }, { assets, extra });
}

const originalQuotes = await readFile(QUOTES_PATH, "utf8").catch(() => null);

// finally만으로는 부족하다. 이 테스트는 브라우저 라우트 핸들러 안에서 비동기로 도는
// 코드가 있어, 거기서 예외가 나면 Node가 uncaughtException으로 프로세스를 즉시 죽인다.
// 실제로 그렇게 죽어서 커밋된 실배치 산출물(4,402건)이 2건짜리 픽스처로 덮어써진 채
// 남았다. 어떤 경로로 끝나든 원본이 돌아오도록 종료 훅에 동기 복원을 걸어 둔다.
function restoreQuotesSync() {
  try {
    if (originalQuotes !== null) writeFileSync(QUOTES_PATH, originalQuotes, "utf8");
  } catch (error) {
    console.error("[정리] data/quotes.json 복원 실패:", error.message);
  }
}
process.on("exit", restoreQuotesSync);
process.on("uncaughtException", (error) => {
  console.error("[치명] 처리되지 않은 예외:", error);
  restoreQuotesSync();
  process.exit(1);
});
process.on("unhandledRejection", (error) => {
  console.error("[치명] 처리되지 않은 거부:", error);
  restoreQuotesSync();
  process.exit(1);
});

let server;
let browser;

try {
  await writeFile(QUOTES_PATH, JSON.stringify(QUOTES_FIXTURE), "utf8");
  server = await startServer();
  browser = await launchTestBrowser(chromium);
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } }); // PC 폭
  // 페이지가 조용히 죽으면 단언 실패 이유가 "문구가 없음"으로만 보여 원인을 못 찾는다.
  page.on("pageerror", (error) => console.error("  [pageerror]", error.message));
  await installTestAuth(page);

  // ===== 1. 자산 0건 =====
  await page.goto(HOME_URL);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await record("자산 0건이면 홈 대신 자산 입력을 유도한다", async () => {
    const text = await page.locator("#app").textContent();
    if (!text.includes("첫 자산을 추가하세요")) return { ok: false, reason: `빈 상태 문구 없음: ${text.slice(0, 120)}` };
    const cta = await page.locator("a.btn.primary");
    const href = await cta.getAttribute("href");
    if (!href.includes("asset-input.html")) return { ok: false, reason: `입력 화면 링크가 아님: ${href}` };
    // 같은 일을 하는 버튼은 화면마다 같은 이름을 달아야 한다. 빈 화면의 버튼과 자산이
    // 있을 때 머리말의 버튼이 다른 이름이면, 사용자는 매번 다시 읽어야 한다.
    const label = (await cta.textContent()).trim();
    return label === "자산 추가" ? { ok: true } : { ok: false, reason: `빈 화면 버튼 이름 "${label}" (머리말과 같은 "자산 추가" 기대)` };
  });

  // ===== 2. 실제 클릭으로 5개 자산 등록 =====
  await selectGroups(page, ["savings", "equity", "crypto", "realestate"]);

  // 예금·적금 5,000,000원 (requiredReady: productType · balance · maturityRange)
  await page.click('[data-chipset="productType"][data-group="savings"] .chip');
  await type(page, "#field-savings-balance", "5000000");
  // select는 id가 없어 data 속성으로 잡는다(태그 한정 — 앱의 셀렉터 규약과 동일).
  await page.selectOption('select[data-field="maturityRange"][data-group="savings"]', { index: 1 });
  await page.click("[data-next]");

  // 삼성전자 10주 @ 60,000 / 카카오 20주 @ 50,000
  await searchAndPick(page, "field-equity-search", "삼성전자");
  await type(page, "#field-equity-quantity", "10");
  await type(page, "#field-equity-averagePrice", "60000");
  await page.click("[data-add-another]");
  await searchAndPick(page, "field-equity-search", "카카오");
  await type(page, "#field-equity-quantity", "20");
  await type(page, "#field-equity-averagePrice", "50000");
  await page.click("[data-next]");

  // BTC 0.5개 @ 80,000,000
  await searchAndPick(page, "field-crypto-search", "비트코인");
  await type(page, "#field-crypto-quantity", "0.5");
  await type(page, "#field-crypto-averagePrice", "80000000");
  await page.click("[data-next]");

  // 부동산 8억, 공동명의 지분 50%
  // requiredReady: propertyType · purpose · province · district · valuation · basis · priceDate · ownershipRate(공동명의일 때)
  await page.click('[data-chipset="propertyType"][data-group="realestate"] .chip');
  await page.click('[data-chipset="purpose"][data-group="realestate"] .chip');
  await page.selectOption('select[data-field="province"][data-group="realestate"]', { index: 1 });
  await page.selectOption('select[data-field="district"][data-group="realestate"]', { index: 1 });
  await type(page, "#field-realestate-valuation", "800000000");
  await page.click('[data-chipset="basis"][data-group="realestate"] .chip');
  await page.locator("#field-realestate-priceDate").fill("2026-08-01");
  await page.click('[data-toggle="joint"][data-group="realestate"]');
  await type(page, "#field-realestate-ownershipRate", "50");
  // 매입가격은 선택 정보라 접힌 섹션 안에 있다. 실물자산 '평가차액'을 검증하려면
  // 매입 정보가 있어야 하므로 실제로 펼쳐서 입력한다.
  await page.locator('details[data-details="optional-realestate"] > summary').click();
  await type(page, "#field-realestate-purchasePrice", "700000000");
  await page.click("[data-next]");

  const registered = await page.evaluate(() => session.assets.length);
  // 아래 검사 일부는 localStorage를 덮어쓰므로, 실제 등록으로 만든 세션을 붙잡아 두고
  // 그 뒤 검사들 전에 되돌린다(등록 경로로 만든 상태를 다시 만들 방법이 없다).
  const REGISTERED = await page.evaluate(() => localStorage.getItem("assetInput.session"));
  const restoreRegistered = async () => {
    await page.evaluate((raw) => localStorage.setItem("assetInput.session", raw), REGISTERED);
    await page.goto(HOME_URL);
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 8000 });
  };
  await record("자산 입력 화면에서 5건이 실제로 등록됨", async () =>
    registered === 5 ? { ok: true } : { ok: false, reason: `등록된 자산 ${registered}건 (5건 기대)` }
  );

  // ===== 2b. 입력 완료 -> 홈 (원래 완료 화면에 갇혀 홈으로 갈 방법이 아예 없었다) =====
  await record("검토 화면에서 완료하면 완료 화면이 뜬다", async () => {
    await page.click("[data-complete]");
    const text = await page.locator(".done").textContent();
    return text.includes("자산 입력을 완료했어요") ? { ok: true } : { ok: false, reason: `완료 화면이 아님: ${text.slice(0, 80)}` };
  });

  await record("완료 화면에 등록 건수가 표시된다", async () => {
    const text = await page.locator(".done .sub").textContent();
    return text.includes("5건") ? { ok: true } : { ok: false, reason: `안내 문구: "${text}"` };
  });

  await record("완료 화면에서 홈으로 넘어간다", async () => {
    await page.click("button[data-home]");
    await page.waitForURL(/home\.html/, { timeout: 5000 });
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 5000 });
    const total = await page.locator(".total-amount").textContent();
    // 넘어간 홈이 방금 입력한 자산으로 계산돼야 한다 — 이동만 하고 빈 화면이면 의미가 없다.
    return total.includes("4억") ? { ok: true } : { ok: false, reason: `홈 총자산: "${total}"` };
  });

  await record("홈의 '+ 자산 추가'가 완료 화면에 갇히지 않고 검토 화면으로 연다", async () => {
    await page.locator(".page-head a.btn.primary").click();
    await page.waitForURL(/asset-input\.html#add/, { timeout: 5000 });
    await page.waitForFunction(() => document.querySelector(".review-list, .done"), { timeout: 5000 });
    const stuck = await page.locator(".done").count();
    if (stuck) return { ok: false, reason: "완료 화면에 갇힘 — 자산을 더 추가할 수 없다" };
    const hasReview = await page.locator(".review-list").count();
    return hasReview ? { ok: true } : { ok: false, reason: "검토 화면이 열리지 않음" };
  });

  // ===== 3. 홈 화면 =====
  await page.goto(HOME_URL);
  await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 5000 });

  const home = await page.evaluate(() => {
    const summary = Portfolio.summarize(session.assets);
    return {
      total: summary.total,
      rows: summary.rows.map((row) => ({ name: row.name, krw: row.krw, group: row.group, profit: row.profit })),
      axes: Object.fromEntries(Object.entries(summary.financial.axes).map(([key, parts]) => [key, parts.map((p) => ({ label: p.label, share: p.share }))])),
      profitCount: summary.profit.count,
      rowCount: summary.rows.length,
      text: document.getElementById("app").textContent,
      totalText: document.querySelector(".total-amount").textContent,
    };
  });

  await record("총자산이 개별 평가금액의 합과 일치한다 (손으로 계산한 값과 대조)", async () => {
    const sum = home.rows.reduce((acc, row) => acc + row.krw, 0);
    if (home.total !== sum) return { ok: false, reason: `총자산 ${home.total} ≠ 행 합계 ${sum}` };
    if (home.total !== EXPECT.total) return { ok: false, reason: `총자산 ${home.total} ≠ 기대값 ${EXPECT.total} · rows=${JSON.stringify(home.rows)}` };
    return { ok: true };
  });

  await record("부동산 공동명의 지분율 50%가 반영된다 (8억 -> 4억)", async () => {
    const realestate = home.rows.find((row) => row.group === "realestate");
    if (!realestate) return { ok: false, reason: "부동산 자산이 없음" };
    return realestate.krw === EXPECT.realestate ? { ok: true } : { ok: false, reason: `${realestate.krw} (기대 ${EXPECT.realestate})` };
  });

  await record("가상자산이 quotes.json 시세로 총자산에 포함된다", async () => {
    const btc = home.rows.find((row) => row.group === "crypto");
    if (!btc) return { ok: false, reason: "가상자산이 없음" };
    return btc.krw === EXPECT.btc ? { ok: true } : { ok: false, reason: `${btc.krw} (기대 ${EXPECT.btc})` };
  });

  await record("금융자산 구성 축 3개의 비중 합이 각각 100%다", async () => {
    for (const key of ["group", "guaranteed", "currency"]) {
      const sum = home.axes[key].reduce((acc, part) => acc + part.share, 0);
      if (Math.abs(sum - 1) > 1e-9) return { ok: false, reason: `${key} 축 합계 ${sum}` };
    }
    return { ok: true };
  });

  await record("총자산이 금융자산 + 실물자산으로 분해된다", async () => {
    const split = await page.evaluate(() => {
      const summary = Portfolio.summarize(session.assets);
      return { financial: summary.financial.total, physical: summary.physical.total, total: summary.total };
    });
    // 실물자산 = 부동산 4억 (원자재 없음), 금융자산 = 나머지 전부
    if (split.physical !== EXPECT_PHYSICAL) return { ok: false, reason: `실물자산 ${split.physical} (기대 ${EXPECT_PHYSICAL})` };
    if (split.financial + split.physical !== split.total) return { ok: false, reason: `분해 합 ${split.financial + split.physical} ≠ 총자산 ${split.total}` };
    const text = await page.locator(".split").textContent();
    if (!text.includes("금융자산") || !text.includes("실물자산")) return { ok: false, reason: `분해 표시 없음: ${text}` };
    return { ok: true };
  });

  await record("구성 카드가 금융자산/실물자산으로 전환된다", async () => {
    const financialLabels = await page.locator(".legend-row .name").allTextContents();
    if (financialLabels.includes("부동산")) return { ok: false, reason: `금융자산 쪽에 부동산이 있음: ${financialLabels.join(",")}` };
    await page.locator('button[data-class="physical"]').click();
    const physicalLabels = await page.locator(".legend-row .name").allTextContents();
    if (!physicalLabels.includes("부동산")) return { ok: false, reason: `실물자산 쪽에 부동산이 없음: ${physicalLabels.join(",")}` };
    // 실물자산은 전부 시가 변동·전부 원화라 두 축이 의미가 없다 — 축 탭을 두지 않는다.
    const axisTabs = await page.locator("button[data-axis]").count();
    if (axisTabs) return { ok: false, reason: `실물자산에 축 탭이 ${axisTabs}개 남아 있음` };
    await page.locator('button[data-class="financial"]').click();
    return { ok: true };
  });

  await record("구성 그래프가 도넛이고 조각 수가 범례와 일치한다", async () => {
    if (await page.locator(".stackbar").count()) return { ok: false, reason: "가로 누적 막대가 아직 있음" };
    const segments = await page.locator("svg.donut .donut-seg").count();
    const legends = await page.locator(".legend-row").count();
    return segments === legends && segments > 0 ? { ok: true } : { ok: false, reason: `도넛 조각 ${segments}개 / 범례 ${legends}개` };
  });

  await record("도넛 조각 클릭이 해당 자산군 화면으로 이동한다", async () => {
    const seg = page.locator("svg.donut .donut-seg").first();
    const label = await seg.getAttribute("data-slice");
    // 도넛은 fill="none"이라 칠해진 호만 반응한다 — 요소 중심(구멍)이 아니라 링 위를 누른다.
    const box = await seg.boundingBox();
    // 12시 방향은 마지막 조각이 끝나는 지점이라 첫 조각의 시작과 겹친다(나중에 그려진
    // 쪽이 위에 놓인다). 최대 조각의 한가운데인 3시 방향 링 위를 누른다.
    await seg.click({ position: { x: box.width * (169 / 184), y: box.height * 0.5 } });
    await page.waitForURL(/assets\.html/, { timeout: 5000 });
    const url = page.url();
    const activeTab = await page.locator(".group-tab.active").textContent();
    await page.goBack();
    await page.waitForFunction(() => document.querySelector("svg.donut"), { timeout: 5000 });
    if (!url.includes("composition=")) return { ok: false, reason: `이동한 URL: ${url}` };
    return activeTab.includes(label) ? { ok: true } : { ok: false, reason: `"${label}" 조각을 눌렀는데 열린 탭: "${activeTab}"` };
  });

  await record("범례 행의 터치 타겟이 충분하다 (44px 이상)", async () => {
    const boxes = await page.locator(".legend-row").evaluateAll((nodes) => nodes.map((node) => node.getBoundingClientRect().height));
    const small = boxes.filter((height) => height < 44);
    return small.length ? { ok: false, reason: `44px 미만 행 ${small.length}개: ${small.map((h) => h.toFixed(1)).join(",")}` } : { ok: true };
  });

  await record("요약 축이 금융자산 기준이고 카드에 명시된다", async () => {
    const meters = await page.evaluate(() => {
      const summary = Portfolio.summarize(session.assets);
      return Object.fromEntries(summary.meters.map((meter) => [meter.key, meter.value]));
    });
    // 부동산(4억)이 빠지면 집중도는 금융자산 5,674만원 기준이라 100%를 향하지 않는다.
    const heading = await page.locator(".card h2").allTextContents();
    if (!heading.some((text) => text.includes("금융자산 기준"))) return { ok: false, reason: `카드 제목에 기준 표기 없음: ${heading.join(" | ")}` };
    const withRealestate = await page.evaluate(() => {
      const summary = Portfolio.summarize(session.assets);
      const top3 = summary.rows.slice(0, 3).reduce((sum, row) => sum + row.krw, 0);
      return top3 / summary.total;
    });
    if (Math.abs(meters.concentration - withRealestate) < 1e-9) return { ok: false, reason: "집중도가 여전히 전체 자산 기준" };
    return { ok: true };
  });

  // 왼쪽 끝부터 채우면 "가득 채울수록 좋다"로 읽힌다. 이 축들은 점수가 아니므로
  // 중앙(50%)에서 값까지만 뻗어야 한다.
  await record("요약 축이 중앙에서 값까지만 채워진다 (왼쪽 끝부터 차오르지 않음)", async () => {
    const fills = await page.locator(".meter-fill").evaluateAll((nodes) => nodes.map((node) => {
      const track = node.parentElement.getBoundingClientRect();
      const fill = node.getBoundingClientRect();
      const center = track.left + track.width / 2;
      return {
        fromLeftEdge: Math.abs(fill.left - track.left) < 1.5 && fill.width > track.width * 0.52,
        touchesCenter: Math.abs(fill.left - center) < 2 || Math.abs(fill.right - center) < 2,
      };
    }));
    if (fills.length !== 3) return { ok: false, reason: `채워진 선 ${fills.length}개 (축 3개 기대)` };
    const fromEdge = fills.filter((fill) => fill.fromLeftEdge);
    if (fromEdge.length) return { ok: false, reason: `왼쪽 끝부터 채워진 축 ${fromEdge.length}개` };
    const detached = fills.filter((fill) => !fill.touchesCenter);
    if (detached.length) return { ok: false, reason: `중앙에서 시작하지 않는 축 ${detached.length}개` };
    return { ok: true };
  });

  await record("축 설명 문구가 그대로 유지된다", async () => {
    const text = await page.locator("#app").textContent();
    return text.includes("축은 위치를 나타냅니다. 점수나 등급이 아니며, 높다고 좋거나 낮다고 나쁜 것이 아닙니다.")
      ? { ok: true } : { ok: false, reason: "문구가 사라졌거나 바뀜" };
  });

  // 4번: 카드를 없애지 않고 총자산 아래 한 줄로 옮긴다. 이 줄은 화면의 숫자가 왜
  // 불완전한지 설명하는 신뢰 장치라, 사라지면 총자산이 왜 어긋나는지 알 수 없다.
  await record("확인이 필요한 항목이 총자산 카드 안으로 옮겨졌다", async () => {
    const inTotalCard = await page.evaluate(() => {
      const line = document.querySelector(".check-line");
      if (!line) return null;
      return Boolean(line.closest(".card") && line.closest(".card").querySelector(".total-amount"));
    });
    if (inTotalCard === null) return { ok: false, reason: "확인이 필요한 항목 줄이 없음 (이 포트폴리오에는 항목이 있어야 한다)" };
    if (!inTotalCard) return { ok: false, reason: "총자산 카드 밖에 있음" };
    // 카드로 남아 있으면 안 된다 — 상단 3열의 그 자리는 뉴스 칸이 쓴다.
    const topCards = await page.locator(".grid-top > .card h2").allTextContents();
    if (topCards.some((text) => text.includes("확인이 필요한 항목"))) return { ok: false, reason: "아직 상단에 별도 카드로 남아 있음" };
    return { ok: true };
  });

  await record("항목이 없으면 그 줄이 숨는다", async () => {
    // 시세·매입 정보가 모두 갖춰지고 건너뛴 자산군도 없는 세션.
    await page.evaluate(() => {
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 7, snapshots: [], skippedGroups: [], assets: [
        { id: "x1", group: "equity", fields: { productName: "삼성전자", productCode: "005930", quantity: 10, averagePrice: 60000 }, autoFields: { currency: "KRW" } },
      ] }));
    });
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 8000 });
    const count = await page.locator(".check-line").count();
    if (!count) return { ok: true };
    // 어떤 항목이 남았는지 적어야 원인을 바로 안다. "줄이 보인다"만으로는 추적이 안 된다.
    const shown = (await page.locator(".check-line").innerText()).replace(/\n+/g, " · ");
    const assets = await page.evaluate(() => (session.assets || []).map((asset) => asset.group));
    return { ok: false, reason: `확인할 항목이 없는데도 줄이 보인다: ${shown} (자산 ${JSON.stringify(assets)})` };
  });

  await record("시세를 못 받은 자산이 있으면 그 안내가 실제로 뜬다", async () => {
    await page.evaluate(() => {
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 7, snapshots: [], skippedGroups: [], assets: [
        { id: "y1", group: "equity", fields: { productName: "삼성전자", productCode: "005930", quantity: 10, averagePrice: 60000 }, autoFields: { currency: "KRW" } },
        // quotes 픽스처에 없는 종목 — 시세를 받을 수 없다.
        { id: "y2", group: "equity", fields: { productName: "없는종목", productCode: "999999", quantity: 5, averagePrice: 10000 }, autoFields: { currency: "KRW" } },
      ] }));
    });
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 8000 });
    const text = await page.locator("#app").textContent();
    if (!text.includes("시세를 확인하지 못한")) return { ok: false, reason: "시세 확인 불가 안내가 없음" };
    if (!text.includes("없는종목")) return { ok: false, reason: "어떤 자산인지 밝히지 않음" };
    return { ok: true };
  });

  // 3번: 좌측 자산 탭을 눌렀을 때 보유하지 않은 자산군까지 8개 전부 펼쳐져야 한다.
  await record("좌측 자산을 누르면 자산군 8개가 모두 펼쳐진다 (0건 포함)", async () => {
    await page.goto(HOME_URL);
    await page.evaluate((assets) => {
      // 현금만 보유 — 나머지 7개 자산군은 0건이다.
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 7, snapshots: [], assets: assets.slice(0, 1) }));
    }, AXIS_ASSETS);
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 8000 });
    if (await page.locator(".nav-sub").count()) return { ok: false, reason: "누르지 않았는데 이미 펼쳐져 있음" };
    await page.locator("[data-nav-toggle]").click();
    const items = await page.locator(".nav-subitem").allTextContents();
    if (items.length !== 8) return { ok: false, reason: `펼쳐진 자산군 ${items.length}개 (8개 기대): ${items.join(", ")}` };
    // 보유하지 않은 자산군을 숨기면 무엇을 더 넣을 수 있는지 알 수 없다.
    for (const label of ["현금", "예금·적금", "주식·ETF", "펀드", "채권", "가상자산", "원자재·실물자산", "부동산"]) {
      if (!items.some((item) => item.includes(label))) return { ok: false, reason: `"${label}"이 목록에 없음` };
    }
    const zeros = await page.locator(".nav-count.zero").count();
    if (zeros !== 7) return { ok: false, reason: `0건 표시 ${zeros}개 (7개 기대)` };
    return { ok: true };
  });

  await record("펼쳐진 자산군을 누르면 그 자산군 화면이 열린다", async () => {
    await page.locator('.nav-subitem[data-nav-group="bond"]').click();
    await page.waitForURL(/assets\.html#group=bond/, { timeout: 5000 });
    await page.waitForSelector(".group-tabs", { timeout: 5000 });
    const active = await page.locator(".group-tab.active").textContent();
    // 자산 화면에서는 하위 목록이 기본으로 펼쳐져 있어야 한다(지금 어디인지 보여야 한다).
    const subOpen = await page.locator(".nav-sub").count();
    if (!subOpen) return { ok: false, reason: "자산 화면에서 하위 목록이 접혀 있음" };
    const navActive = await page.locator(".nav-subitem.active").textContent();
    if (!active.includes("채권")) return { ok: false, reason: `열린 탭: "${active}"` };
    return navActive.includes("채권") ? { ok: true } : { ok: false, reason: `내비 강조: "${navActive}"` };
  });

  // 5번: 자리만 잡아둔 칸들
  await record("오늘의 주요 뉴스만 준비 중이고, 다가오는 일정·주요 경제지표는 실제로 동작한다", async () => {
    await page.goto(HOME_URL);
    await page.evaluate((assets) => {
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 7, snapshots: [], assets }));
    }, AXIS_ASSETS);
    await page.reload();
    await page.waitForFunction(() => document.querySelector("svg.donut"), { timeout: 8000 });
    const headings = await page.locator(".card h2").allTextContents();
    const news = headings.find((text) => text.includes("오늘의 주요 뉴스"));
    if (!news) return { ok: false, reason: `오늘의 주요 뉴스 칸이 없음: ${headings.join(" | ")}` };
    if (!news.includes("준비 중")) return { ok: false, reason: "오늘의 주요 뉴스에 준비 중 표시가 없음" };
    // 다가오는 일정과 주요 경제지표는 이제 자리표시가 아니라 실제 기능이다.
    for (const title of ["다가오는 일정", "주요 경제지표"]) {
      const found = headings.find((text) => text.includes(title));
      if (!found) return { ok: false, reason: `"${title}" 칸이 없음: ${headings.join(" | ")}` };
      if (found.includes("준비 중")) return { ok: false, reason: `"${title}"이 아직 준비 중 표시로 남아 있음` };
    }
    // 이 카드는 자산 만기가 아니라 거시 경제 일정을 가리킨다 — 그 경계를 지켜야 한다.
    const body = await page.locator(".card").filter({ hasText: "다가오는 일정" }).textContent();
    if (/만기/.test(body)) return { ok: false, reason: "다가오는 일정이 자산 만기를 가리킨다" };
    return { ok: true };
  });

  // 위 몇 검사가 localStorage를 덮어썼으므로, 등록으로 만든 세션으로 되돌린다.
  await restoreRegistered();

  await record("성장 가능성 축은 노출하지 않는다 (위험자산 비중과 같은 값)", async () => {
    const keys = await page.evaluate(() => Portfolio.summarize(session.assets).meters.map((meter) => meter.key));
    if (keys.includes("growth")) return { ok: false, reason: `축에 growth가 남아 있음: ${keys.join(",")}` };
    // 분류 자체는 남아 있어야 한다 — 업종 데이터로 주식 내부를 세분화할 때 다시 쓴다.
    const stillClassified = await page.evaluate(() => Portfolio.summarize(session.assets).rows.every((row) => Boolean(row.growth)));
    return stillClassified ? { ok: true } : { ok: false, reason: "growth 분류 자체가 사라짐" };
  });

  await record("평가손익 모집단과 총자산 모집단이 다르다는 것이 화면에 드러난다", async () => {
    const financialProfitCount = await page.evaluate(() => Portfolio.summarize(session.assets).financial.profit.count);
    if (financialProfitCount >= home.rowCount) return { ok: false, reason: `손익 ${financialProfitCount}건 / 전체 ${home.rowCount}건 — 모집단이 같아 검증이 성립하지 않음` };
    const needle = `매입 정보가 있는 ${financialProfitCount}개 금융자산 기준`;
    if (!home.text.includes(needle)) return { ok: false, reason: `"${needle}" 문구가 화면에 없음` };
    return { ok: true };
  });

  // 4①: 평가손익을 금융자산 기준으로 분리하고, 실물자산은 '평가차액'으로 따로 낸다.
  // 부동산의 차액은 시장이 매긴 값이 아니라 사용자가 적어 넣은 추정가와 매입가의 차이라
  // 같은 줄에 더하면 숫자의 성격이 섞인다(실측: 합산 손익의 98.7%가 부동산이었다).
  await record("평가손익은 금융자산 기준이고, 실물자산은 평가차액으로 분리된다", async () => {
    const split = await page.evaluate(() => {
      const summary = Portfolio.summarize(session.assets);
      return { financial: summary.financial.profit, physical: summary.physical.gap };
    });
    if (!split.physical.count) return { ok: false, reason: "실물자산 매입 정보가 없어 검증 전제가 성립하지 않음" };
    if (split.financial.sum === split.financial.sum + split.physical.sum) return { ok: false, reason: "두 값이 합쳐져 있음" };
    const text = await page.locator(".pl-strip").textContent();
    if (!text.includes("평가손익")) return { ok: false, reason: "평가손익 표기 없음" };
    if (!text.includes("실물자산 평가차액")) return { ok: false, reason: "실물자산 평가차액 표기 없음" };
    if (!text.includes("사용자가 입력한 추정가 기준")) return { ok: false, reason: "평가차액의 근거 표기가 없음" };
    // 금융 손익에 부동산 차액이 섞이지 않아야 한다.
    const financialRows = await page.evaluate(() => Portfolio.summarize(session.assets).financial.rows.filter((row) => row.profit !== null).reduce((sum, row) => sum + row.profit, 0));
    return split.financial.sum === financialRows ? { ok: true } : { ok: false, reason: `금융 손익 ${split.financial.sum} ≠ 금융자산 행 합 ${financialRows}` };
  });

  await record("기준일 표기가 quotes.json의 asOf와 일치한다", async () => {
    // 기준일은 이제 제목 아래 부제가 아니라 기준일 레일이 들고 있다.
    const anchor = await page.locator(".rail-anchor").textContent();
    return anchor.includes(ASOF_DATE) && anchor.includes("종가 기준")
      ? { ok: true }
      : { ok: false, reason: `레일 기준일: "${anchor}" (${ASOF_DATE} 기대)` };
  });

  await record("CoinGecko 출처와 국제 시세 기준 표기가 뜬다", async () => {
    if (!home.text.includes("Data provided by CoinGecko")) return { ok: false, reason: "출처 표기 없음" };
    if (!home.text.includes("국제 시세 기준")) return { ok: false, reason: "국제 시세 기준 표기 없음" };
    return { ok: true };
  });

  // PC 1440px에서 목업 1페이지의 배치가 재현되는지. 세로 단일 스택으로 돌아가면
  // 이 검사가 깨진다.
  await record("PC 1440px에서 상단이 3열, 그 아래도 3열로 배치된다", async () => {
    const boxes = await page.evaluate(() => {
      const pick = (selector) => [...document.querySelectorAll(selector)].map((node) => {
        const rect = node.getBoundingClientRect();
        return { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width) };
      });
      return { top: pick(".grid-top > .card"), half: pick(".grid-mid > .card"), nav: pick(".nav")[0] };
    });
    if (!boxes.nav) return { ok: false, reason: "좌측 내비가 없음" };
    if (boxes.top.length !== 3) return { ok: false, reason: `상단 카드 ${boxes.top.length}개 (3개 기대)` };
    if (new Set(boxes.top.map((box) => box.y)).size !== 1) return { ok: false, reason: `상단 3열이 같은 행에 있지 않음: ${JSON.stringify(boxes.top)}` };
    if (boxes.half.length !== 3) return { ok: false, reason: `중간 행 카드 ${boxes.half.length}개 (3개 기대)` };
    if (new Set(boxes.half.map((box) => box.y)).size !== 1) return { ok: false, reason: "자산 구성·경제지표·포트폴리오 요약이 같은 행에 있지 않음" };
    if (boxes.half[0].y <= boxes.top[0].y) return { ok: false, reason: "중간 행이 상단 행보다 위에 있음" };
    return { ok: true };
  });

  await record("보유 자산 표에 여섯 개 열이 모두 있다", async () => {
    const headers = await page.locator("table.data thead th").allTextContents();
    const want = ["자산명", "자산 유형", "평가금액", "평가손익", "전체 대비 비중", "최근 업데이트"];
    const missing = want.filter((label) => !headers.includes(label));
    return missing.length ? { ok: false, reason: `빠진 열: ${missing.join(", ")} · 실제: ${headers.join(", ")}` } : { ok: true };
  });

  // 모바일에서 접어뒀던 두 카드는 PC에서 펼친다.
  await record("확인이 필요한 항목과 다가오는 일정이 접히지 않고 펼쳐져 있다", async () => {
    if (await page.locator("details").count()) return { ok: false, reason: "접힌 카드(details)가 남아 있음" };
    const text = await page.locator(".grid-top").textContent();
    if (!text.includes("확인이 필요한 항목")) return { ok: false, reason: "확인이 필요한 항목 카드가 상단에 없음" };
    if (!text.includes("다가오는 일정")) return { ok: false, reason: "다가오는 일정 카드가 상단에 없음" };
    return { ok: true };
  });

  await record("범례 행 클릭이 해당 자산군 탭으로 이동한다", async () => {
    const row = page.locator(".legend-row").first();
    const label = await row.locator(".name").textContent();
    await row.click();
    await page.waitForURL(/assets\.html/, { timeout: 5000 });
    const url = page.url();
    // 누른 항목의 탭이 실제로 열려야 한다 — 단순 이동만으로는 부족하다.
    const activeTab = await page.locator(".group-tab.active").textContent();
    await page.goBack();
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 5000 });
    if (!url.includes(`composition=${encodeURIComponent(label)}`)) return { ok: false, reason: `"${label}" 범례를 눌렀는데 이동한 URL: ${url}` };
    return activeTab.includes(label) ? { ok: true } : { ok: false, reason: `"${label}"을 눌렀는데 열린 탭: "${activeTab}"` };
  });

  await record("보유 자산 행 클릭이 실제로 이동한다", async () => {
    await page.locator("tr[data-asset]").first().click();
    await page.waitForURL(/asset-input\.html/, { timeout: 5000 });
    const url = page.url();
    await page.goBack();
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 5000 });
    return url.includes("asset=") ? { ok: true } : { ok: false, reason: `이동한 URL: ${url}` };
  });

  // 변화의 기준선은 방문 이력이 아니라 시세 데이터다. 스냅샷은 추이 차트만 쓴다.
  await record("첫날에도 전일 종가와 비교해 변화를 낸다 (스냅샷 이력과 무관)", async () => {
    const snapshots = await page.evaluate(() => session.snapshots);
    if (snapshots.length !== 1) return { ok: false, reason: `스냅샷 ${snapshots.length}건 (1건 기대)` };
    if (snapshots[0].date !== ASOF_DATE) return { ok: false, reason: `스냅샷 날짜 ${snapshots[0].date}` };
    const change = await page.locator(".total-change").textContent();
    // 픽스처: 삼성전자 10주 +1,000 = +10,000 · 카카오 20주 -500 = -10,000 → 합계 0
    // 하지만 "0원"이 아니라 비교 날짜가 데이터에서 온 값인지가 요점이다.
    if (!change.includes("2026-08-06")) return { ok: false, reason: `비교 날짜가 데이터에서 오지 않음: "${change}"` };
    if (!/종가 대비/.test(change)) return { ok: false, reason: `변화 문구: "${change}"` };
    if (/내일부터/.test(change)) return { ok: false, reason: "아직 방문 이력 기준 문구가 남아 있음" };
    return { ok: true };
  });

  // 기존 결함: 스냅샷이 총액을 담고 있어 자산을 추가하면 그 금액이 통째로 변동으로 잡혔다.
  await record("자산을 추가해도 변화로 잡히지 않는다", async () => {
    const before = await page.evaluate(() => dayChange(session.assets));
    await page.evaluate(() => {
      session.assets.push({ id: "added", group: "savings", fields: { productType: "예금", balance: 50000000 }, autoFields: {} });
      render();
    });
    const after = await page.evaluate(() => dayChange(session.assets));
    const text = await page.locator(".total-change").textContent();
    await page.evaluate(() => {
      session.assets = session.assets.filter((asset) => asset.id !== "added");
      render();
    });
    if (after.delta !== before.delta) {
      return { ok: false, reason: `5,000만원 예금을 넣었더니 변화가 ${before.delta} → ${after.delta}로 바뀜` };
    }
    if (/5,000만원/.test(text)) return { ok: false, reason: `추가한 금액이 변화로 표시됨: "${text}"` };
    return { ok: true };
  });

  // 전일 종가가 없는 자산은 0으로 때우지 않고 빼고, 몇 건인지 밝힌다.
  await record("전일 종가가 없는 자산은 제외하고 건수를 밝힌다", async () => {
    const probe = await page.evaluate(() => {
      // 가상자산은 코인게코가 전일 종가를 주지 않는다 — 제외 대상이다.
      const btc = session.assets.find((asset) => asset.group === "crypto");
      const result = Valuation.valuatePrevious(btc);
      const move = dayChange(session.assets);
      return { reason: result.reason, unavailable: result.unavailable, excluded: move.excluded.map((entry) => entry.name), delta: move.delta };
    });
    if (!probe.unavailable) return { ok: false, reason: "가상자산에 전일 종가가 있다고 나옴" };
    if (!probe.excluded.length) return { ok: false, reason: "제외 목록이 비어 있음" };
    const text = await page.locator(".total-change, .change-note").allTextContents();
    const joined = text.join(" ");
    if (!/제외/.test(joined)) return { ok: false, reason: `제외 건수를 밝히지 않음: ${joined}` };
    // 0으로 더해 버리면 delta가 그 자산의 현재 평가액만큼 흔들린다.
    if (!Number.isFinite(probe.delta)) return { ok: false, reason: `delta가 숫자가 아님: ${probe.delta}` };
    return { ok: true };
  });

  await record("같은 asOf로 다시 열어도 스냅샷이 중복되지 않는다", async () => {
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 5000 });
    const snapshots = await page.evaluate(() => session.snapshots);
    return snapshots.length === 1 ? { ok: true } : { ok: false, reason: `재방문 후 스냅샷 ${snapshots.length}건` };
  });

  // ===== 4. 시세 로드 실패 =====
  await record("quotes.json 로드 실패 시 계산 불가를 알리고 입력 자산은 그대로 보여준다", async () => {
    await page.route("**/data/quotes.json", (route) => route.abort());
    await page.goto(HOME_URL);
    await page.waitForFunction(() => document.querySelector(".banner"), { timeout: 5000 });
    const text = await page.locator("#app").textContent();
    await page.unroute("**/data/quotes.json");
    // 오류는 무엇이 안 됐는지, 무엇이 무사한지, 다음에 무엇을 하면 되는지를 다 말해야 한다.
    if (!text.includes("총자산을 계산하지 못했습니다")) return { ok: false, reason: "계산 불가 안내가 없음" };
    if (!text.includes("data/quotes.json")) return { ok: false, reason: "무엇을 읽지 못했는지 밝히지 않음" };
    if (!text.includes("자산 5건은 그대로 있습니다")) return { ok: false, reason: "입력 자산 건수 안내가 없음" };
    if (!/새로고침|확인하세요/.test(text)) return { ok: false, reason: "다음에 무엇을 할지 알려주지 않음" };
    return { ok: true };
  });

  // ===== 5. 끊긴 구간은 직선으로 잇지 않는다 =====
  await record("기록이 없는 구간은 점선으로 표시하고 비어 있는 일수를 밝힌다", async () => {
    await page.goto(HOME_URL);
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 5000 });
    await page.evaluate(() => {
      session.snapshots = [
        { date: "2026-08-01", total: 100000000 },
        { date: "2026-08-02", total: 101000000 },
        { date: "2026-08-07", total: 105000000 }, // 3일치 구멍
      ];
      saveSession();
      render();
    });
    const dashed = await page.locator("svg.chart line[stroke-dasharray]").count();
    const note = await page.locator(".chart-note").textContent();
    if (dashed !== 1) return { ok: false, reason: `점선 세그먼트 ${dashed}개 (1개 기대)` };
    return note.includes("4일치 기록이 비어 있어요") ? { ok: true } : { ok: false, reason: `안내 문구: "${note}"` };
  });

  // ===== 5a. 자산군이 전부 있을 때 색이 서로 구분되는가 =====
  await record("자산군 8개가 모두 있어도 구성 색이 서로 겹치지 않는다", async () => {
    await page.goto(HOME_URL);
    await page.evaluate((assets) => {
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 7, assets, snapshots: [] }));
    }, SAMPLE_ASSETS);
    await page.reload();
    await page.waitForFunction(() => document.querySelector("svg.donut"), { timeout: 5000 });
    const colorsOf = () => page.locator(".legend-row .swatch").evaluateAll((nodes) => nodes.map((node) => getComputedStyle(node).backgroundColor));
    const financial = await colorsOf();
    if (financial.length !== 6) return { ok: false, reason: `금융자산 항목 ${financial.length}개 (6개 기대)` };
    if (new Set(financial).size !== financial.length) return { ok: false, reason: `색 중복: ${financial.join(", ")}` };
    await page.locator('button[data-class="physical"]').click();
    const physical = await colorsOf();
    if (new Set(physical).size !== physical.length) return { ok: false, reason: `실물자산 색 중복: ${physical.join(", ")}` };
    await page.locator('button[data-class="financial"]').click();
    return { ok: true };
  });

  await record("부동산이 큰 포트폴리오에서도 금융자산 구성이 읽힌다", async () => {
    // 부동산 6억 vs 금융자산 ~5천만. 한 그래프에 넣으면 나머지가 실 가닥이 된다.
    const shares = await page.evaluate(() => {
      const summary = Portfolio.summarize(session.assets);
      return {
        physicalShareOfTotal: summary.physical.share,
        smallestFinancial: Math.min(...summary.financial.axes.group.map((part) => part.share)),
      };
    });
    if (shares.physicalShareOfTotal < 0.8) return { ok: false, reason: `실물 비중 ${shares.physicalShareOfTotal} — 검증 전제가 성립하지 않음` };
    // 금융자산만 따로 보면 가장 작은 항목도 1% 이상으로 올라온다(전체 기준이면 0.3%대).
    return shares.smallestFinancial > 0.01 ? { ok: true } : { ok: false, reason: `가장 작은 금융자산 항목 비중 ${shares.smallestFinancial}` };
  });

  await restoreRegistered();

  // ===== 5a-1b. 축 전환이 실제로 갈리는지 =====
  await record("원금 보장 축이 원화 현금·예적금과 나머지로 갈린다", async () => {
    await page.goto(HOME_URL);
    await page.evaluate((assets) => {
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 7, assets, snapshots: [] }));
    }, AXIS_ASSETS);
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".legend-row"), { timeout: 8000 });
    await page.locator('button[data-axis="guaranteed"]').click();
    const parts = await page.evaluate(() => Portfolio.summarize(session.assets).financial.axes.guaranteed);
    const guaranteed = parts.find((part) => part.label === "원금 보장");
    // 원화 현금 300만 + 예적금 1,200만 = 1,500만. USD 현금은 환율에 따라 원화 가치가
    // 변하므로 시가 변동 쪽이어야 한다.
    if (!guaranteed || guaranteed.krw !== 15000000) return { ok: false, reason: `원금 보장 ${guaranteed ? guaranteed.krw : "없음"} (15,000,000 기대)` };
    const sum = parts.reduce((acc, part) => acc + part.share, 0);
    return Math.abs(sum - 1) < 1e-9 ? { ok: true } : { ok: false, reason: `합계 ${sum}` };
  });

  await record("통화 축에서 USD 현금이 달러로 잡히고 커버리지가 100%다", async () => {
    await page.locator('button[data-axis="currency"]').click();
    const parts = await page.evaluate(() => Portfolio.summarize(session.assets).financial.axes.currency);
    const usd = parts.find((part) => part.label === "달러");
    if (!usd) return { ok: false, reason: `달러 항목 없음: ${parts.map((part) => part.label).join(", ")}` };
    // USD 2,000 × 실제 환율. 1:1로 계산됐다면 2,000원이 나온다.
    if (usd.krw < 1000000) return { ok: false, reason: `달러 ${usd.krw}원 — 환율이 적용되지 않은 값으로 보임` };
    const sum = parts.reduce((acc, part) => acc + part.share, 0);
    if (Math.abs(sum - 1) > 1e-9) return { ok: false, reason: `합계 ${sum}` };
    // 분류되지 않은 자산이 없어야 한다.
    const rows = await page.evaluate(() => Portfolio.summarize(session.assets).financial.rows.length);
    const counted = await page.evaluate(() => Portfolio.summarize(session.assets).financial.axes.currency.length);
    return counted > 0 && rows > 0 ? { ok: true } : { ok: false, reason: "통화 축에 자산이 없음" };
  });

  await record("실물자산으로 전환하면 부동산·원자재만 나오고 3축 탭이 숨는다", async () => {
    await page.locator('button[data-class="physical"]').click();
    const labels = await page.locator(".legend-row .name").allTextContents();
    const unexpected = labels.filter((label) => !["부동산", "원자재·실물자산"].includes(label));
    if (unexpected.length) return { ok: false, reason: `실물자산에 없어야 할 항목: ${unexpected.join(", ")}` };
    const axisTabs = await page.locator("button[data-axis]").count();
    if (axisTabs) return { ok: false, reason: `축 탭이 ${axisTabs}개 남아 있음` };
    const note = await page.locator(".card-note").first().textContent();
    return note.includes("원금 보장·통화 축을 두지 않았습니다") ? { ok: true } : { ok: false, reason: `안내 문구: ${note.slice(0, 60)}` };
  });

  // ===== 5a-1c. 현재가·보유 수량 열 =====
  await record("보유 자산 표에 현재가와 보유 수량이 있고, 단가 개념이 없는 자산군은 비어 있다", async () => {
    await page.goto(HOME_URL);
    await page.waitForFunction(() => document.querySelector("table.data tbody tr"), { timeout: 8000 });
    const headers = await page.locator("table.data thead th").allTextContents();
    for (const want of ["현재가", "보유 수량"]) {
      if (!headers.includes(want)) return { ok: false, reason: `"${want}" 열이 없음: ${headers.join(", ")}` };
    }
    const priceIndex = headers.indexOf("현재가");
    const rows = await page.locator("table.data tbody tr").evaluateAll((nodes, index) =>
      nodes.map((node) => {
        const cells = [...node.querySelectorAll("td")].map((cell) => cell.textContent.trim());
        return { name: cells[0], price: cells[index], qty: cells[index + 1] };
      }), priceIndex);
    const withUnit = ["SK하이닉스", "비트코인", "금"];
    for (const row of rows) {
      const shouldHave = withUnit.includes(row.name);
      const has = row.price !== "–";
      if (shouldHave && !has) return { ok: false, reason: `${row.name}에 현재가가 없음` };
      // 예적금 잔액이나 부동산 추정가에 억지로 단가를 붙이면 없는 개념을 만들어내는 셈이다.
      if (!shouldHave && has) return { ok: false, reason: `${row.name}에 현재가가 억지로 채워짐: ${row.price}` };
      if (shouldHave && row.qty === "–") return { ok: false, reason: `${row.name}에 보유 수량이 없음` };
    }
    return { ok: true };
  });

  // ===== 5a-1d. 보유 자산 카드는 자산이 많아도 높이가 늘지 않는다 =====
  await record("자산 11건에서도 보유 자산 카드 높이가 고정되고 건수는 정확하다", async () => {
    const many = Array.from({ length: 11 }, (_, index) => ({
      id: `m${index}`,
      group: "equity",
      fields: { productName: `종목${index}`, productCode: "005930", quantity: index + 1, averagePrice: 60000 },
      autoFields: { currency: "KRW" },
    }));
    await page.goto(HOME_URL);
    await page.evaluate((assets) => {
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 7, snapshots: [], assets }));
    }, many);
    await page.reload();
    await page.waitForFunction(() => document.querySelector("table.data tbody tr"), { timeout: 8000 });

    const rowCount = await page.locator("table.data tbody tr").count();
    if (rowCount !== 11) return { ok: false, reason: `표에 ${rowCount}행 (11행 기대 — 나머지는 스크롤로 가려질 뿐 빠지면 안 된다)` };

    // 머리말이 전체 건수를 말해야 한다 — 4개만 보인다고 4개라고 적으면 안 된다.
    const sub = await page.locator(".card").filter({ hasText: "보유 자산" }).locator(".card-sub").first().textContent();
    if (!sub.includes("전체 11개")) return { ok: false, reason: `머리말에 전체 건수가 없음: "${sub}"` };

    const box = await page.evaluate(() => {
      const scroller = document.querySelector(".holdings-scroll");
      if (!scroller) return null;
      return { clientHeight: scroller.clientHeight, scrollHeight: scroller.scrollHeight, capped: scroller.classList.contains("capped") };
    });
    if (!box) return { ok: false, reason: "스크롤 영역이 없음" };
    if (!box.capped) return { ok: false, reason: "높이 제한이 걸리지 않음" };
    if (box.scrollHeight <= box.clientHeight) return { ok: false, reason: `내용이 잘리지 않아 스크롤이 생기지 않음 (${box.scrollHeight} <= ${box.clientHeight})` };

    // 자산이 4건일 때는 스크롤이 걸리지 않아야 한다 — 굳이 가둘 이유가 없다.
    await page.evaluate((assets) => {
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 7, snapshots: [], assets: assets.slice(0, 4) }));
    }, many);
    await page.reload();
    await page.waitForFunction(() => document.querySelector("table.data tbody tr"), { timeout: 8000 });
    const cappedWithFew = await page.locator(".holdings-scroll.capped").count();
    return cappedWithFew === 0 ? { ok: true } : { ok: false, reason: "4건뿐인데도 높이가 제한됨" };
  });

  // 날짜가 섞인 데이터를 한 시점의 스냅샷처럼 보여주면 안 된다.
  // 이 검사만 새 컨텍스트에서 돈다 — 앞선 검사들이 남긴 저장 대기(디바운스)와 원격
  // 사본이 같은 페이지에서 계속 되살아나 세션이 엎치락뒤치락했다.
  await record("가상자산 기준일이 이르면 화면이 그 사실을 밝힌다", async () => {
    const fixture = { ...QUOTES_FIXTURE, cryptoAsOf: "2026-08-04" };
    await writeFile(QUOTES_PATH, JSON.stringify(fixture), "utf8");
    const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
    try {
      const fresh = await context.newPage();
      await installTestAuth(fresh);
      await fresh.goto(HOME_URL);
      await seedSession(fresh, [
        { id: "k1", group: "crypto", fields: { productName: "비트코인", productCode: "BTC", quantity: 0.1, averagePrice: 80000000 }, autoFields: { currency: "KRW" } },
      ]);
      await fresh.reload();
      await fresh.waitForFunction(() => {
        const host = document.getElementById("page");
        return host && host.innerText.includes("총자산");
      }, { timeout: 10000 });
      const text = await fresh.locator("#page").innerText();
      if (!text.includes("2026-08-04")) return { ok: false, reason: `가상자산 기준일이 화면에 없음: ${text.slice(0, 200)}` };
      if (!text.includes("가상자산 시세가")) return { ok: false, reason: "안내 문구가 없음" };
      return { ok: true };
    } finally {
      await context.close();
      await writeFile(QUOTES_PATH, JSON.stringify(QUOTES_FIXTURE), "utf8");
    }
  });

  // ===== 5a-1e. 다가오는 일정 (거시 경제 일정) =====
  await record("일정 카드가 상단 행 높이를 늘리지 않는다", async () => {
    await page.goto(HOME_URL);
    await seedSession(page, AXIS_ASSETS.slice(0, 2));
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".schedule-scroll"), { timeout: 9000 });
    const box = await page.evaluate(() => {
      const row = document.querySelector(".grid-top");
      const card = document.querySelector(".schedule-scroll").closest(".card");
      // scrollHeight는 이미 늘어난 높이라 카드가 행을 밀었는지 알 수 없다(늘어난 값이
      // 행 높이와 같아 언제나 통과한다). 잠깐 늘이기를 풀고 내용만의 높이를 잰다.
      const previous = card.style.alignSelf;
      card.style.alignSelf = "start";
      const cardNatural = Math.round(card.getBoundingClientRect().height);
      card.style.alignSelf = previous;
      return { rowH: Math.round(row.getBoundingClientRect().height), cardNatural };
    });
    // 카드 자연 높이가 행 높이를 넘으면 이 카드가 행을 끌어올린다.
    return box.cardNatural <= box.rowH
      ? { ok: true }
      : { ok: false, reason: `일정 카드 자연 높이 ${box.cardNatural} > 행 높이 ${box.rowH}` };
  });

  await record("일정이 카드 안에서 스크롤된다 (접기 토글 없이)", async () => {
    const box = await page.evaluate(() => {
      const scroller = document.querySelector(".schedule-scroll");
      return { client: scroller.clientHeight, scroll: scroller.scrollHeight, items: document.querySelectorAll(".sched-item").length };
    });
    if (box.items < 2) return { ok: false, reason: `일정 ${box.items}건 — 스크롤 검증 전제가 성립하지 않음` };
    if (box.scroll <= box.client) return { ok: false, reason: `내용이 넘치지 않아 스크롤이 없음 (${box.scroll} <= ${box.client})` };
    const details = await page.locator(".schedule-scroll details").count();
    return details === 0 ? { ok: true } : { ok: false, reason: "목록 안에 접기 토글이 있음" };
  });

  await record("미래 일정만 가까운 순으로 나오고 D-day가 KST 기준이다", async () => {
    const info = await page.evaluate(() => {
      const today = new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
      return {
        today,
        ddays: [...document.querySelectorAll(".sched-dday")].map((node) => node.textContent.trim()),
        all: (macro.data.events || []).map((event) => event.datetimeKst.slice(0, 10)),
      };
    });
    const past = info.all.filter((date) => date < info.today);
    if (!past.length) return { ok: false, reason: "달력에 과거 일정이 없어 필터 검증이 성립하지 않음" };
    const future = info.all.filter((date) => date >= info.today).length;
    if (info.ddays.length !== future) return { ok: false, reason: `표시 ${info.ddays.length}건 / 미래 ${future}건` };
    const nums = info.ddays.map((text) => (text === "D-DAY" ? 0 : Number(text.replace("D-", ""))));
    const sorted = nums.every((value, index) => index === 0 || nums[index - 1] <= value);
    return sorted ? { ok: true } : { ok: false, reason: `가까운 순이 아님: ${info.ddays.slice(0, 6).join(", ")}` };
  });

  await record("일정을 누르면 시나리오 두 개가 펼쳐진다 (컴팩트 보기)", async () => {
    await page.locator("button[data-event]").first().click();
    await page.waitForSelector(".sched-body", { timeout: 5000 });
    const labels = await page.locator(".sched-body .scenario b").allTextContents();
    if (labels.length !== 2) return { ok: false, reason: `시나리오 ${labels.length}개 (2개 기대)` };
    const text = await page.locator(".sched-body").innerText();
    // 어느 쪽이 될지 모른다는 것과, 투자 권유가 아니라는 것을 함께 밝혀야 한다.
    return text.includes("어느 쪽이 될지는 알 수 없습니다") ? { ok: true } : { ok: false, reason: "예측이 아니라는 표기가 없음" };
  });

  await record("더보기가 확대 보기를 열고 거기서도 시나리오가 펼쳐진다", async () => {
    await page.locator("[data-schedule-more]").click();
    await page.waitForSelector(".overlay-panel", { timeout: 5000 });
    const inOverlay = await page.locator(".overlay-body .sched-item").count();
    if (inOverlay < 2) return { ok: false, reason: `확대 보기 항목 ${inOverlay}건` };
    await page.locator(".overlay-body button[data-event]").nth(1).click();
    const scenarios = await page.locator(".overlay-body .sched-body .scenario").count();
    if (scenarios !== 2) return { ok: false, reason: `확대 보기 시나리오 ${scenarios}개` };
    await page.locator(".overlay").click({ position: { x: 5, y: 5 } });
    await page.waitForFunction(() => !document.querySelector(".overlay-panel"), { timeout: 5000 });
    return { ok: true };
  });

  await record("시나리오 문구에 매매 지시·보유 자산 언급·예측이 없다", async () => {
    const events = await page.evaluate(() => macro.data.events);
    const forbidden = /(매수|매도|사세요|파세요|줄이세요|늘리세요|비중을 조정|리밸런싱|보유하신|귀하의|확률|가능성이 높|전망합니다|예상됩니다)/;
    const hits = [];
    events.forEach((event) => (event.scenarios || []).forEach((scenario) => {
      if (forbidden.test(scenario.text) || forbidden.test(scenario.label)) hits.push(`${event.id}/${scenario.label}`);
    }));
    if (hits.length) return { ok: false, reason: `금지 표현: ${hits.slice(0, 3).join(", ")}` };
    const bad = events.filter((event) => !Array.isArray(event.scenarios) || event.scenarios.length !== 2);
    return bad.length ? { ok: false, reason: `시나리오가 2개가 아닌 일정 ${bad.length}건` } : { ok: true };
  });

  await record("FOMC 시각이 미국 날짜가 아니라 KST 다음 날 새벽으로 저장돼 있다", async () => {
    const fomc = await page.evaluate(() => macro.data.events.filter((event) => event.org === "FOMC").slice(0, 6));
    for (const event of fomc) {
      const hour = Number(event.datetimeKst.slice(11, 13));
      // 14:00 ET는 서머타임이면 03:00, 아니면 04:00 KST — 어느 쪽이든 다음 날 새벽이다.
      if (hour !== 3 && hour !== 4) return { ok: false, reason: `${event.id} 시각 ${event.datetimeKst}` };
      const usDate = /(\d{4}-\d{2}-\d{2})/.exec(event.note || "")?.[1];
      if (usDate && event.datetimeKst.slice(0, 10) <= usDate) {
        return { ok: false, reason: `${event.id}: KST 날짜(${event.datetimeKst.slice(0, 10)})가 미국 날짜(${usDate})보다 뒤가 아님` };
      }
    }
    return { ok: true };
  });

  // 만료는 반드시 시끄럽게. 빈 카드나 "일정이 없습니다"로 넘어가면 안 된다.
  await record("coverageUntil을 지나면 갱신이 필요하다고 명시한다", async () => {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    try {
      const fresh = await context.newPage();
      await installTestAuth(fresh);
      await fresh.route("**/data/macro-calendar.json", (route) => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          coverageUntil: "2020-12-31",
          coverage: { FOMC: "2020-12-31", BOK: "2020-12-31" },
          sources: {},
          events: [{ id: "fomc-2020-12-16", datetimeKst: "2020-12-17T04:00:00+09:00", org: "FOMC", title: "미국 FOMC 정책금리 결정", scenarios: [{ label: "a", text: "a" }, { label: "b", text: "b" }] }],
        }),
      }));
      await fresh.goto(HOME_URL);
      await seedSession(fresh, AXIS_ASSETS.slice(0, 2));
      await fresh.reload();
      await fresh.waitForFunction(() => {
        const host = document.getElementById("page");
        return host && host.innerText.includes("다가오는 일정");
      }, { timeout: 9000 });
      const card = await fresh.locator(".card").filter({ hasText: "다가오는 일정" }).innerText();
      if (!/갱신|업데이트/.test(card)) return { ok: false, reason: `갱신 필요 표기가 없음: ${card.slice(0, 160)}` };
      if (!card.includes("2020-12-31")) return { ok: false, reason: `coverageUntil 날짜가 없음: ${card.slice(0, 160)}` };
      if (/일정이 없습니다|예정된 일정이 없어요/.test(card)) return { ok: false, reason: "일반 문구로 대체됨" };
      if (await fresh.locator(".schedule-scroll").count()) return { ok: false, reason: "만료 상태인데 목록이 그려짐" };
      return { ok: true };
    } finally {
      await context.close();
    }
  });

  // ===== 5a-1e-2. 주별·월별 보기와 필터 =====
  //
  // 이 카드는 상단 3열 행의 높이를 정하지 않는다는 것이 원래 조건이다. 보기 전환
  // 버튼 한 줄을 더했으니 행 높이를 값으로 못 박는다 — "카드 <= 행"만 보면 카드가
  // 커지면서 행도 같이 커지는 경우를 잡지 못한다.
  // 상단 행이 지켜야 하는 것은 픽셀 값이 아니라 두 가지 구조다.
  //   1) 열 비율이 선언한 1.35 : 1 : 1 그대로여야 한다. 격자 칸에 min-width:0이 없으면
  //      열이 8개인 표나 긴 문구가 칸을 밀어내 비율이 무너진다(실측 404/299/377px로
  //      찌그러져 있었다 → 435/322/322로 회복).
  //   2) 일정 카드가 총자산 카드보다 커지면 이 카드가 행 높이를 정하게 된다.
  // 높이를 숫자로 못 박았더니 세션에 어떤 자산이 들었는지에 따라 값이 흔들려서
  // (확인이 필요한 항목 줄이 있고 없고에 따라 15px) 구조 자체를 검사하도록 바꿨다.
  await record("상단 3열 구조가 유지된다 (열 비율 1.35:1:1 · 일정 카드가 행을 밀지 않음)", async () => {
    await page.goto(HOME_URL);
    await seedSession(page, AXIS_ASSETS.slice(0, 2));
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".schedule-scroll"), { timeout: 9000 });
    // 웹폰트가 늦게 도착하면 글자 폭·행간이 바뀌어 높이가 흔들린다. 교체가 끝난 뒤 잰다.
    await page.evaluate(() => document.fonts.ready);
    const box = await page.evaluate(() => {
      const row = document.querySelector(".grid-top");
      const card = document.querySelector(".schedule-scroll").closest(".card");
      // scrollHeight는 이미 늘어난 높이라 카드가 행을 밀었는지 알 수 없다(늘어난 값이
      // 행 높이와 같아 언제나 통과한다). 잠깐 늘이기를 풀고 내용만의 높이를 잰다.
      const previous = card.style.alignSelf;
      card.style.alignSelf = "start";
      const schedule = Math.round(card.getBoundingClientRect().height);
      card.style.alignSelf = previous;
      return {
        rowH: Math.round(row.getBoundingClientRect().height),
        schedule,
        columns: getComputedStyle(row).gridTemplateColumns.split(" ").map(parseFloat),
        scheduleCap: getComputedStyle(document.querySelector(".schedule-scroll")).maxHeight,
      };
    });
    const [wide, mid, right] = box.columns;
    if (box.columns.length !== 3) return { ok: false, reason: `상단이 3열이 아님: ${box.columns.join(" / ")}` };
    // 1fr짜리 두 열은 서로 같아야 하고, 첫 열은 그 1.35배여야 한다.
    if (Math.abs(mid - right) > 1) return { ok: false, reason: `1fr 두 열이 다름: ${mid} / ${right}` };
    const ratio = wide / mid;
    if (Math.abs(ratio - 1.35) > 0.02) return { ok: false, reason: `첫 열 비율 ${ratio.toFixed(3)} (1.35 기대) — 내용이 열을 밀고 있음` };
    if (box.scheduleCap !== "108px") return { ok: false, reason: `일정 목록 높이 상한 ${box.scheduleCap} (108px 기대)` };
    return box.schedule < box.rowH
      ? { ok: true }
      : { ok: false, reason: `일정 카드(${box.schedule}px)가 행 높이(${box.rowH}px)를 정하고 있음` };
  });

  await record("주별·월별로 묶어서 볼 수 있다 (컴팩트 카드)", async () => {
    const flat = await page.locator(".schedule-scroll .sched-group").count();
    if (flat !== 0) return { ok: false, reason: "기본값이 가까운 순인데 묶음 머리글이 있음" };

    await page.locator(".schedule-scroll").isVisible();
    await page.locator("button[data-sched-mode='week']").first().click();
    const weekHeads = await page.locator(".schedule-scroll .sched-group-head").allTextContents();
    if (!weekHeads.length) return { ok: false, reason: "주별 묶음 머리글이 없음" };
    // "8월 17일~23일" 또는 달을 넘는 "8월 31일~9월 6일"
    if (!/^\d{1,2}월 \d{1,2}일~(\d{1,2}월 )?\d{1,2}일/.test(weekHeads[0])) {
      return { ok: false, reason: `주 라벨 형식이 아님: ${weekHeads[0]}` };
    }

    await page.locator("button[data-sched-mode='month']").first().click();
    const monthHeads = await page.locator(".schedule-scroll .sched-group-head").allTextContents();
    if (!/^\d{4}년 \d{1,2}월/.test(monthHeads[0])) return { ok: false, reason: `월 라벨 형식이 아님: ${monthHeads[0]}` };
    // 묶어도 일정이 사라지면 안 된다.
    const grouped = await page.locator(".schedule-scroll .sched-item").count();
    await page.locator("button[data-sched-mode='near']").first().click();
    const flatCount = await page.locator(".schedule-scroll .sched-item").count();
    return grouped === flatCount ? { ok: true } : { ok: false, reason: `월별 ${grouped}건 / 가까운 순 ${flatCount}건` };
  });

  await record("주 경계가 달을 넘어가도 같은 주로 묶인다", async () => {
    // 화면의 묶음 로직을 그대로 불러 확인한다. 달 경계(8/31 월요일 ~ 9/6 일요일)에서
    // 두 날짜가 같은 주로 가야 하고, 라벨은 양쪽 달을 다 적어야 한다.
    const probe = await page.evaluate(() => ({
      aug31: weekStart("2026-08-31"), sep06: weekStart("2026-09-06"), sep07: weekStart("2026-09-07"),
      label: weekLabel(weekStart("2026-08-31")),
      sameMonthLabel: weekLabel(weekStart("2026-09-08")),
    }));
    if (probe.aug31 !== probe.sep06) return { ok: false, reason: `8/31과 9/6이 다른 주: ${probe.aug31} / ${probe.sep06}` };
    if (probe.sep07 === probe.aug31) return { ok: false, reason: "9/7(월)이 앞 주에 묶임" };
    if (probe.aug31 !== "2026-08-31") return { ok: false, reason: `주 시작이 월요일이 아님: ${probe.aug31}` };
    if (!probe.label.includes("9월")) return { ok: false, reason: `달을 넘는 주인데 라벨에 9월이 없음: ${probe.label}` };
    if (/월.*월/.test(probe.sameMonthLabel)) return { ok: false, reason: `같은 달 안의 주인데 달을 두 번 적음: ${probe.sameMonthLabel}` };
    return { ok: true };
  });

  await record("확대 보기에서 국가·중요도 필터가 묶음 방식과 함께 걸린다", async () => {
    await page.locator("[data-schedule-more]").click();
    await page.waitForSelector(".overlay-panel", { timeout: 5000 });
    await page.locator(".overlay-panel button[data-sched-mode='month']").click();

    const all = await page.locator(".overlay-body .sched-item").count();
    await page.locator(".overlay-panel button[data-sched-filter='국가'][data-value='KOR']").click();
    const korOnly = await page.evaluate(() => [...document.querySelectorAll(".overlay-body .sched-org")].map((node) => node.textContent.trim()));
    if (!korOnly.length || korOnly.some((label) => /미 |ISM|FOMC|센서스/.test(label))) {
      return { ok: false, reason: `한국 필터에 미국 기관이 섞임: ${[...new Set(korOnly)].join(",")}` };
    }
    // 묶음 방식은 필터를 걸어도 유지돼야 한다.
    if (!(await page.locator(".overlay-body .sched-group-head").count())) return { ok: false, reason: "필터 후 월별 묶음이 풀림" };

    await page.locator(".overlay-panel button[data-sched-filter='중요도'][data-value='high']").click();
    const both = await page.evaluate(() => {
      const ids = [...document.querySelectorAll(".overlay-body button[data-event]")].map((node) => node.dataset.event);
      const byId = new Map(macro.data.events.map((event) => [event.id, event]));
      return ids.map((id) => byId.get(id)).filter(Boolean).map((event) => `${event.country}/${event.importance}`);
    });
    if (!both.length) return { ok: false, reason: "한국+중요 조합이 0건" };
    if (both.some((pair) => pair !== "KOR/high")) return { ok: false, reason: `조합이 안 걸림: ${[...new Set(both)].join(",")}` };
    if (both.length >= all) return { ok: false, reason: `필터 후 ${both.length}건이 전체 ${all}건 이상` };
    return { ok: true };
  });

  await record("필터로 0건이 되면 조건 때문이라고 밝힌다", async () => {
    // 한국 + 보통 중요도가 0건이 되는 조합은 없으므로, 존재하지 않는 조합을 직접 만든다.
    const emptied = await page.evaluate(() => {
      scheduleCountry = "USA"; scheduleImportance = "none-such"; render();
      return document.querySelector(".overlay-body").innerText;
    });
    if (/^\s*$/.test(emptied)) return { ok: false, reason: "빈 화면으로 남음" };
    if (!/고른 조건/.test(emptied)) return { ok: false, reason: `조건 때문이라는 설명이 없음: ${emptied.slice(0, 120)}` };
    if (!/\d+건/.test(emptied)) return { ok: false, reason: "남은 전체 건수를 적지 않음" };
    // 만료 문구로 오해되면 안 된다 — 데이터가 낡은 것과 조건이 좁은 것은 다른 상황이다.
    if (/갱신 필요|coverageUntil/.test(emptied)) return { ok: false, reason: "만료 안내로 잘못 표시됨" };
    await page.evaluate(() => { scheduleCountry = "all"; scheduleImportance = "all"; render(); });
    return { ok: true };
  });

  await record("고른 보기 방식이 확대 보기를 닫았다 열어도 남는다", async () => {
    await page.locator(".overlay").click({ position: { x: 5, y: 5 } });
    await page.waitForFunction(() => !document.querySelector(".overlay-panel"), { timeout: 5000 });
    // 확대 보기에서 월별로 바꿨으니 컴팩트 카드도 월별이어야 한다(같은 상태를 본다).
    const compact = await page.locator(".schedule-scroll .sched-group-head").first().textContent();
    if (!/^\d{4}년/.test(compact || "")) return { ok: false, reason: `컴팩트가 월별이 아님: ${compact}` };
    await page.locator("[data-schedule-more]").click();
    await page.waitForSelector(".overlay-panel", { timeout: 5000 });
    const pressed = await page.locator(".overlay-panel button[data-sched-mode='month']").getAttribute("aria-pressed");
    await page.locator(".overlay-panel button[data-sched-mode='near']").click();
    await page.locator(".overlay").click({ position: { x: 5, y: 5 } });
    await page.waitForFunction(() => !document.querySelector(".overlay-panel"), { timeout: 5000 });
    return pressed === "true" ? { ok: true } : { ok: false, reason: `다시 열었더니 월별이 아님 (aria-pressed=${pressed})` };
  });

  // 시간대 환산은 이 파일에서 가장 틀리기 쉬운 곳이다. 하나의 오프셋을 통째로 적용하면
  // 08:30 ET와 14:00 ET 중 한쪽이 반드시 하루 어긋난다.
  await record("08:30 ET는 같은 KST 날짜, 14:00 ET는 다음 KST 날짜로 저장된다", async () => {
    const info = await page.evaluate(() => {
      const rows = macro.data.events
        .map((event) => ({ id: event.id, kst: event.datetimeKst, us: /현지 (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2})/.exec(event.note || "") }))
        .filter((row) => row.us);
      return rows.map((row) => ({ id: row.id, kstDate: row.kst.slice(0, 10), kstHour: row.kst.slice(11, 16), usDate: row.us[1], usTime: row.us[2] }));
    });
    const morning = info.filter((row) => row.usTime === "08:30");
    const fomc = info.filter((row) => row.usTime === "14:00");
    if (!morning.length || !fomc.length) return { ok: false, reason: `표본 부족: 08:30 ${morning.length}건 / 14:00 ${fomc.length}건` };

    const badMorning = morning.filter((row) => row.kstDate !== row.usDate || !["21:30", "22:30"].includes(row.kstHour));
    if (badMorning.length) return { ok: false, reason: `08:30 ET 환산 오류: ${badMorning[0].id} ${badMorning[0].usDate} -> ${badMorning[0].kstDate} ${badMorning[0].kstHour}` };
    const badFomc = fomc.filter((row) => row.kstDate <= row.usDate || !["03:00", "04:00"].includes(row.kstHour));
    if (badFomc.length) return { ok: false, reason: `14:00 ET 환산 오류: ${badFomc[0].id} ${badFomc[0].usDate} -> ${badFomc[0].kstDate} ${badFomc[0].kstHour}` };

    // 서머타임 경계(2026-11-01)를 사이에 두고 실제로 값이 갈리는지 — 하나만 맞고
    // 나머지가 우연히 맞는 경우를 배제한다.
    const summer = morning.find((row) => row.usDate < "2026-11-01");
    const winter = morning.find((row) => row.usDate > "2026-11-01");
    if (!summer || !winter) return { ok: false, reason: "서머타임 경계 양쪽 표본이 없음" };
    if (summer.kstHour === winter.kstHour) return { ok: false, reason: `서머타임 경계 양쪽이 같은 시각(${summer.kstHour}) — 고정 오프셋을 쓴 것` };
    return { ok: true };
  });

  await record("규칙으로 잡은 예정일은 공표된 일정과 구별해 표시한다", async () => {
    const counts = await page.evaluate(() => {
      const rule = macro.data.events.filter((event) => event.dateBasis === "rule");
      return { rule: rule.length, published: macro.data.events.filter((event) => event.dateBasis === "published").length };
    });
    if (!counts.rule || !counts.published) return { ok: false, reason: `표본 부족: 규칙 ${counts.rule}건 / 공표 ${counts.published}건` };
    const marks = await page.locator(".schedule-scroll .sched-approx").count();
    const items = await page.locator(".schedule-scroll .sched-item").count();
    if (!marks) return { ok: false, reason: "예정 표시가 하나도 없음" };
    return marks < items ? { ok: true } : { ok: false, reason: "모든 일정이 예정으로 표시됨 — 구별이 안 됨" };
  });

  await record("확대한 범위가 두 나라·다섯 종류를 실제로 담고 있다", async () => {
    const shape = await page.evaluate(() => {
      const events = macro.data.events;
      return {
        countries: [...new Set(events.map((event) => event.country))].sort(),
        categories: [...new Set(events.map((event) => event.category))].sort(),
        importances: [...new Set(events.map((event) => event.importance))].sort(),
        missing: events.filter((event) => !event.country || !event.category || !event.importance).length,
      };
    });
    if (shape.missing) return { ok: false, reason: `분류가 빠진 일정 ${shape.missing}건` };
    if (shape.countries.join() !== "KOR,USA") return { ok: false, reason: `국가: ${shape.countries.join()}` };
    for (const category of ["rate", "inflation", "employment", "growth", "trade"]) {
      if (!shape.categories.includes(category)) return { ok: false, reason: `분류 누락: ${category}` };
    }
    if (shape.importances.join() !== "high,medium") return { ok: false, reason: `중요도: ${shape.importances.join()}` };
    return { ok: true };
  });

  // ===== 5a-1g. 배치 산출물 캐시 =====
  //
  // 배치는 커밋했고 배포도 됐는데 브라우저가 어제 파일을 들고 있던 적이 있다. 화면에는
  // 배치가 고장 난 것과 똑같이 보여서 파이프라인 쪽을 며칠 뒤졌다. 손으로 캐시를
  // 비껴가지 않고도 새 값이 오는지를 못 박는다.
  await record("배치가 파일을 다시 쓰면 다음 두 번의 로드가 모두 새 값을 본다", async () => {
    const saved = await readFile(QUOTES_PATH, "utf8");
    // 서버가 If-None-Match를 실제로 다루도록 캐시를 허용한 새 컨텍스트를 쓴다.
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    try {
      const fresh = await context.newPage();
      await installTestAuth(fresh);
      await fresh.goto(HOME_URL);
      await seedSession(fresh, AXIS_ASSETS.slice(0, 2));

      const read = async () => {
        await fresh.reload();
        await fresh.waitForFunction(() => window.Valuation && Valuation.state.status !== "loading", { timeout: 12000 });
        return fresh.evaluate(() => ({
          asOf: Valuation.asOfDate(),
          info: Valuation.loadInfo(),
        }));
      };

      // 1) 먼저 한 번 읽어 브라우저 캐시에 넣는다.
      const before = await read();
      if (before.asOf !== ASOF_DATE) return { ok: false, reason: `첫 로드 asOf ${before.asOf}` };

      // 2) 배치가 파일을 다시 쓴 상황을 만든다(날짜만 하루 뒤로).
      const next = JSON.parse(saved);
      next.asOf = "2026-08-08T00:00:00+09:00";
      await writeFile(QUOTES_PATH, JSON.stringify(next), "utf8");

      // 3) 연속 두 번. 캐시 무력화 질의를 손으로 붙이지 않는다.
      const first = await read();
      const second = await read();
      if (first.asOf !== "2026-08-08") return { ok: false, reason: `갱신 후 첫 로드가 옛 값: ${first.asOf}` };
      if (second.asOf !== "2026-08-08") return { ok: false, reason: `갱신 후 두 번째 로드가 옛 값: ${second.asOf}` };
      // 캐시에서 그냥 나온 것이 아니라 실제로 서버에 물어봤어야 한다.
      if (first.info && first.info.known && first.info.fromCache) {
        return { ok: false, reason: "새 값인데 네트워크를 타지 않았다고 보고됨" };
      }
      return { ok: true };
    } finally {
      await writeFile(QUOTES_PATH, saved, "utf8");
      await context.close();
    }
  });

  await record("자주 바뀌는 데이터는 조건부 요청, 종목 목록은 기본 캐시", async () => {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    try {
      const fresh = await context.newPage();
      await installTestAuth(fresh);
      // 실제로 나가는 요청의 캐시 모드를 기록한다. 요청 헤더는 브라우저가 캐시 항목을
      // 갖고 있을 때만 붙으므로, 헤더가 아니라 fetch에 넘긴 모드를 본다.
      await fresh.addInitScript(() => {
        window.__cacheModes = {};
        const original = window.fetch;
        window.fetch = function (input, init) {
          const url = String(input && input.url ? input.url : input);
          if (url.includes("/data/")) {
            window.__cacheModes[url.replace(/^.*\/data\//, "data/")] = (init && init.cache) || "(기본)";
          }
          return original.call(this, input, init);
        };
      });

      await fresh.goto(HOME_URL);
      await seedSession(fresh, AXIS_ASSETS.slice(0, 2));
      await fresh.reload();
      await fresh.waitForFunction(() => window.Valuation && Valuation.state.status !== "loading", { timeout: 12000 });
      await fresh.waitForTimeout(1500);
      const home = await fresh.evaluate(() => window.__cacheModes);

      if (home["data/quotes.json"] !== "no-cache") {
        return { ok: false, reason: `quotes.json 모드 ${home["data/quotes.json"]} (no-cache 기대)` };
      }
      if (home["data/macro-calendar.json"] !== "no-cache") {
        return { ok: false, reason: `macro-calendar.json 모드 ${home["data/macro-calendar.json"]}` };
      }
      if (home["data/indicators/index.json"] !== "no-cache") {
        return { ok: false, reason: `indicators/index.json 모드 ${home["data/indicators/index.json"]}` };
      }

      // 종목 목록은 주 1회 바뀌고 합쳐서 4MB다. 매 로드마다 조건부 요청을 더 보낼 이유가 없다.
      await fresh.goto(`http://127.0.0.1:${PORT}/asset-input.html`);
      await fresh.waitForTimeout(2500);
      const input = await fresh.evaluate(() => window.__cacheModes);
      const ticker = Object.keys(input).find((path) => path.startsWith("data/tickers-"));
      if (!ticker) return { ok: false, reason: `종목 파일 요청이 관찰되지 않음: ${Object.keys(input).join(", ")}` };
      return input[ticker] === "(기본)"
        ? { ok: true }
        : { ok: false, reason: `${ticker} 모드 ${input[ticker]} (기본 캐시 기대)` };
    } finally { await context.close(); }
  });

  await record("캐시에서 나온 낡은 시세는 배치 문제와 구별해 알린다", async () => {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    try {
      const fresh = await context.newPage();
      await installTestAuth(fresh);
      // 캐시에서 그대로 나온 상황을 만든다. DataFetch가 정의되는 순간 loadInfo만 바꾼다.
      await fresh.addInitScript(() => {
        let real;
        Object.defineProperty(window, "DataFetch", {
          configurable: true,
          get() { return real; },
          set(value) {
            real = value;
            value.loadInfo = () => ({ known: true, fromCache: true, transferSize: 0 });
          },
        });
      });
      await fresh.goto(HOME_URL);
      await seedSession(fresh, AXIS_ASSETS.slice(0, 2));
      await fresh.reload();
      await fresh.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 12000 });
      const text = await fresh.locator("#app").innerText();
      if (!/브라우저 캐시/.test(text)) return { ok: false, reason: `캐시 안내가 없음: ${text.slice(0, 200)}` };
      if (!/새로고침/.test(text)) return { ok: false, reason: "다음에 무엇을 할지 알려주지 않음" };
      // 배치가 멈춘 것과 같은 문구로 뭉쳐 놓으면 원인을 가릴 수 없다.
      if (!/캐시에서 나왔어요|브라우저 캐시에서/.test(text)) return { ok: false, reason: "출처를 밝히지 않음" };
      return { ok: true };
    } finally { await context.close(); }
  });

  // ===== 5a-1f. 경제지표 (OECD) =====
  await record("경제지표 카드가 값과 관측 기간을 함께 보여주고 행을 늘리지 않는다", async () => {
    await page.goto(HOME_URL);
    await seedSession(page, AXIS_ASSETS.slice(0, 2));
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".ind-rows"), { timeout: 12000 });
    const box = await page.evaluate(() => {
      const row = document.querySelector(".grid-mid");
      const card = document.querySelector(".ind-rows").closest(".card");
      const style = getComputedStyle(card);
      // 카드는 그리드에서 늘어나므로 실제 높이가 아니라 내용 높이로 비교해야 한다.
      const content = [...card.children].reduce((sum, el) => sum + el.getBoundingClientRect().height, 0)
        + parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
      return {
        rowH: Math.round(row.getBoundingClientRect().height),
        content: Math.round(content),
        rows: document.querySelectorAll(".ind-row").length,
        periods: [...document.querySelectorAll(".ind-row-period")].map((node) => node.textContent.trim()),
      };
    });
    if (!box.rows) return { ok: false, reason: "지표 행이 없음" };
    // 값마다 관측 기간이 반드시 붙어야 한다 — 몇 달 전 값도 그 시점의 정상 데이터다.
    if (box.periods.length !== box.rows) return { ok: false, reason: `기간 표기 ${box.periods.length} / 행 ${box.rows}` };
    if (box.periods.some((text) => !/\d{4}년/.test(text))) return { ok: false, reason: `기간 형식 이상: ${box.periods.join(", ")}` };
    return box.content <= box.rowH ? { ok: true } : { ok: false, reason: `지표 카드 내용 ${box.content} > 행 ${box.rowH}` };
  });

  await record("더보기가 경제지표 탭으로 가고, 한국·미국이 같은 계열에서 값을 받는다", async () => {
    await page.locator(".card").filter({ hasText: "주요 경제지표" }).locator("a.btn").click();
    await page.waitForURL(/indicators\.html/, { timeout: 8000 });
    await page.waitForSelector(".topic-tab", { timeout: 12000 });
    const tabs = await page.locator(".topic-tab").count();
    if (tabs < 5) return { ok: false, reason: `갈래 탭 ${tabs}개 (5개 이상 기대)` };
    // 이 출처를 고른 이유가 국가 간 비교 가능성이다 — 둘이 같은 계열에서 나와야 한다.
    const both = await page.evaluate(async () => {
      await Indicators.loadTopic("cycle");
      const kor = Indicators.find("cli", "KOR");
      const usa = Indicators.find("cli", "USA");
      return { kor: kor && kor.value, usa: usa && usa.value };
    });
    return Number.isFinite(both.kor) && Number.isFinite(both.usa)
      ? { ok: true } : { ok: false, reason: `KOR/USA 값 없음: ${JSON.stringify(both)}` };
  });

  await record("검색이 한글·영문, 지표명·국가명 모두에 걸린다", async () => {
    for (const needle of ["실업", "Unemployment", "대한민국", "Korea"]) {
      const hits = await page.evaluate((query) => Indicators.search(query).length, needle);
      if (!hits) return { ok: false, reason: `"${needle}" 검색 결과 0건` };
    }
    await page.locator("[data-search]").fill("Korea");
    await page.waitForFunction(() => document.querySelectorAll(".ind-cell").length > 0, { timeout: 8000 });
    const names = await page.evaluate(() => [...document.querySelectorAll(".ind-cell .ind-name")].map((n) => n.textContent));
    await page.locator("[data-search]").fill("");
    return names.length && names.every((name) => name.includes("대한민국"))
      ? { ok: true } : { ok: false, reason: `한국 아닌 결과 섞임: ${[...new Set(names)].slice(0, 3).join(" / ")}` };
  });

  // 3.4: 검색은 전체 카탈로그를 훑는다. 기본 브라우즈 트리는 갈래마다 지표를 접어 두므로,
  // 화면에 열려 있지 않은 지표가 검색으로 나오는지가 이 검사의 요점이다.
  await record("기본 화면에 열려 있지 않은 지표도 검색으로 찾힌다", async () => {
    const probe = await page.evaluate(() => {
      const visible = [...document.querySelectorAll(".ind-cell .ind-name")].map((n) => n.textContent).join(" ");
      const target = Indicators.indicatorList().find((entry) => entry.nameKo.includes("실질실효환율"));
      return { target: target ? target.id : null, shown: visible.includes("실질실효환율") };
    });
    if (!probe.target) return { ok: false, reason: "실질실효환율 지표가 카탈로그에 없음" };
    if (probe.shown) return { ok: false, reason: "이미 화면에 보이는 지표라 검증이 성립하지 않음" };
    await page.locator("[data-search]").fill("실질실효환율");
    await page.waitForFunction(() => document.querySelectorAll(".ind-cell").length > 0, { timeout: 8000 });
    const found = await page.evaluate(() => [...document.querySelectorAll(".ind-cell .ind-name")]
      .some((n) => n.textContent.includes("실질실효환율")));
    await page.locator("[data-search]").fill("");
    return found ? { ok: true } : { ok: false, reason: "검색 결과에 없음" };
  });

  await record("경제지표 검색 중 한글 IME 조합과 입력 DOM이 유지된다", async () => {
    const result = await page.evaluate(() => {
      const box = document.querySelector("[data-search]");
      box.dataset.imeProbe = "same-node";
      box.focus();
      box.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true, data: "" }));
      for (const value of ["ㅇ", "일", "일ㅂ", "일보", "일보ㄴ", "일본"]) {
        box.value = value;
        box.dispatchEvent(new InputEvent("input", { bubbles: true, data: value.at(-1), isComposing: true, inputType: "insertCompositionText" }));
      }
      box.dispatchEvent(new CompositionEvent("compositionend", { bubbles: true, data: "일본" }));
      const current = document.querySelector("[data-search]");
      return {
        value: current.value,
        sameNode: current === box && current.dataset.imeProbe === "same-node",
        names: [...document.querySelectorAll(".ind-cell .ind-name")].map((node) => node.textContent.trim()).slice(0, 3),
      };
    });
    await page.locator("[data-search]").fill("");
    return result.value === "일본" && result.sameNode && result.names.some((name) => name.includes("일본"))
      ? { ok: true }
      : { ok: false, reason: JSON.stringify(result) };
  });

  // 3.4: 갈래 우선과 국가 우선이 같은 계열에 닿아야 한다. 두 경로에서 같은 값이 나오는지 본다.
  await record("갈래 우선과 국가 우선이 같은 값에 닿는다", async () => {
    await page.locator(".topic-tab").filter({ hasText: "경기" }).click();
    // 갈래를 바꾸면 주제 파일을 새로 받아 다시 그린다. 그리기가 끝난 뒤에 눌러야
    // 클릭이 옛 DOM에 떨어지지 않는다.
    await page.waitForSelector("[data-indicator=cli]", { timeout: 8000 });
    await page.locator("[data-indicator=cli]").click();
    await page.waitForFunction(() => {
      const cells = [...document.querySelectorAll(".ind-grid .ind-cell")];
      return cells.length > 0 && cells.some((node) => !node.querySelector(".skel"));
    }, { timeout: 10000 });
    const viaTopic = await page.evaluate(() => {
      const cell = [...document.querySelectorAll(".ind-grid .ind-cell")]
        .find((node) => node.querySelector(".ind-name").textContent.trim() === "대한민국");
      return cell ? cell.querySelector(".ind-value").textContent.trim() : null;
    });
    if (!viaTopic) {
      const names = await page.evaluate(() => [...document.querySelectorAll(".ind-grid .ind-cell .ind-name")].map((n) => n.textContent.trim()).slice(0, 6));
      return { ok: false, reason: `갈래 보기에서 대한민국 셀을 찾지 못함 · 보이는 이름: ${names.join(", ")}` };
    }

    await page.locator("[data-mode=country]").click();
    await page.waitForFunction(() => document.querySelectorAll(".pivot-topic").length > 0, { timeout: 8000 });
    const viaCountry = await page.evaluate(() => {
      const cell = [...document.querySelectorAll(".ind-cell")]
        .find((node) => node.querySelector(".ind-name").textContent.includes("경기선행지수"));
      return cell ? cell.querySelector(".ind-value").textContent.trim() : null;
    });
    await page.locator("[data-mode=topic]").click();
    return viaTopic === viaCountry
      ? { ok: true } : { ok: false, reason: `갈래 ${viaTopic} ≠ 국가 ${viaCountry}` };
  });

  await record("모든 값에 관측 기간과 주기가 함께 표시된다", async () => {
    await page.locator(".topic-tab").first().click();
    await page.locator(".ind-head").first().click();
    await page.waitForSelector(".ind-cell", { timeout: 8000 });
    const bad = await page.evaluate(() => [...document.querySelectorAll(".ind-cell")]
      .filter((cell) => !/\d{4}년/.test(cell.innerText) || !/(월간|분기|연간)/.test(cell.innerText)).length);
    return bad === 0 ? { ok: true } : { ok: false, reason: `기간·주기 누락 셀 ${bad}개` };
  });

  await record("OECD 출처 표기가 지표 화면에 있다", async () => {
    const text = await page.locator(".sources").innerText();
    return /OECD/.test(text) ? { ok: true } : { ok: false, reason: `출처 표기: ${text.slice(0, 120)}` };
  });

  // 3.7: 없거나 낡으면 빈 화면이 아니라 무엇이 없는지 말한다.
  await record("카탈로그가 없으면 무엇이 없는지 명시한다", async () => {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    try {
      const fresh = await context.newPage();
      await installTestAuth(fresh);
      await fresh.route("**/data/indicators/index.json", (route) => route.fulfill({ status: 404, body: "" }));
      await fresh.goto(`http://127.0.0.1:${PORT}/indicators.html`);
      await fresh.waitForSelector(".card", { timeout: 9000 });
      const text = await fresh.locator("#page").innerText();
      if (!/index\.json/.test(text)) return { ok: false, reason: `무엇이 없는지 밝히지 않음: ${text.slice(0, 140)}` };
      if (!/불러오지 못했|확인 필요/.test(text)) return { ok: false, reason: "실패 표기가 없음" };
      return { ok: true };
    } finally { await context.close(); }
  });

  await record("카탈로그가 오래되면 홈 카드가 그 사실을 말한다", async () => {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    try {
      const fresh = await context.newPage();
      await installTestAuth(fresh);
      await fresh.route("**/data/indicators/index.json", (route) => route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          newestPeriod: "2019-01", updatedAt: "2019-02-01T00:00:00Z", attribution: "OECD",
          countries: { KOR: { ko: "대한민국", en: "Korea" } },
          topics: [{ id: "cycle", nameKo: "경기", nameEn: "Cycle", file: "latest/cycle.json", count: 1 }],
          indicators: [{ id: "cli", source: "OECD", topic: "cycle", nameKo: "경기선행지수", nameEn: "CLI", unitKo: "지수", freq: "M", headline: true, file: "oecd/DF_CLI.json", countries: ["KOR"] }],
          headlineSeries: { "oecd:cli:KOR": { indicator: "cli", country: "KOR", period: "2019-01", value: 100 } },
          failures: [],
        }),
      }));
      await fresh.goto(HOME_URL);
      await seedSession(fresh, AXIS_ASSETS.slice(0, 2));
      await fresh.reload();
      await fresh.waitForFunction(() => {
        const host = document.getElementById("page");
        return host && host.innerText.includes("주요 경제지표");
      }, { timeout: 12000 });
      const card = await fresh.locator(".card").filter({ hasText: "주요 경제지표" }).innerText();
      return /오래됨|2019-01/.test(card) ? { ok: true } : { ok: false, reason: `홈 카드에 경고 없음: ${card.slice(0, 140)}` };
    } finally { await context.close(); }
  });

  // 위 검사들이 세션을 바꿨으므로 자산군 탭 검사 전에 되돌린다.
  await page.goto(HOME_URL);
  await seedSession(page, AXIS_ASSETS);

  // ===== 5a-2. 자산군 탭 =====
  await record("자산군 탭 8개가 모두 열리고 해당 자산만 나온다", async () => {
    await page.goto(`http://127.0.0.1:${PORT}/assets.html`);
    await page.waitForSelector(".group-tabs", { timeout: 5000 });
    const tabs = await page.locator(".group-tab").allTextContents();
    if (tabs.length !== 8) return { ok: false, reason: `탭 ${tabs.length}개 (8개 기대): ${tabs.join(", ")}` };
    const expected = await page.evaluate(() => {
      const summary = Portfolio.summarize(session ? session.assets : SessionStore.read().assets);
      return summary.rows.reduce((acc, row) => { acc[row.groupName] = (acc[row.groupName] || 0) + 1; return acc; }, {});
    }).catch(() => null);
    for (let index = 0; index < 8; index += 1) {
      await page.locator(".group-tab").nth(index).click();
      const label = (await page.locator(".group-tab.active").textContent()).replace(/\d+$/, "").trim();
      const names = await page.locator("table.data tbody tr .name").allTextContents();
      const empty = await page.locator(".card").filter({ hasText: "등록된" }).count();
      if (!names.length && !empty) return { ok: false, reason: `"${label}" 탭에 표도 빈 상태 안내도 없음` };
      if (expected && names.length && names.length !== (expected[label] || 0)) {
        return { ok: false, reason: `"${label}" 탭 ${names.length}건, 기대 ${expected[label] || 0}건` };
      }
    }
    return { ok: true };
  });

  await record("자산군 탭이 해당 자산군 자산만 보여준다 (다른 자산군 혼입 없음)", async () => {
    await page.goto(`http://127.0.0.1:${PORT}/assets.html`);
    await page.waitForSelector(".group-tabs", { timeout: 5000 });
    await page.locator(".group-tab").filter({ hasText: "부동산" }).click();
    const rows = await page.evaluate(() => {
      const names = [...document.querySelectorAll("table.data tbody tr .name")].map((node) => node.textContent);
      const summary = Portfolio.summarize(SessionStore.read().assets);
      return { names, realestate: summary.rows.filter((row) => row.group === "realestate").map((row) => row.name) };
    });
    if (rows.names.length !== rows.realestate.length) return { ok: false, reason: `부동산 탭 ${rows.names.length}건 / 실제 부동산 ${rows.realestate.length}건` };
    return { ok: true };
  });

  await record("실물자산 탭은 손익을 '평가차액'으로 부른다", async () => {
    const headers = await page.locator("table.data thead th").allTextContents();
    if (!headers.includes("평가차액")) return { ok: false, reason: `열 이름: ${headers.join(", ")}` };
    await page.locator(".group-tab").filter({ hasText: "주식·ETF" }).click();
    const financialHeaders = await page.locator("table.data thead th").allTextContents();
    return financialHeaders.includes("평가손익") ? { ok: true } : { ok: false, reason: `금융자산 탭 열 이름: ${financialHeaders.join(", ")}` };
  });

  // ===== 5a-3. 시세 로딩 중에는 숫자를 내지 않는다 =====
  // 실측 버그: quotes.json 도착 전에 한 번 렌더해서 환율이 없는 총자산(6억 2,350만원)이
  // 뜨고 2초 뒤 6억 5,866만원으로 정정됐다. 3,516만원 차이가 깜빡였고, 게다가
  // "시세를 확인하지 못했어요"라는 사실 아닌 경고가 떴다 사라졌다.
  for (const [pageName, url] of [["홈", HOME_URL], ["자산", `http://127.0.0.1:${PORT}/assets.html`]]) {
    await record(`${pageName} 화면: 시세 로딩이 느려도 금액이 바뀌는 순간이 없다`, async () => {
      // quotes.json 응답을 1.2초 지연시켜 로딩 구간을 실제로 만든다.
      await page.route("**/data/quotes.json", async (route) => {
        await new Promise((done) => setTimeout(done, 1200));
        // 지연 도중 페이지를 벗어나면 라우트가 이미 처리돼 있을 수 있다 — 그건 오류가 아니다.
        await route.continue().catch(() => {});
      });
      // commit까지만 기다린다. load를 기다리면 관찰 시작이 하위 리소스(웹폰트 등)만큼
      // 밀려서, 1.1초 창이 시세 도착(1.2초) 뒤로 넘어가 로딩 구간을 놓친다.
      await page.goto(url, { waitUntil: "commit" });
      // 로딩 구간 동안 화면을 반복 관찰한다.
      const seen = new Set();
      const deadline = Date.now() + 1100;
      let sawSkeleton = false;
      while (Date.now() < deadline) {
        // commit 직후에는 body조차 없을 수 있다. 그 순간도 관찰 구간의 일부다.
        const snap = await page.evaluate(() => ({
          skeleton: Boolean(document.querySelector(".skel")),
          text: (document.getElementById("page") || document.body || {}).innerText || "",
        }));
        if (snap.skeleton) sawSkeleton = true;
        // 금액 형태(…원)가 보이면 기록한다.
        (snap.text.match(/[\d,]+(?:억|만)?\s?[\d,]*원/g) || []).forEach((money) => seen.add(money));
        if (/시세를 확인하지 못한/.test(snap.text)) return { ok: false, reason: "로딩 중에 '시세를 확인하지 못한' 경고가 떴다" };
        await new Promise((done) => setTimeout(done, 120));
      }
      await page.unroute("**/data/quotes.json");
      if (!sawSkeleton) return { ok: false, reason: "로딩 자리표시(.skel)가 한 번도 보이지 않음 — 로딩 구간이 재현되지 않았을 수 있다" };
      if (seen.size) return { ok: false, reason: `로딩 중에 금액이 표시됐다: ${[...seen].slice(0, 4).join(", ")}` };
      await page.waitForFunction(() => !document.querySelector(".skel"), { timeout: 8000 });
      return { ok: true };
    });
  }

  await record("두 화면의 총자산이 서로 같다", async () => {
    await page.goto(HOME_URL);
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 8000 });
    const homeTotal = await page.evaluate(() => Portfolio.summarize(session.assets).total);
    await page.goto(`http://127.0.0.1:${PORT}/assets.html`);
    await page.waitForSelector(".group-tabs", { timeout: 8000 });
    const assetsTotal = await page.evaluate(() => Portfolio.summarize(SessionStore.read().assets).total);
    return homeTotal === assetsTotal ? { ok: true } : { ok: false, reason: `홈 ${homeTotal} ≠ 자산 ${assetsTotal}` };
  });

  // ===== 5b. 스키마 불일치: 데이터를 조용히 버리지 않는다 =====
  // 앱을 업데이트해 SESSION_SCHEMA가 올라가면, 자산을 입력해 둔 사용자가 홈에서
  // "자산이 없어요"를 보게 됐다. 데이터는 살아 있는데 사라진 것처럼 보이는 상태다.
  await record("스키마가 달라도 살릴 수 있는 자산은 불러오고 원본을 지우지 않는다", async () => {
    await page.goto(HOME_URL);
    await page.evaluate(() => {
      localStorage.clear();
      const legacy = {
        schema: 99, // 미래(또는 과거) 스키마
        assets: [
          { id: "x1", group: "savings", fields: { productType: "예금", balance: 3000000 }, autoFields: {} },
          { id: "x2", group: "cash", fields: { currency: "KRW", amount: 1000000 }, autoFields: {} },
        ],
      };
      localStorage.setItem("assetInput.session", JSON.stringify(legacy));
      // 원격 사본도 같은 값으로 둔다 — 비어 있으면 앱이 로컬을 비우고 시작하므로
      // "예전 형식이 이 계정에 실제로 저장돼 있는" 상황이 재현되지 않는다.
      sessionStorage.setItem("assetflow.test.remote", JSON.stringify(legacy));
    });
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".total-amount, .empty-state"), { timeout: 5000 });
    const text = await page.locator("#app").textContent();
    if (text.includes("아직 등록된 자산이 없어요")) return { ok: false, reason: "자산이 있는데 빈 상태로 표시됨" };
    if (!text.includes("400만원")) return { ok: false, reason: `총자산이 계산되지 않음: ${text.slice(0, 160)}` };
    if (!text.includes("예전 형식")) return { ok: false, reason: "예전 형식 안내가 없음" };
    // 계정 저장이 원격 조회 후 로컬을 다시 쓰는 구간이 있어, 값이 자리를 잡을 때까지 기다린다.
    await page.waitForFunction(() => localStorage.getItem("assetInput.session") !== null, { timeout: 5000 });
    const preserved = await page.evaluate(() => JSON.parse(localStorage.getItem("assetInput.session")).schema);
    return preserved === 99 ? { ok: true } : { ok: false, reason: `원본이 덮어써짐 (schema=${preserved})` };
  });

  await record("살릴 자산이 없는 예전 형식은 '자산 0건'과 구분해 안내한다", async () => {
    await page.evaluate(() => {
      const legacy = { schema: 99, foo: "bar" };
      localStorage.setItem("assetInput.session", JSON.stringify(legacy));
      sessionStorage.setItem("assetflow.test.remote", JSON.stringify(legacy));
    });
    await page.reload();
    await page.waitForSelector(".empty-state", { timeout: 5000 });
    const text = await page.locator("#app").textContent();
    if (text.includes("아직 등록된 자산이 없어요")) return { ok: false, reason: "자산 0건 화면과 구분되지 않음" };
    if (!text.includes("예전 형식으로 저장된 기록이 있어요")) return { ok: false, reason: `안내 문구 없음: ${text.slice(0, 160)}` };
    // 계정 저장이 원격 조회 후 로컬을 다시 쓰는 구간이 있어, 값이 자리를 잡을 때까지 기다린다.
    await page.waitForFunction(() => localStorage.getItem("assetInput.session") !== null, { timeout: 5000 });
    const preserved = await page.evaluate(() => JSON.parse(localStorage.getItem("assetInput.session")).schema);
    return preserved === 99 ? { ok: true } : { ok: false, reason: `원본이 덮어써짐 (schema=${preserved})` };
  });

  await record("입력 화면도 스키마 불일치에서 원본을 지우지 않고 백업한다", async () => {
    await page.goto(INPUT_URL);
    await page.evaluate(() => {
      localStorage.clear();
      localStorage.setItem("assetInput.session", JSON.stringify({
        schema: 99,
        assets: [{ id: "x1", group: "savings", fields: { productType: "예금", balance: 3000000 }, autoFields: {} }],
      }));
    });
    await page.reload();
    const state = await page.evaluate(() => ({
      recovered: session.assets.length,
      backup: JSON.parse(localStorage.getItem("assetInput.session.backup.v99") || "null"),
    }));
    if (state.recovered !== 1) return { ok: false, reason: `살려낸 자산 ${state.recovered}건 (1건 기대)` };
    if (!state.backup || state.backup.schema !== 99) return { ok: false, reason: "원본 백업이 없음" };
    return { ok: true };
  });

  // ===== 6. 성장 가능성 축이 독립적인 정보를 주는가 =====
  // 위 몇 검사가 localStorage를 덮어썼으므로, 등록으로 만든 세션으로 되돌린다.
  await restoreRegistered();

  // 축을 내린 근거를 코드로 고정해 둔다. 나중에 누가 되살리려 할 때 왜 뺐는지가
  // 문장이 아니라 실행되는 검사로 남아 있어야 한다.
  await record("성장 가능성을 금융자산 기준으로 계산하면 위험자산 비중과 같은 값이 된다", async () => {
    await page.goto(HOME_URL);
    await page.evaluate((assets) => {
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 7, assets, snapshots: [] }));
    }, SAMPLE_ASSETS);
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 5000 });
    const report = await page.evaluate(() => {
      const SCORE = { 낮음: 0, 중간: 0.5, 높음: 1 };
      const summary = Portfolio.summarize(session.assets);
      const total = summary.financial.total;
      const growth = summary.financial.rows.reduce((sum, row) => sum + SCORE[row.growth] * row.krw, 0) / total;
      const risky = summary.financial.rows.reduce((sum, row) => sum + (row.growth === "높음" ? row.krw : 0), 0) / total;
      return { growth, risky, mid: summary.financial.rows.filter((row) => row.growth === "중간").length };
    });
    console.log(
      `      금융자산 기준 성장 가능성 = ${(report.growth * 100).toFixed(1)}% · 위험자산('높음') 비중 = ${(report.risky * 100).toFixed(1)}% · '중간' 등급 자산 ${report.mid}건`
    );
    if (report.mid) return { ok: false, reason: "표본에 '중간' 자산이 있어 동일성 검증이 성립하지 않음 — 표본을 확인하세요" };
    return Math.abs(report.growth - report.risky) < 1e-9
      ? { ok: true }
      : { ok: false, reason: `두 값이 다름: ${report.growth} vs ${report.risky}` };
  });
} finally {
  if (browser) await browser.close();
  if (server) server.kill();
  if (originalQuotes !== null) await writeFile(QUOTES_PATH, originalQuotes, "utf8");
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exitCode = 1;
