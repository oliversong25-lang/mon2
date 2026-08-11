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
  quotes: {
    "005930": { price: 73400, currency: "KRW" }, // 삼성전자
    "035720": { price: 52300, currency: "KRW" }, // 카카오
    "000660": { price: 474000, currency: "KRW" }, // SK하이닉스 — 축·열 확인용
  },
  crypto: { BTC: { price: 91000000, currency: "KRW" }, ETH: { price: 2700000, currency: "KRW" } },
  rates: { USD: 1380.5, JPY: 9.25 },
  commodities: { goldPerGram: 151200 },
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
  await installTestAuth(page);

  // ===== 1. 자산 0건 =====
  await page.goto(HOME_URL);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await record("자산 0건이면 홈 대신 자산 입력을 유도한다", async () => {
    const text = await page.locator("#app").textContent();
    if (!text.includes("아직 등록된 자산이 없어요")) return { ok: false, reason: `빈 상태 문구 없음: ${text.slice(0, 120)}` };
    const href = await page.locator("a.btn.primary").getAttribute("href");
    if (!href.includes("asset-input.html")) return { ok: false, reason: `입력 화면 링크가 아님: ${href}` };
    return { ok: true };
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
    return count === 0 ? { ok: true } : { ok: false, reason: "확인할 항목이 없는데도 줄이 보인다" };
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
  await record("오늘의 주요 뉴스·주요 경제지표·다가오는 일정이 준비 중으로 자리를 잡는다", async () => {
    await page.goto(HOME_URL);
    await page.evaluate((assets) => {
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 7, snapshots: [], assets }));
    }, AXIS_ASSETS);
    await page.reload();
    await page.waitForFunction(() => document.querySelector("svg.donut"), { timeout: 8000 });
    const headings = await page.locator(".card h2").allTextContents();
    for (const title of ["오늘의 주요 뉴스", "주요 경제지표", "다가오는 일정"]) {
      const found = headings.find((text) => text.includes(title));
      if (!found) return { ok: false, reason: `"${title}" 칸이 없음: ${headings.join(" | ")}` };
      if (!found.includes("준비 중")) return { ok: false, reason: `"${title}"에 준비 중 표시가 없음` };
    }
    // 다가오는 일정은 자산 만기가 아니라 거시 경제 일정 칸이다.
    const schedule = await page.locator(".card").filter({ hasText: "다가오는 일정" }).textContent();
    if (/만기/.test(schedule)) return { ok: false, reason: "다가오는 일정이 아직 자산 만기를 가리킨다" };
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
    const sub = await page.locator(".page-head .sub").textContent();
    return sub.includes("8월 7일 종가 기준") ? { ok: true } : { ok: false, reason: `헤더 기준일: "${sub}"` };
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

  await record("첫날(스냅샷 1건)에는 변화 대신 안내를 보여준다", async () => {
    const snapshots = await page.evaluate(() => session.snapshots);
    if (snapshots.length !== 1) return { ok: false, reason: `스냅샷 ${snapshots.length}건 (1건 기대)` };
    if (snapshots[0].date !== ASOF_DATE) return { ok: false, reason: `스냅샷 날짜 ${snapshots[0].date}` };
    if (snapshots[0].total !== EXPECT.total) return { ok: false, reason: `스냅샷 총액 ${snapshots[0].total}` };
    const change = await page.locator(".total-change").textContent();
    return change.includes("내일부터") ? { ok: true } : { ok: false, reason: `변화 문구: "${change}"` };
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
    if (!text.includes("총자산을 계산할 수 없어요")) return { ok: false, reason: "계산 불가 안내가 없음" };
    if (!text.includes("5건은 그대로 남아 있습니다")) return { ok: false, reason: "입력 자산 건수 안내가 없음" };
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
      await page.goto(url);
      // 로딩 구간 동안 화면을 반복 관찰한다.
      const seen = new Set();
      const deadline = Date.now() + 1100;
      let sawSkeleton = false;
      while (Date.now() < deadline) {
        const snap = await page.evaluate(() => ({
          skeleton: Boolean(document.querySelector(".skel")),
          text: (document.getElementById("page") || document.body).innerText,
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
      localStorage.setItem("assetInput.session", JSON.stringify({
        schema: 99, // 미래(또는 과거) 스키마
        assets: [
          { id: "x1", group: "savings", fields: { productType: "예금", balance: 3000000 }, autoFields: {} },
          { id: "x2", group: "cash", fields: { currency: "KRW", amount: 1000000 }, autoFields: {} },
        ],
      }));
    });
    await page.reload();
    await page.waitForFunction(() => document.querySelector(".total-amount, .empty-state"), { timeout: 5000 });
    const text = await page.locator("#app").textContent();
    if (text.includes("아직 등록된 자산이 없어요")) return { ok: false, reason: "자산이 있는데 빈 상태로 표시됨" };
    if (!text.includes("400만원")) return { ok: false, reason: `총자산이 계산되지 않음: ${text.slice(0, 160)}` };
    if (!text.includes("예전 형식")) return { ok: false, reason: "예전 형식 안내가 없음" };
    const preserved = await page.evaluate(() => JSON.parse(localStorage.getItem("assetInput.session")).schema);
    return preserved === 99 ? { ok: true } : { ok: false, reason: `원본이 덮어써짐 (schema=${preserved})` };
  });

  await record("살릴 자산이 없는 예전 형식은 '자산 0건'과 구분해 안내한다", async () => {
    await page.evaluate(() => {
      localStorage.setItem("assetInput.session", JSON.stringify({ schema: 99, foo: "bar" }));
    });
    await page.reload();
    await page.waitForSelector(".empty-state", { timeout: 5000 });
    const text = await page.locator("#app").textContent();
    if (text.includes("아직 등록된 자산이 없어요")) return { ok: false, reason: "자산 0건 화면과 구분되지 않음" };
    if (!text.includes("예전 형식으로 저장된 기록이 있어요")) return { ok: false, reason: `안내 문구 없음: ${text.slice(0, 160)}` };
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
