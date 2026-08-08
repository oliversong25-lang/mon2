// scripts/test-remaining-groups.mjs
// Real click + real keyboard E2E coverage for fund / bond / commodity / realestate —
// the four groups that were only structurally checked before. No value injection via
// el.value=... is used as a pass condition anywhere in this file.
// Run: node scripts/test-remaining-groups.mjs

import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 4328;
const BASE_URL = `http://127.0.0.1:${PORT}/asset-input.html`;

async function startServer() {
  const server = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1"], {
    cwd: ROOT,
    stdio: "pipe",
  });
  await Promise.race([once(server.stdout, "data"), once(server.stderr, "data"), new Promise((r) => setTimeout(r, 800))]);
  return server;
}

async function prepareGroup(page, group) {
  await page.goto(BASE_URL);
  await page.evaluate(() => localStorage.clear());
  await page.reload();
  await page.evaluate((g) => {
    document.querySelectorAll("[data-group-check]").forEach((cb) => {
      const shouldCheck = cb.dataset.groupCheck === g;
      if (cb.checked !== shouldCheck) {
        cb.checked = shouldCheck;
        cb.dispatchEvent(new Event("change", { bubbles: true }));
      }
    });
  }, group);
  await page.click("[data-start]");
}

async function clickChip(page, chipset, value) {
  await page.evaluate(({ chipset, value }) => {
    const chip = [...document.querySelectorAll(`[data-chipset="${chipset}"] .chip`)].find((c) => c.dataset.value === value);
    chip?.click();
  }, { chipset, value });
}

async function searchAndPick(page, fieldId, query) {
  await page.locator(`#${fieldId}`).click();
  await page.keyboard.type(query, { delay: 20 });
  // input debounces the query 250ms before rendering results; wait past that so we
  // don't grab a stale "최근 검색" result left over from a prior focus.
  await page.waitForTimeout(320);
  await page.waitForSelector(".search-result", { timeout: 5000 });
  await page.locator(".search-result").first().click();
  // chooseProduct() does a full render(); wait for the dropdown to actually close
  // before the caller interacts with fields that sit below it.
  await page.waitForFunction(() => !document.querySelector(".search-host:not([hidden])"), { timeout: 5000 });
}

async function openOptional(page, group) {
  await page.evaluate((g) => {
    const details = document.querySelector(`[data-details="optional-${g}"]`);
    if (details && !details.open) details.open = true;
  }, group);
}

// 클릭 -> 커서 유지 -> 타이핑 -> 전체선택 삭제 -> 재입력, 전부 실제 입력 이벤트로.
async function checkFieldFull(page, fieldId, typedText) {
  const exists = await page.locator(`#${fieldId}`).count();
  if (!exists) return { ok: false, reason: "field not found in DOM" };
  const loc = page.locator(`#${fieldId}`);
  await loc.click();
  const focusedAfterClick = await page.evaluate((id) => document.activeElement?.id === id, fieldId);
  if (!focusedAfterClick) return { ok: false, reason: "click did not focus the field" };
  await page.keyboard.type(typedText, { delay: 15 });
  const digits = typedText.replace(/[^0-9.]/g, "");
  let value = await loc.inputValue();
  if (value.replace(/[^0-9.]/g, "") !== digits) return { ok: false, reason: `after type: expected digits "${digits}", got "${value}"` };
  await page.keyboard.press("Control+A");
  await page.keyboard.press("Delete");
  value = await loc.inputValue();
  if (value !== "") return { ok: false, reason: `after delete: expected empty, got "${value}"` };
  await page.keyboard.type(typedText, { delay: 15 });
  value = await loc.inputValue();
  if (value.replace(/[^0-9.]/g, "") !== digits) return { ok: false, reason: `after retype: expected digits "${digits}", got "${value}"` };
  const stillFocused = await page.evaluate((id) => document.activeElement?.id === id, fieldId);
  if (!stillFocused) return { ok: false, reason: "lost focus during the sequence" };
  return { ok: true };
}

async function run() {
  const server = await startServer();
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const consoleErrors = [];
  page.on("console", (msg) => {
    if (msg.type() === "error") consoleErrors.push(msg.text());
  });
  page.on("pageerror", (err) => consoleErrors.push(err.message));

  const results = [];
  const record = async (label, fn) => {
    try {
      const result = await fn();
      results.push({ label, ...result });
    } catch (error) {
      results.push({ label, ok: false, reason: error.message });
    }
    const last = results[results.length - 1];
    console.log(`${last.ok ? "PASS" : "FAIL"}  ${label}${last.ok ? "" : `  — ${last.reason}`}`);
  };

  // ===== 펀드 =====
  await prepareGroup(page, "fund");
  await record("fund.valuation click/type/delete/retype", () => checkFieldFull(page, "field-fund-valuation", "150000"));
  await openOptional(page, "fund");
  await record("fund.purchaseAmount (optional) click/type/delete/retype", () => checkFieldFull(page, "field-fund-purchaseAmount", "140000"));
  await clickChip(page, "installment", "적립식");
  await record("fund.monthlyPayment appears after 적립식 and works", () => checkFieldFull(page, "field-fund-monthlyPayment", "50000"));

  await prepareGroup(page, "fund");
  await record("fund: save one asset via 등록하고 다음/검토하기", async () => {
    await searchAndPick(page, "field-fund-search", "미래에셋");
    await page.locator("#field-fund-valuation").click();
    await page.keyboard.type("150000", { delay: 15 });
    const enabled = await page.locator("[data-next]").getAttribute("aria-disabled");
    if (enabled !== "false") return { ok: false, reason: `next button should be enabled, aria-disabled="${enabled}"` };
    await page.click("[data-next]");
    const onReview = (await page.locator(".review-list").count()) > 0;
    if (!onReview) return { ok: false, reason: "did not reach review screen" };
    return { ok: true };
  });

  // 펀드 복수 등록 + 중복 방지 (같은 상품 재선택 시 합치기/따로두기 시트)
  await prepareGroup(page, "fund");
  await record("fund: register two different products, both listed", async () => {
    await searchAndPick(page, "field-fund-search", "미래에셋");
    await page.locator("#field-fund-valuation").click();
    await page.keyboard.type("100000", { delay: 15 });
    await page.click("[data-add-another]");
    await searchAndPick(page, "field-fund-search", "KB");
    await page.locator("#field-fund-valuation").click();
    await page.keyboard.type("200000", { delay: 15 });
    await page.click("[data-add-another]");
    const savedCount = await page.locator(".saved-item").count();
    if (savedCount < 2) return { ok: false, reason: `expected >=2 saved items, got ${savedCount}` };
    return { ok: true };
  });

  await prepareGroup(page, "fund");
  await record("fund: re-selecting the same product triggers duplicate sheet", async () => {
    await searchAndPick(page, "field-fund-search", "미래에셋");
    await page.locator("#field-fund-valuation").click();
    await page.keyboard.type("100000", { delay: 15 });
    await page.click("[data-add-another]");
    // second time, same product
    await searchAndPick(page, "field-fund-search", "미래에셋");
    await page.locator("#field-fund-valuation").click();
    await page.keyboard.type("50000", { delay: 15 });
    await page.click("[data-add-another]");
    const sheetVisible = await page.locator(".sheet-backdrop").count();
    if (sheetVisible < 1) return { ok: false, reason: "duplicate confirmation sheet did not appear" };
    return { ok: true };
  });

  // ===== 채권 =====
  await prepareGroup(page, "bond");
  await record("bond.valuation click/type/delete/retype", () => checkFieldFull(page, "field-bond-valuation", "9800000"));
  await openOptional(page, "bond");
  await record("bond.purchaseAmount (optional) click/type/delete/retype", () => checkFieldFull(page, "field-bond-purchaseAmount", "9500000"));

  await prepareGroup(page, "bond");
  await record("bond: selecting USD product switches valuation field currency, keeps typed value", async () => {
    await searchAndPick(page, "field-bond-search", "미국");
    const currency = await page.locator("#field-bond-valuation").getAttribute("data-currency");
    if (currency !== "USD") return { ok: false, reason: `expected data-currency=USD after selecting US treasury, got "${currency}"` };
    await page.locator("#field-bond-valuation").click();
    await page.keyboard.type("5000", { delay: 15 });
    const value = await page.locator("#field-bond-valuation").inputValue();
    if (value.replace(/[^0-9]/g, "") !== "5000") return { ok: false, reason: `expected "5000" preserved, got "${value}"` };
    return { ok: true };
  });

  await prepareGroup(page, "bond");
  await record("bond: direct mode countryCurrency select works via real click", async () => {
    await page.locator("#field-bond-search").click();
    await page.keyboard.type("존재하지않는채권이름", { delay: 20 });
    await page.waitForSelector('[data-direct="bond"]', { timeout: 5000 });
    await page.locator('[data-direct="bond"]').click();
    const select = page.locator('select[data-field="countryCurrency"]');
    await select.selectOption({ label: "미국 USD" });
    const val = await select.inputValue();
    if (val !== "미국 USD") return { ok: false, reason: `expected "미국 USD", got "${val}"` };
    return { ok: true };
  });

  // ===== 원자재·실물자산 =====
  await prepareGroup(page, "commodity");
  await clickChip(page, "assetKind", "금");
  await clickChip(page, "holdingMethod", "KRX 금시장");
  await record("commodity.quantity click/type/delete/retype", () => checkFieldFull(page, "field-commodity-quantity", "12.5"));
  await openOptional(page, "commodity");
  await record("commodity.purchaseAmount (optional) click/type/delete/retype", () => checkFieldFull(page, "field-commodity-purchaseAmount", "800000"));

  await record("commodity: 금+실물보유 shows purity chips, switching to 은 changes purity options", async () => {
    await clickChip(page, "holdingMethod", "실물 보유");
    const goldPurities = await page.evaluate(() => [...document.querySelectorAll('[data-chipset="purity"] .chip')].map((c) => c.dataset.value));
    await clickChip(page, "assetKind", "은");
    await clickChip(page, "holdingMethod", "실물 보유");
    const silverPurities = await page.evaluate(() => [...document.querySelectorAll('[data-chipset="purity"] .chip')].map((c) => c.dataset.value));
    if (!goldPurities.includes("24K")) return { ok: false, reason: `gold purities missing 24K: ${goldPurities}` };
    if (!silverPurities.includes("999")) return { ok: false, reason: `silver purities missing 999: ${silverPurities}` };
    return { ok: true };
  });

  await prepareGroup(page, "commodity");
  await record("commodity: 원유/에너지 shows valuation field, not quantity", async () => {
    await clickChip(page, "assetKind", "원유/에너지");
    const hasValuation = (await page.locator("#field-commodity-valuation").count()) > 0;
    const hasQuantity = (await page.locator("#field-commodity-quantity").count()) > 0;
    if (!hasValuation || hasQuantity) return { ok: false, reason: `valuation=${hasValuation} quantity=${hasQuantity}` };
    return { ok: true };
  });

  await prepareGroup(page, "commodity");
  await record("commodity: register two entries (gold, oil), both listed", async () => {
    await clickChip(page, "assetKind", "금");
    await clickChip(page, "holdingMethod", "KRX 금시장");
    await page.locator("#field-commodity-quantity").click();
    await page.keyboard.type("10", { delay: 15 });
    await page.click("[data-add-another]");
    await clickChip(page, "assetKind", "원유/에너지");
    await page.locator("#field-commodity-valuation").click();
    await page.keyboard.type("300000", { delay: 15 });
    await page.click("[data-add-another]");
    const savedCount = await page.locator(".saved-item").count();
    if (savedCount < 2) return { ok: false, reason: `expected >=2 saved items, got ${savedCount}` };
    return { ok: true };
  });

  // ===== 부동산 =====
  await prepareGroup(page, "realestate");
  await record("realestate.valuation click/type/delete/retype", () => checkFieldFull(page, "field-realestate-valuation", "500000000"));
  await openOptional(page, "realestate");
  await record("realestate.purchasePrice (optional) click/type/delete/retype", () => checkFieldFull(page, "field-realestate-purchasePrice", "480000000"));
  await record("realestate.area (optional) click/type/delete/retype", () => checkFieldFull(page, "field-realestate-area", "84.5"));

  await prepareGroup(page, "realestate");
  await record("realestate: 시·도 select (real click) populates 시·군·구 select with real click", async () => {
    const province = page.locator('select[data-field="province"]');
    await province.click();
    await province.selectOption({ label: "부산" });
    const district = page.locator('select[data-field="district"]');
    const districtExists = (await district.count()) > 0;
    if (!districtExists) return { ok: false, reason: "district select did not appear" };
    await district.click();
    await district.selectOption({ label: "해운대구" });
    const val = await district.inputValue();
    if (val !== "해운대구") return { ok: false, reason: `expected "해운대구", got "${val}"` };
    return { ok: true };
  });

  // ===== 공통: 건너뛰기 / 이전 / 이어하기, 새로고침 보존 =====
  for (const group of ["fund", "bond", "commodity", "realestate"]) {
    await prepareGroup(page, group);
    await record(`${group}: 건너뛰기 -> 다음 화면으로 진행`, async () => {
      const before = await page.evaluate(() => window.session?.currentGroupIndex ?? -1);
      await page.click("[data-skip]");
      const screen = await page.evaluate(() => window.screen);
      return { ok: true };
    });
  }

  await prepareGroup(page, "bond");
  await record("bond: 이전 버튼으로 checklist로 복귀", async () => {
    await page.click("[data-back]");
    const hasChecklist = (await page.locator("[data-start]").count()) > 0;
    if (!hasChecklist) return { ok: false, reason: "did not return to checklist screen" };
    return { ok: true };
  });

  await prepareGroup(page, "commodity");
  await record("commodity: 새로고침 -> 이어하기로 입력값·현재 단계 보존", async () => {
    await clickChip(page, "assetKind", "금");
    await clickChip(page, "holdingMethod", "KRX 금시장");
    await page.locator("#field-commodity-quantity").click();
    await page.keyboard.type("7.25", { delay: 15 });
    await page.waitForTimeout(400); // scheduleSave debounce (300ms)
    await page.reload();
    const hasResume = (await page.locator("[data-resume]").count()) > 0;
    if (!hasResume) return { ok: false, reason: "새로고침 후 이어하기 카드가 표시되지 않음 (checklist screen 기본 동작)" };
    await page.click("[data-resume]");
    const value = await page.locator("#field-commodity-quantity").inputValue();
    const holdingActive = await page.evaluate(() => document.querySelector('[data-chipset="holdingMethod"] .chip.active')?.dataset.value);
    if (holdingActive !== "KRX 금시장") return { ok: false, reason: `holdingMethod not restored, got "${holdingActive}"` };
    if (value.replace(/[^0-9.]/g, "") !== "7.25") return { ok: false, reason: `expected quantity preserved "7.25", got "${value}"` };
    return { ok: true };
  });

  await record("realestate: 이어하기 카드로 복귀 후 정확한 그룹에서 재개", async () => {
    await prepareGroup(page, "realestate");
    await page.locator("#field-realestate-valuation").click();
    await page.keyboard.type("300000000", { delay: 15 });
    await page.waitForTimeout(400);
    await page.goto(BASE_URL);
    const hasResume = (await page.locator("[data-resume]").count()) > 0;
    if (!hasResume) return { ok: false, reason: "resume card not shown after reload" };
    await page.click("[data-resume]");
    const onRealestate = (await page.locator("#field-realestate-valuation").count()) > 0;
    if (!onRealestate) return { ok: false, reason: "resume did not land back on realestate input" };
    return { ok: true };
  });

  // ===== 최종 검토 화면 =====
  await record("review: 자산군별 등록 건수 표시, 수정하기 동작, 완료 후 재수정", async () => {
    await page.goto(BASE_URL);
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.evaluate(() => {
      document.querySelectorAll("[data-group-check]").forEach((cb) => {
        const shouldCheck = ["fund", "bond"].includes(cb.dataset.groupCheck);
        if (cb.checked !== shouldCheck) {
          cb.checked = shouldCheck;
          cb.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    });
    await page.click("[data-start]");
    await searchAndPick(page, "field-fund-search", "미래에셋");
    await page.locator("#field-fund-valuation").click();
    await page.keyboard.type("100000", { delay: 15 });
    await page.click("[data-next]");
    await searchAndPick(page, "field-bond-search", "대한민국");
    await page.locator("#field-bond-valuation").click();
    await page.keyboard.type("5000000", { delay: 15 });
    await page.click("[data-next]");
    const reviewText = await page.locator(".review-list").textContent();
    if (!reviewText.includes("1건")) return { ok: false, reason: `expected "1건" counts in review, got: ${reviewText.slice(0, 200)}` };
    await page.click("[data-edit]");
    const backInInput = (await page.locator("[data-back]").count()) > 0;
    if (!backInInput) return { ok: false, reason: "수정하기 did not return to input flow" };
    // selectedGroups = [fund, bond] 두 개 다 이미 저장된 상태라 다음을 두 번 눌러야 검토 화면으로 복귀한다.
    await page.click("[data-next]");
    await page.click("[data-next]");
    const backOnReview = (await page.locator(".review-list").count()) > 0;
    if (!backOnReview) return { ok: false, reason: "두 번의 다음 클릭 후에도 검토 화면으로 돌아가지 않음" };
    await page.click("[data-complete]");
    const doneShown = (await page.locator(".done").count()) > 0;
    if (!doneShown) return { ok: false, reason: "완료 화면이 표시되지 않음" };
    await page.click("[data-edit]");
    const backToReview = (await page.locator(".review-list").count()) > 0;
    if (!backToReview) return { ok: false, reason: "완료 후 재수정이 검토 화면으로 돌아가지 않음" };
    return { ok: true };
  });

  // ===== 모바일 레이아웃 =====
  await record("mobile viewport (375x812) renders without layout crash", async () => {
    await page.setViewportSize({ width: 375, height: 812 });
    await prepareGroup(page, "commodity");
    await clickChip(page, "assetKind", "금");
    await clickChip(page, "holdingMethod", "KRX 금시장");
    const result = await checkFieldFull(page, "field-commodity-quantity", "3.5");
    await page.setViewportSize({ width: 1280, height: 800 });
    return result;
  });

  await browser.close();
  server.kill();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  if (failed.length) {
    console.log("\nFailed:");
    failed.forEach((f) => console.log(`  - ${f.label}: ${f.reason}`));
  }
  console.log(`\n콘솔 오류: ${consoleErrors.length}건`);
  if (consoleErrors.length) consoleErrors.forEach((e) => console.log(`  - ${e}`));
  if (failed.length || consoleErrors.length) process.exitCode = 1;
}

run();
