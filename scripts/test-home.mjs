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
let server;
let browser;

try {
  await writeFile(QUOTES_PATH, JSON.stringify(QUOTES_FIXTURE), "utf8");
  server = await startServer();
  browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 390, height: 844 } }); // 모바일 폭

  // ===== 1. 자산 0건 =====
  await page.goto(HOME_URL);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await record("자산 0건이면 홈 대신 자산 입력을 유도한다", async () => {
    const text = await page.locator("#app").textContent();
    if (!text.includes("아직 등록된 자산이 없어요")) return { ok: false, reason: `빈 상태 문구 없음: ${text.slice(0, 120)}` };
    const href = await page.locator("a.cta").getAttribute("href");
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
  await page.click("[data-next]");

  const registered = await page.evaluate(() => session.assets.length);
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
    await page.locator("a.add").click();
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

  await record("구성 그래프가 도넛이 아니라 가로 누적 막대다", async () => {
    if (await page.locator("svg.donut").count()) return { ok: false, reason: "도넛이 아직 있음" };
    const segments = await page.locator(".stack .stack-seg").count();
    const legends = await page.locator(".legend-row").count();
    return segments === legends && segments > 0 ? { ok: true } : { ok: false, reason: `막대 조각 ${segments}개 / 범례 ${legends}개` };
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

  await record("요약 축이 채움 막대가 아니라 위치 마커다", async () => {
    if (await page.locator(".meter-fill").count()) return { ok: false, reason: "채움 막대가 남아 있음" };
    const marks = await page.locator(".meter-mark").count();
    // 축은 3개다 — 성장 가능성 축은 위험자산 비중과 같은 값이 나와서 내렸다.
    return marks === 3 ? { ok: true } : { ok: false, reason: `마커 ${marks}개 (3개 기대)` };
  });

  await record("성장 가능성 축은 노출하지 않는다 (위험자산 비중과 같은 값)", async () => {
    const keys = await page.evaluate(() => Portfolio.summarize(session.assets).meters.map((meter) => meter.key));
    if (keys.includes("growth")) return { ok: false, reason: `축에 growth가 남아 있음: ${keys.join(",")}` };
    // 분류 자체는 남아 있어야 한다 — 업종 데이터로 주식 내부를 세분화할 때 다시 쓴다.
    const stillClassified = await page.evaluate(() => Portfolio.summarize(session.assets).rows.every((row) => Boolean(row.growth)));
    return stillClassified ? { ok: true } : { ok: false, reason: "growth 분류 자체가 사라짐" };
  });

  await record("평가손익 모집단과 총자산 모집단이 다르다는 것이 화면에 드러난다", async () => {
    if (home.profitCount >= home.rowCount) return { ok: false, reason: `손익 ${home.profitCount}건 / 전체 ${home.rowCount}건 — 모집단이 같아 검증이 성립하지 않음` };
    const needle = `매입 정보가 있는 ${home.profitCount}개 자산 기준`;
    if (!home.text.includes(needle)) return { ok: false, reason: `"${needle}" 문구가 화면에 없음` };
    if (!home.text.includes(`총자산은 ${home.rowCount}개 자산 기준`)) return { ok: false, reason: "총자산 모집단 표기가 없음" };
    return { ok: true };
  });

  await record("기준일 표기가 quotes.json의 asOf와 일치한다", async () => {
    const sub = await page.locator(".head .sub").textContent();
    return sub.includes("8월 7일 종가 기준") ? { ok: true } : { ok: false, reason: `헤더 기준일: "${sub}"` };
  });

  await record("CoinGecko 출처와 국제 시세 기준 표기가 뜬다", async () => {
    if (!home.text.includes("Data provided by CoinGecko")) return { ok: false, reason: "출처 표기 없음" };
    if (!home.text.includes("국제 시세 기준")) return { ok: false, reason: "국제 시세 기준 표기 없음" };
    return { ok: true };
  });

  await record("모바일 폭 첫 화면에 총자산과 자산 구성이 함께 보인다", async () => {
    const viewport = page.viewportSize().height;
    const totalBox = await page.locator(".total-amount").boundingBox();
    const stackBox = await page.locator(".stack").boundingBox();
    if (!stackBox) return { ok: false, reason: "구성 막대가 렌더되지 않음" };
    if (totalBox.y < 0 || totalBox.y > viewport) return { ok: false, reason: `총자산이 첫 화면 밖 (y=${totalBox.y})` };
    if (stackBox.y + stackBox.height > viewport) return { ok: false, reason: `자산 구성이 첫 화면(${viewport}px) 밖 — 막대 하단 y=${Math.round(stackBox.y + stackBox.height)}` };
    return { ok: true };
  });

  await record("범례 행 클릭이 실제로 이동한다 (막대 조각이 아니라 범례가 진입점)", async () => {
    const row = page.locator(".legend-row").first();
    const label = await row.locator(".name").textContent();
    await row.click();
    await page.waitForURL(/asset-input\.html/, { timeout: 5000 });
    const url = page.url();
    await page.goBack();
    await page.waitForFunction(() => document.querySelector(".total-amount"), { timeout: 5000 });
    // 누른 항목이 무엇인지까지 URL로 전달돼야 한다 — 단순 이동만으로는 부족하다.
    return url.includes(`composition=${encodeURIComponent(label)}`) ? { ok: true } : { ok: false, reason: `"${label}" 범례를 눌렀는데 이동한 URL: ${url}` };
  });

  await record("보유 자산 행 클릭이 실제로 이동한다", async () => {
    await page.locator("button.holding").first().click();
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
    await page.waitForFunction(() => document.querySelector(".stack"), { timeout: 5000 });
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
    await page.waitForFunction(() => document.querySelector(".total-amount, .empty"), { timeout: 5000 });
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
    await page.waitForSelector(".empty", { timeout: 5000 });
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
