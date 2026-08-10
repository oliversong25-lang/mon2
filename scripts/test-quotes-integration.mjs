// scripts/test-quotes-integration.mjs
// Item 6 검증: MOCK_QUOTES에 한 번도 없었던 종목(카카오)으로 실제 클릭·타이핑
// 평가금액 계산을 확인한다. 삼성전자만 테스트하면 우연히 통과할 수 있다는 지적에
// 대한 답. 실제 tickers-kr.json(커밋된 진짜 데이터)을 그대로 쓰고, quotes.json과
// 업종(sec) 필드만 테스트용으로 임시로 얹었다가 끝나면 원복한다.
//
// data/tickers-kr.json과 data/quotes.json은 항상 시작할 때 백업하고 끝나면 그대로
// 복원한다(중간에 assertion이 실패하거나 예외가 나도) — 실수로 실배치 산출물이나
// 커밋된 원본을 테스트 픽스처로 영구히 덮어쓰지 않기 위함이다.

import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { readFile, writeFile, rm } from "node:fs/promises";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 4331;
const BASE_URL = `http://127.0.0.1:${PORT}/asset-input.html`;
const TICKERS_KR_PATH = resolve(ROOT, "data", "tickers-kr.json");
const QUOTES_PATH = resolve(ROOT, "data", "quotes.json");

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

async function searchAndPick(page, fieldId, query) {
  await page.locator(`#${fieldId}`).click();
  await page.keyboard.type(query, { delay: 20 });
  await page.waitForSelector(".search-result", { timeout: 5000 });
  await page.locator(".search-result").first().click();
  await page.waitForFunction(() => !document.querySelector(".search-host:not([hidden])"), { timeout: 5000 });
}

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

// 백업은 무조건 가장 먼저, 아무것도 아직 건드리기 전에 한다.
const originalTickersKr = await readFile(TICKERS_KR_PATH, "utf8").catch(() => null);
const originalQuotes = await readFile(QUOTES_PATH, "utf8").catch(() => null);

let server, browser;
try {
  if (originalTickersKr === null) {
    record("사전 조건: data/tickers-kr.json 존재", false, "파일이 없습니다 — 먼저 build-tickers.mjs를 한 번 실행해야 합니다");
  } else {
    const tickerRows = JSON.parse(originalTickersKr);
    const kakaoRow = tickerRows.find((row) => row.c === "035720");
    if (!kakaoRow) {
      record("사전 조건: tickers-kr.json에 카카오(035720)가 있음", false, "카카오가 없습니다 — 커밋된 원본이 아니거나(예: 라이브 배치의 버그 있는 산출물), A접두어 등 다른 코드 형식으로 저장돼 있을 수 있습니다");
    } else {
      // DART 업종코드(세분류) + build-tickers가 붙여주는 중분류까지 갖춘 임시 테스트값.
      // 세 필드를 함께 얹어야 "화면엔 한글명, 레코드엔 원본 코드"가 실제로 검증된다.
      kakaoRow.sec = "264";
      kakaoRow.secDiv = "26";
      kakaoRow.secDivName = "전자·통신장비";
      await writeFile(TICKERS_KR_PATH, `${JSON.stringify(tickerRows)}\n`, "utf8");

      const ASOF_DATE = "2026-08-06";
      const quotesFixture = {
        asOf: `${ASOF_DATE}T00:00:00+09:00`,
        sources: { equity: "테스트", fx: "테스트" },
        quotes: {
          "005930": { price: 73400, currency: "KRW" }, // 삼성전자 — 예전 MOCK_QUOTES에도 있었음
          "035720": { price: 52300, currency: "KRW" }, // 카카오 — MOCK_QUOTES엔 한 번도 없었음
          // 현대차(005380)는 의도적으로 뺐다 — "시세 확인 불가" 경로 검증용
        },
        rates: { USD: 1380.5 },
        commodities: { goldPerGram: 151200 },
      };
      await writeFile(QUOTES_PATH, JSON.stringify(quotesFixture), "utf8");

      server = await startServer();
      browser = await chromium.launch();
      const page = await browser.newPage();

      // 1. 카카오(MOCK에 없던 종목) 실제 검색·선택·수량 입력 -> 평가금액 정상 계산
      await prepareGroup(page, "equity");
      await record("카카오 검색 -> 선택 -> 수량 입력 -> 평가금액이 quotes.json 가격(52,300원)으로 계산됨", async () => {
        await searchAndPick(page, "field-equity-search", "카카오");
        await page.locator("#field-equity-quantity").click();
        await page.keyboard.type("10", { delay: 15 });
        const valuationText = await page.locator("[data-valuation-line='equity']").textContent();
        if (!valuationText.includes("523,000")) return { ok: false, reason: `평가금액에 523,000이 없음: "${valuationText}"` };
        return { ok: true };
      });

      await record("카카오 등록 시점에 업종이 자산 레코드에 고정 저장됨 (등록 시 DART 실시간 호출 없이)", async () => {
        await page.locator("#field-equity-averagePrice").click();
        await page.keyboard.type("50000", { delay: 15 });
        await page.click("[data-next]");
        const asset = await page.evaluate(() => session?.assets?.find((a) => a.fields.productCode === "035720"));
        if (!asset) return { ok: false, reason: "카카오 자산이 저장되지 않음" };
        const auto = asset.autoFields || {};
        // 화면에 쓰는 값은 한글 업종명이어야 한다 — KSIC 코드("264")를 그대로 노출하면 안 된다.
        if (auto.sector !== "전자·통신장비") return { ok: false, reason: `sector가 한글 업종명이 아님: ${JSON.stringify(auto)}` };
        if (auto.sectorDivision !== "26") return { ok: false, reason: `중분류 코드 불일치: ${JSON.stringify(auto)}` };
        // 세분류 원본 코드는 상세 화면·재분류를 위해 함께 남아 있어야 한다.
        if (auto.sectorCode !== "264") return { ok: false, reason: `세분류 원본 코드가 보존되지 않음: ${JSON.stringify(auto)}` };
        return { ok: true };
      });

      // 2. 기준일 표기가 asOf 기반 (더 이상 "오늘 오전 6시"라는 거짓 문구가 아님)
      await record('평가금액 문구가 "오늘 오전 6시 기준"이 아니라 asOf 날짜(8월 6일) 기반으로 표기됨', async () => {
        await prepareGroup(page, "equity");
        await searchAndPick(page, "field-equity-search", "삼성전자");
        await page.locator("#field-equity-quantity").click();
        await page.keyboard.type("1", { delay: 15 });
        const text = await page.locator("[data-valuation-line='equity']").textContent();
        if (text.includes("오늘 오전 6시")) return { ok: false, reason: `아직도 거짓 문구가 남아있음: "${text}"` };
        if (!text.includes("8월 6일")) return { ok: false, reason: `asOf 날짜(8월 6일)가 안 보임: "${text}"` };
        return { ok: true };
      });

      // 3. quotes.json에 없는 종목(현대차) -> "확인 불가" 표시, 조용히 사라지지 않음
      await prepareGroup(page, "equity");
      await record("현대차(quotes.json에 없음) 등록 시 평가금액 대신 '시세 확인 불가'가 명시적으로 뜸", async () => {
        await searchAndPick(page, "field-equity-search", "현대차");
        await page.locator("#field-equity-quantity").click();
        await page.keyboard.type("5", { delay: 15 });
        const text = await page.locator("[data-valuation-line='equity']").textContent();
        if (!text.includes("확인 불가")) return { ok: false, reason: `"확인 불가" 문구가 없음: "${text}"` };
        return { ok: true };
      });

      await record("현대차를 그대로 등록하면 검토 화면 목록에도 '확인 불가'가 보임 (조용히 사라지지 않음)", async () => {
        await page.locator("#field-equity-averagePrice").click();
        await page.keyboard.type("200000", { delay: 15 });
        await page.click("[data-next]");
        const reviewText = await page.locator(".review-list").textContent();
        if (!reviewText.includes("확인 불가")) return { ok: false, reason: `검토 화면에 확인 불가 문구가 없음: "${reviewText.slice(0, 300)}"` };
        return { ok: true };
      });

      // 4. quotes.json 자체가 없을 때 (배치 실패/최초 상태) 전체가 부드럽게 확인 불가로 빠지는지
      await rm(QUOTES_PATH, { force: true });
      await record("quotes.json이 아예 없을 때도 앱이 죽지 않고 '확인 불가'로 계속 동작함", async () => {
        await prepareGroup(page, "equity");
        const consoleErrors = [];
        page.on("pageerror", (e) => consoleErrors.push(e.message));
        await searchAndPick(page, "field-equity-search", "삼성전자");
        await page.locator("#field-equity-quantity").click();
        await page.keyboard.type("1", { delay: 15 });
        const text = await page.locator("[data-valuation-line='equity']").textContent();
        if (!text.includes("확인 불가")) return { ok: false, reason: `quotes.json 없을 때 확인 불가로 안 빠짐: "${text}"` };
        if (consoleErrors.length) return { ok: false, reason: `페이지 에러 발생: ${consoleErrors.join(", ")}` };
        return { ok: true };
      });
    }
  }
} finally {
  await browser?.close();
  server?.kill();
  if (originalTickersKr !== null) await writeFile(TICKERS_KR_PATH, originalTickersKr, "utf8");
  if (originalQuotes !== null) await writeFile(QUOTES_PATH, originalQuotes, "utf8");
  else await rm(QUOTES_PATH, { force: true });
}

const failed = results.filter((r) => !r.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
if (failed.length) process.exitCode = 1;
