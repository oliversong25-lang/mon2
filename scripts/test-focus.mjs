// scripts/test-focus.mjs
// Regression test: clicking into a money/quantity/search field must not lose focus,
// and typing must land in full without being interrupted by a full re-render.
// Run: npm run test:focus  (starts its own static server on a free port)

import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 4321;
const BASE_URL = `http://127.0.0.1:${PORT}/asset-input.html`;

async function startServer() {
  const server = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1"], {
    cwd: ROOT,
    stdio: "pipe",
  });
  await Promise.race([
    once(server.stdout, "data"),
    once(server.stderr, "data"),
    new Promise(resolveTimer => setTimeout(resolveTimer, 800)),
  ]);
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
    const chip = [...document.querySelectorAll(`[data-chipset="${chipset}"] .chip`)].find(
      (c) => c.dataset.value === value
    );
    chip?.click();
  }, { chipset, value });
}

async function openOptional(page, group) {
  await page.evaluate((g) => {
    const details = document.querySelector(`[data-details="optional-${g}"]`);
    if (details && !details.open) details.open = true;
  }, group);
}

// Returns { ok, reason } — clicks the field, checks the field alone receives
// focus with no immediate blur, then types digits and checks nothing was dropped.
async function checkFieldFocus(page, fieldId, typedText = "1234567") {
  const exists = await page.locator(`#${fieldId}`).count();
  if (!exists) return { ok: false, reason: "field not found in DOM" };

  await page.evaluate((id) => {
    if (window.__focusCleanup) window.__focusCleanup();
    const el = document.getElementById(id);
    const log = [];
    const onFocus = () => log.push("focus");
    const onBlur = () => log.push("blur");
    el.addEventListener("focus", onFocus);
    el.addEventListener("blur", onBlur);
    window.__focusLog = log;
    window.__focusCleanup = () => {
      el.removeEventListener("focus", onFocus);
      el.removeEventListener("blur", onBlur);
      window.__focusCleanup = null;
    };
  }, fieldId);

  await page.locator(`#${fieldId}`).click();

  const afterClick = await page.evaluate((id) => ({
    log: window.__focusLog,
    activeId: document.activeElement?.id || null,
  }), fieldId);

  if (afterClick.activeId !== fieldId) {
    return { ok: false, reason: `click did not focus the field (active=${afterClick.activeId})` };
  }
  if (afterClick.log.join(",") !== "focus") {
    return { ok: false, reason: `unexpected focus/blur sequence: [${afterClick.log.join(",")}]` };
  }

  await page.keyboard.type(typedText, { delay: 15 });

  const value = await page.locator(`#${fieldId}`).inputValue();
  const digitsOnly = value.replace(/[^0-9]/g, "");
  if (digitsOnly !== typedText.replace(/[^0-9]/g, "")) {
    return { ok: false, reason: `value truncated: expected digits "${typedText}" got "${value}"` };
  }

  const stillFocused = await page.evaluate((id) => document.activeElement?.id === id, fieldId);
  if (!stillFocused) return { ok: false, reason: "lost focus while typing" };

  return { ok: true };
}

async function run() {
  const server = await startServer();
  const browser = await chromium.launch();
  const page = await browser.newPage();
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

  // 현금 금액
  await prepareGroup(page, "cash");
  await record("cash.amount", () => checkFieldFocus(page, "field-cash-amount"));

  // 예금·적금 현재 잔액
  await prepareGroup(page, "savings");
  await record("savings.balance", () => checkFieldFocus(page, "field-savings-balance"));

  // 주식 보유 수량 · 평균 매입단가
  await prepareGroup(page, "equity");
  await record("equity.quantity", () => checkFieldFocus(page, "field-equity-quantity"));
  await record("equity.averagePrice", () => checkFieldFocus(page, "field-equity-averagePrice"));

  // 가상자산 보유 수량 · 평균 매입단가
  await prepareGroup(page, "crypto");
  await record("crypto.quantity", () => checkFieldFocus(page, "field-crypto-quantity"));
  await record("crypto.averagePrice", () => checkFieldFocus(page, "field-crypto-averagePrice"));

  // 펀드 현재 평가금액
  await prepareGroup(page, "fund");
  await record("fund.valuation", () => checkFieldFocus(page, "field-fund-valuation"));

  // 채권 현재 평가금액
  await prepareGroup(page, "bond");
  await record("bond.valuation", () => checkFieldFocus(page, "field-bond-valuation"));

  // 원자재 보유 수량 (금 · KRX 금시장 → 수량 필드)
  await prepareGroup(page, "commodity");
  await clickChip(page, "assetKind", "금");
  await clickChip(page, "holdingMethod", "KRX 금시장");
  await record("commodity.quantity", () => checkFieldFocus(page, "field-commodity-quantity"));

  // 원자재 현재 평가금액 (원유/에너지 → 보유방식 자동 "기타" → 평가금액 필드)
  await prepareGroup(page, "commodity");
  await clickChip(page, "assetKind", "원유/에너지");
  await record("commodity.valuation", () => checkFieldFocus(page, "field-commodity-valuation"));

  // 부동산 현재 추정가치
  await prepareGroup(page, "realestate");
  await record("realestate.valuation", () => checkFieldFocus(page, "field-realestate-valuation"));

  // 부동산 매입가격 (선택 정보 아코디언 열기)
  await prepareGroup(page, "realestate");
  await openOptional(page, "realestate");
  await record("realestate.purchasePrice", () => checkFieldFocus(page, "field-realestate-purchasePrice"));

  // 회귀: 주식 종목 검색어를 한 번에 이어서 칠 수 있다 (한글 포함)
  await prepareGroup(page, "equity");
  await record("equity.search continuous typing", async () => {
    const fieldId = "field-equity-search";
    await page.locator(`#${fieldId}`).click();
    await page.keyboard.type("삼성전자", { delay: 15 });
    const value = await page.locator(`#${fieldId}`).inputValue();
    if (value !== "삼성전자") return { ok: false, reason: `expected "삼성전자" got "${value}"` };
    return { ok: true };
  });

  // 회귀: 통화를 바꿔도 입력한 금액이 사라지지 않는다
  await prepareGroup(page, "cash");
  await record("cash currency switch preserves amount", async () => {
    await page.locator("#field-cash-amount").click();
    await page.keyboard.type("500000", { delay: 15 });
    await page.locator("[data-currency-search]").click();
    await page.evaluate(() => {
      const btn = [...document.querySelectorAll("[data-currency]")].find((b) => b.dataset.currency === "USD");
      btn?.click();
    });
    const value = await page.locator("#field-cash-amount").inputValue();
    if (!value.replace(/[^0-9]/g, "").includes("500000")) {
      return { ok: false, reason: `amount lost after currency switch, got "${value}"` };
    }
    return { ok: true };
  });

  // 회귀: 진행 표시 분모가 선택한 자산군 개수와 일치한다
  await record("progress denominator matches selected groups", async () => {
    await page.goto(BASE_URL);
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.evaluate(() => {
      document.querySelectorAll("[data-group-check]").forEach((cb) => {
        const shouldCheck = ["cash", "savings", "equity"].includes(cb.dataset.groupCheck);
        if (cb.checked !== shouldCheck) {
          cb.checked = shouldCheck;
          cb.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    });
    await page.click("[data-start]");
    const text = await page.locator(".progress").textContent();
    if (!text.includes("/ 3")) return { ok: false, reason: `expected "/ 3" in progress text, got "${text}"` };
    return { ok: true };
  });

  // 회귀: 건너뛰기가 8개 자산군 전부에 보인다
  for (const group of ["cash", "savings", "equity", "crypto", "fund", "bond", "commodity", "realestate"]) {
    await prepareGroup(page, group);
    await record(`skip button visible for ${group}`, async () => {
      const count = await page.locator("[data-skip]").count();
      if (count !== 1) return { ok: false, reason: `expected 1 skip button, found ${count}` };
      return { ok: true };
    });
  }

  // 회귀: 금 → 원유/에너지 → 금 으로 돌아오면 수량·보유 방식이 복원된다
  await prepareGroup(page, "commodity");
  await record("commodity gold->oil->gold restores quantity/holdingMethod", async () => {
    await clickChip(page, "assetKind", "금");
    await clickChip(page, "holdingMethod", "KRX 금시장");
    await page.locator("#field-commodity-quantity").click();
    await page.keyboard.type("12", { delay: 15 });
    await clickChip(page, "assetKind", "원유/에너지");
    await clickChip(page, "assetKind", "금");
    const holdingActive = await page.evaluate(() => {
      const chip = document.querySelector('[data-chipset="holdingMethod"] .chip.active');
      return chip?.dataset.value || null;
    });
    const qtyValue = await page.locator("#field-commodity-quantity").inputValue();
    if (holdingActive !== "KRX 금시장") return { ok: false, reason: `holdingMethod not restored, got "${holdingActive}"` };
    if (qtyValue.replace(/[^0-9]/g, "") !== "12") return { ok: false, reason: `quantity not restored, got "${qtyValue}"` };
    return { ok: true };
  });

  // 회귀: 토지를 고르면 보유 목적에서 "실거주"가 사라진다
  await prepareGroup(page, "realestate");
  await record("realestate land removes 실거주 purpose option", async () => {
    await clickChip(page, "propertyType", "토지");
    const purposeValues = await page.evaluate(() => {
      const set = document.querySelector('[data-chipset="purpose"]');
      return set ? [...set.querySelectorAll(".chip")].map((c) => c.dataset.value) : null;
    });
    if (!purposeValues) return { ok: false, reason: "purpose chipset not found" };
    if (purposeValues.includes("실거주")) return { ok: false, reason: `실거주 still present: ${purposeValues.join(",")}` };
    return { ok: true };
  });

  // 회귀: 최종 화면 제목이 "자산 입력 현황"이고 총합 건수가 없다
  await record("review screen title and no total sum", async () => {
    await page.goto(BASE_URL);
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.evaluate(() => {
      document.querySelectorAll("[data-group-check]").forEach((cb) => {
        const shouldCheck = cb.dataset.groupCheck === "cash";
        if (cb.checked !== shouldCheck) {
          cb.checked = shouldCheck;
          cb.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    });
    await page.click("[data-start]");
    await page.locator("#field-cash-amount").click();
    await page.keyboard.type("100000", { delay: 15 });
    await page.click("[data-next]");
    const h1 = await page.locator(".view h1").first().textContent();
    const bodyText = await page.locator(".view").first().textContent();
    if (h1?.trim() !== "자산 입력 현황") return { ok: false, reason: `expected title "자산 입력 현황", got "${h1}"` };
    if (/총\s*자산|총합|합계금액/.test(bodyText)) return { ok: false, reason: "found a total-sum label that should not exist" };
    return { ok: true };
  });

  // 회귀: 검토 화면에서 새로고침하면 이어하기 카드가 아니라 검토 화면 그대로 유지된다
  await record("review screen survives a page reload (does not fall back to resume card)", async () => {
    await page.goto(BASE_URL);
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.evaluate(() => {
      document.querySelectorAll("[data-group-check]").forEach((cb) => {
        const shouldCheck = cb.dataset.groupCheck === "cash";
        if (cb.checked !== shouldCheck) {
          cb.checked = shouldCheck;
          cb.dispatchEvent(new Event("change", { bubbles: true }));
        }
      });
    });
    await page.click("[data-start]");
    await page.locator("#field-cash-amount").click();
    await page.keyboard.type("100000", { delay: 15 });
    await page.click("[data-next]");
    const reachedReview = (await page.locator(".review-list").count()) > 0;
    if (!reachedReview) return { ok: false, reason: "did not reach review screen before reload" };
    await page.reload();
    const stillOnReview = (await page.locator(".review-list").count()) > 0;
    const resumeCardShown = (await page.locator("[data-resume]").count()) > 0;
    if (!stillOnReview) return { ok: false, reason: "review screen was lost after reload" };
    if (resumeCardShown) return { ok: false, reason: "resume card should not appear when returning directly to review" };
    return { ok: true };
  });

  // 회귀: 입력 도중(review 아님) 새로고침은 기존처럼 이어하기 카드를 보여준다
  await record("mid-input reload still shows the resume card (unchanged behavior)", async () => {
    await prepareGroup(page, "savings");
    await page.locator("#field-savings-balance").click();
    await page.keyboard.type("500000", { delay: 15 });
    await page.waitForTimeout(400);
    await page.reload();
    const resumeCardShown = (await page.locator("[data-resume]").count()) > 0;
    if (!resumeCardShown) return { ok: false, reason: "expected resume card for mid-input reload, got none" };
    return { ok: true };
  });

  // 회귀: details 열림 상태와 스크롤 위치가 렌더 후에도 유지된다
  await prepareGroup(page, "savings");
  await record("details open state survives a render", async () => {
    const details = await page.locator('[data-details^="optional-"]').first();
    await details.locator("summary").click();
    const openBefore = await details.evaluate((el) => el.open);
    await page.evaluate(() => window.render());
    const openAfter = await details.evaluate((el) => el.open);
    if (!openBefore) return { ok: false, reason: "details did not open on click" };
    if (!openAfter) return { ok: false, reason: "details closed after render()" };
    return { ok: true };
  });

  // 회귀: script 태그가 1개씩만 존재한다
  await record("script tag count is exactly one open/close pair", async () => {
    const html = await page.content();
    const opens = (html.match(/<script/g) || []).length;
    const closes = (html.match(/<\/script>/g) || []).length;
    if (opens !== 1 || closes !== 1) return { ok: false, reason: `opens=${opens} closes=${closes}` };
    return { ok: true };
  });

  await browser.close();
  server.kill();

  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} passed`);
  if (failed.length) {
    console.log("\nFailed:");
    failed.forEach((f) => console.log(`  - ${f.label}: ${f.reason}`));
    process.exitCode = 1;
  }
}

run();
