// scripts/test-numeric.mjs
// Regression test for: numeric:true fields (quantity, ownershipRate, interestRate, area,
// faceQuantity) accepting letters/Hangul verbatim. Verifies beforeinput-level rejection,
// paste cleanup, IME composition cleanup, per-field decimal limits, and the
// ownershipRate 0~100 clamp. Run: node scripts/test-numeric.mjs

import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { installTestAuth, launchTestBrowser } from "./lib/test-auth.mjs";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 4327;
const BASE_URL = `http://127.0.0.1:${PORT}/asset-input.html`;

async function startServer() {
  const server = spawn(process.platform === "win32" ? "python" : "python3", ["-m", "http.server", String(PORT), "--bind", "127.0.0.1"], {
    cwd: ROOT,
    stdio: "pipe",
  });
  await Promise.race([
    once(server.stdout, "data"),
    once(server.stderr, "data"),
    new Promise((r) => setTimeout(r, 800)),
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
    const chip = [...document.querySelectorAll(`[data-chipset="${chipset}"] .chip`)].find((c) => c.dataset.value === value);
    chip?.click();
  }, { chipset, value });
}

async function openOptional(page, group) {
  await page.evaluate((g) => {
    const details = document.querySelector(`[data-details="optional-${g}"]`);
    if (details && !details.open) details.open = true;
  }, group);
}

// Real click + real keyboard: type "10" then Korean letters, expect letters rejected.
async function checkRejectsLetters(page, fieldId) {
  const exists = await page.locator(`#${fieldId}`).count();
  if (!exists) return { ok: false, reason: "field not found in DOM" };
  await page.locator(`#${fieldId}`).click();
  await page.keyboard.type("10", { delay: 15 });
  await page.keyboard.type("가나다", { delay: 15 });
  await page.keyboard.type("abc", { delay: 15 });
  const value = await page.locator(`#${fieldId}`).inputValue();
  if (value !== "10") return { ok: false, reason: `expected "10", got "${value}"` };
  return { ok: true };
}

// Simulate a paste (clipboard write not available headless w/o permissions, so
// dispatch the same beforeinput/input sequence a real paste produces).
async function checkPasteSanitized(page, fieldId, pasteText, expected) {
  await page.locator(`#${fieldId}`).click();
  const result = await page.evaluate(({ id, text }) => {
    const el = document.getElementById(id);
    el.focus();
    const start = el.selectionStart ?? el.value.length;
    const end = el.selectionEnd ?? start;
    const beforeInputEvent = new InputEvent("beforeinput", { data: text, inputType: "insertFromPaste", bubbles: true, cancelable: true });
    const notCancelled = el.dispatchEvent(beforeInputEvent);
    if (notCancelled) {
      el.value = el.value.slice(0, start) + text + el.value.slice(end);
      el.dispatchEvent(new InputEvent("input", { data: text, inputType: "insertFromPaste", bubbles: true }));
    }
    return el.value;
  }, { id: fieldId, text: pasteText });
  if (result !== expected) return { ok: false, reason: `paste "${pasteText}" -> expected "${expected}", got "${result}"` };
  return { ok: true };
}

// Simulate Hangul IME composition committing invalid text into a numeric field.
async function checkCompositionSanitized(page, fieldId) {
  await page.locator(`#${fieldId}`).click();
  await page.keyboard.type("5", { delay: 15 });
  const result = await page.evaluate((id) => {
    const el = document.getElementById(id);
    el.focus();
    el.dispatchEvent(new CompositionEvent("compositionstart", { bubbles: true }));
    el.value = el.value + "가";
    el.dispatchEvent(new InputEvent("input", { data: "가", inputType: "insertCompositionText", bubbles: true }));
    el.dispatchEvent(new CompositionEvent("compositionend", { data: "가", bubbles: true }));
    return el.value;
  }, fieldId);
  if (result !== "5") return { ok: false, reason: `composition commit left "${result}", expected "5"` };
  return { ok: true };
}

async function checkDecimalLimit(page, fieldId, typedText, expected) {
  await page.locator(`#${fieldId}`).click();
  await page.keyboard.type(typedText, { delay: 15 });
  const value = await page.locator(`#${fieldId}`).inputValue();
  if (value !== expected) return { ok: false, reason: `typed "${typedText}" -> expected "${expected}", got "${value}"` };
  return { ok: true };
}

async function run() {
  const server = await startServer();
  const browser = await launchTestBrowser(chromium);
  const page = await browser.newPage();
  await installTestAuth(page);
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

  // 1. 재현 절차 그대로: 주식 보유 수량에 10 입력 -> 가나다abc 입력 -> 10만 남아야 함
  await prepareGroup(page, "equity");
  await record("equity.quantity rejects letters/Hangul after digits", () => checkRejectsLetters(page, "field-equity-quantity"));

  await prepareGroup(page, "crypto");
  await record("crypto.quantity rejects letters/Hangul", () => checkRejectsLetters(page, "field-crypto-quantity"));

  await prepareGroup(page, "commodity");
  await clickChip(page, "assetKind", "금");
  await clickChip(page, "holdingMethod", "KRX 금시장");
  await record("commodity.quantity rejects letters/Hangul", () => checkRejectsLetters(page, "field-commodity-quantity"));

  await prepareGroup(page, "realestate");
  await record("realestate.ownershipRate rejects letters/Hangul", async () => {
    await page.evaluate(() => {
      const toggle = document.querySelector('[data-toggle="joint"]');
      toggle?.click();
    });
    return checkRejectsLetters(page, "field-realestate-ownershipRate");
  });

  await prepareGroup(page, "savings");
  await openOptional(page, "savings");
  await record("savings.interestRate rejects letters/Hangul", () => checkRejectsLetters(page, "field-savings-interestRate"));

  await prepareGroup(page, "bond");
  await openOptional(page, "bond");
  await record("bond.faceQuantity rejects letters/Hangul", () => checkRejectsLetters(page, "field-bond-faceQuantity"));

  await prepareGroup(page, "realestate");
  await openOptional(page, "realestate");
  await record("realestate.area rejects letters/Hangul", () => checkRejectsLetters(page, "field-realestate-area"));

  // 2. 소수점 자릿수 강제
  await prepareGroup(page, "equity");
  await record("equity.quantity clips to 6 decimals (help text spec)", () =>
    checkDecimalLimit(page, "field-equity-quantity", "1.23456789", "1.234567")
  );

  await prepareGroup(page, "crypto");
  await record("crypto.quantity clips to 8 decimals (help text spec), 0.35 still works", async () => {
    const a = await checkDecimalLimit(page, "field-crypto-quantity", "0.123456789", "0.12345678");
    if (!a.ok) return a;
    await page.locator("#field-crypto-quantity").fill("");
    return checkDecimalLimit(page, "field-crypto-quantity", "0.35", "0.35");
  });

  // 3. 붙여넣기에서도 동작
  await prepareGroup(page, "equity");
  await record("equity.quantity paste strips letters and clips decimals", () =>
    checkPasteSanitized(page, "field-equity-quantity", "12.3456789abc", "12.345678")
  );

  // 4. 한글 IME 조합 커밋에서도 정제됨
  await prepareGroup(page, "equity");
  await record("equity.quantity Hangul composition commit is stripped", () => checkCompositionSanitized(page, "field-equity-quantity"));

  // 5. 지분율 0~100 범위 clamp (blur 시점)
  await prepareGroup(page, "realestate");
  await record("realestate.ownershipRate clamps to 100 on blur", async () => {
    await page.evaluate(() => document.querySelector('[data-toggle="joint"]')?.click());
    await page.locator("#field-realestate-ownershipRate").click();
    await page.keyboard.type("150", { delay: 15 });
    await page.locator("#field-realestate-valuation").click();
    const value = await page.locator("#field-realestate-ownershipRate").inputValue();
    if (value !== "100") return { ok: false, reason: `expected clamp to "100", got "${value}"` };
    return { ok: true };
  });

  // 6. 회귀: money 필드는 여전히 정상 동작 (숫자 필드 정제 로직 공유 리팩터가 money를 깨지 않았는지)
  await prepareGroup(page, "cash");
  await record("cash.amount (money field) still rejects letters and formats on blur", async () => {
    await page.locator("#field-cash-amount").click();
    await page.keyboard.type("10000", { delay: 15 });
    await page.keyboard.type("가나다", { delay: 15 });
    const raw = await page.locator("#field-cash-amount").inputValue();
    if (raw !== "10000") return { ok: false, reason: `expected raw "10000", got "${raw}"` };
    await page.locator("[data-currency-search]").click();
    const formatted = await page.locator("#field-cash-amount").inputValue();
    if (formatted !== "10,000") return { ok: false, reason: `expected formatted "10,000", got "${formatted}"` };
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
