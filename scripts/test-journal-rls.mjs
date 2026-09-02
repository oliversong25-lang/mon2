// 원칙·원칙 이력·의사결정 기록의 계정 격리를 **실제 계정 두 개로** 확인한다.
//
// 정책 SQL이 파일에 있다는 사실은 격리의 증거가 아니다. 한 계정이 자기 행 하나를 보는
// 것도 증거가 아니다 — 그것은 정책이 없어도 성립한다. 증거는 셋이다.
//
//   1. A가 쓴 행을 A가 본다
//   2. **B가 A의 행을 보지 못한다**
//   3. **B가 A의 user_id로 행을 만들지 못한다**
//
// 세 번째가 특히 중요하다. select 정책만 걸고 insert를 열어 두면 남의 계정에 기록을
// 심을 수 있고, 그것은 조회 격리가 통과해도 남는 구멍이다.
const required = ["SUPABASE_URL", "SUPABASE_ANON_KEY", "RLS_TEST_A_EMAIL", "RLS_TEST_A_PASSWORD", "RLS_TEST_B_EMAIL", "RLS_TEST_B_PASSWORD"];
const missing = required.filter((key) => !process.env[key]);
if (missing.length) {
  console.error(`[기록 RLS] 환경 변수가 없습니다: ${missing.join(", ")}`);
  console.error("[기록 RLS] 두 계정 자격 증명 없이는 격리를 확인할 수 없습니다. 확인하지 않은 것을 통과로 적지 않습니다.");
  process.exit(1);
}

const url = process.env.SUPABASE_URL.replace(/\/$/, "");
const anon = process.env.SUPABASE_ANON_KEY;
const base = { apikey: anon, "Content-Type": "application/json" };

async function call(path, options = {}) {
  const response = await fetch(url + path, { ...options, headers: { ...base, ...options.headers } });
  const text = await response.text();
  const body = text ? JSON.parse(text) : null;
  if (!response.ok) throw new Error(`${response.status} ${body?.message || body?.msg || text}`);
  return body;
}
const login = (email, password) => call("/auth/v1/token?grant_type=password", { method: "POST", body: JSON.stringify({ email, password }) });
const auth = (token, extra = {}) => ({ ...extra, Authorization: `Bearer ${token}` });

const a = await login(process.env.RLS_TEST_A_EMAIL, process.env.RLS_TEST_A_PASSWORD);
const b = await login(process.env.RLS_TEST_B_EMAIL, process.env.RLS_TEST_B_PASSWORD);
const stamp = Date.now();
const results = [];
const record = (label, ok, detail) => {
  results.push({ label, ok });
  console.log(`${ok ? "PASS" : "FAIL"}  ${label}${ok ? "" : `  — ${detail}`}`);
};

async function blocked(promise) {
  try { await promise; return false; }
  catch (error) {
    if (/401|403|42501|row-level security|violates/i.test(error.message)) return true;
    throw error;
  }
}

// ── 1. 투자 원칙 ───────────────────────────────────────────────────────────
const answers = { buy: `rls-${stamp}`, sell: "테스트" };
await call("/rest/v1/user_investment_philosophy?on_conflict=user_id", {
  method: "POST",
  headers: auth(a.access_token, { Prefer: "resolution=merge-duplicates,return=minimal" }),
  body: JSON.stringify({ user_id: a.user.id, answers }),
});
const ownPhil = await call(`/rest/v1/user_investment_philosophy?select=answers&user_id=eq.${a.user.id}`, { headers: auth(a.access_token) });
const crossPhil = await call(`/rest/v1/user_investment_philosophy?select=answers&user_id=eq.${a.user.id}`, { headers: auth(b.access_token) });
record("A가 자기 원칙을 본다", ownPhil.length === 1 && ownPhil[0].answers.buy === answers.buy, JSON.stringify(ownPhil));
record("B가 A의 원칙을 보지 못한다", crossPhil.length === 0, `${crossPhil.length}건`);
record("B가 A의 원칙을 만들지 못한다", await blocked(call("/rest/v1/user_investment_philosophy", {
  method: "POST", headers: auth(b.access_token), body: JSON.stringify({ user_id: a.user.id, answers: { buy: "forged" } }),
})), "삽입이 통과했습니다");

// ── 2. 원칙 이력 ───────────────────────────────────────────────────────────
await call("/rest/v1/user_philosophy_revisions", {
  method: "POST", headers: auth(a.access_token, { Prefer: "return=minimal" }),
  body: JSON.stringify({ user_id: a.user.id, reason: `rls-${stamp}`, answers }),
});
const ownRev = await call(`/rest/v1/user_philosophy_revisions?select=id,reason&user_id=eq.${a.user.id}&reason=eq.rls-${stamp}`, { headers: auth(a.access_token) });
const crossRev = await call(`/rest/v1/user_philosophy_revisions?select=id&user_id=eq.${a.user.id}`, { headers: auth(b.access_token) });
record("A가 자기 원칙 이력을 본다", ownRev.length === 1, `${ownRev.length}건`);
record("B가 A의 원칙 이력을 보지 못한다", crossRev.length === 0, `${crossRev.length}건`);

// 이력은 고치거나 지울 수 없어야 한다. 나중에 손볼 수 있는 이력은 이력이 아니다.
record("A도 자기 원칙 이력을 고칠 수 없다", await blocked(call(`/rest/v1/user_philosophy_revisions?id=eq.${ownRev[0].id}`, {
  method: "PATCH", headers: auth(a.access_token), body: JSON.stringify({ reason: "고쳐 봄" }),
})), "수정이 통과했습니다");
record("A도 자기 원칙 이력을 지울 수 없다", await blocked(call(`/rest/v1/user_philosophy_revisions?id=eq.${ownRev[0].id}`, {
  method: "DELETE", headers: auth(a.access_token),
})), "삭제가 통과했습니다");

// ── 3. 의사결정 기록 ───────────────────────────────────────────────────────
const made = await call("/rest/v1/user_decision_records", {
  method: "POST", headers: auth(a.access_token, { Prefer: "return=representation" }),
  body: JSON.stringify({
    user_id: a.user.id, action_statement: `rls-${stamp}`,
    reasons_for: [{ id: "for-1", text: "테스트 근거" }],
    reasons_against: [{ id: "against-1", text: "테스트 반대", falsifies: true,
      kind: "machine", rule: { metric: "price", op: "lte", value: 1, holdingId: null, checkable: false } }],
    decision: "executed", expectation: "테스트 기대",
    holding_id: null, holding_label: "", context: { businessCycle: { phase: "expansion" } },
  }),
});
const ownRec = await call(`/rest/v1/user_decision_records?select=id,action_statement,reasons_against,decision&user_id=eq.${a.user.id}&action_statement=eq.rls-${stamp}`, { headers: auth(a.access_token) });
const crossRec = await call(`/rest/v1/user_decision_records?select=id&user_id=eq.${a.user.id}`, { headers: auth(b.access_token) });
record("A가 자기 결정 기록을 본다", ownRec.length === 1 && ownRec[0].decision === "executed", JSON.stringify(ownRec));
record("보유 자산 없이도 기록이 저장된다", made[0].holding_id === null, JSON.stringify(made[0]?.holding_id));
record("반증 조건의 종류가 구분돼 저장된다", ownRec[0].reasons_against?.[0]?.kind === "machine", JSON.stringify(ownRec[0]?.reasons_against));
record("B가 A의 결정 기록을 보지 못한다", crossRec.length === 0, `${crossRec.length}건`);
record("B가 A의 결정 기록을 만들지 못한다", await blocked(call("/rest/v1/user_decision_records", {
  method: "POST", headers: auth(b.access_token), body: JSON.stringify({
    user_id: a.user.id, action_statement: "forged", reasons_for: [], reasons_against: [], decision: "executed",
  }),
})), "삽입이 통과했습니다");
record("B가 A의 결정 기록을 지우지 못한다", await blocked(call(`/rest/v1/user_decision_records?id=eq.${made[0].id}`, {
  method: "DELETE", headers: auth(b.access_token),
})) || (await call(`/rest/v1/user_decision_records?select=id&id=eq.${made[0].id}`, { headers: auth(a.access_token) })).length === 1,
  "행이 사라졌습니다");

// 정리. 남겨 두면 다음 실행이 이전 흔적을 자기 것으로 센다.
await call(`/rest/v1/user_decision_records?id=eq.${made[0].id}`, { method: "DELETE", headers: auth(a.access_token) });

const failed = results.filter((row) => !row.ok);
console.log(`\n${results.length - failed.length}/${results.length} passed`);
process.exit(failed.length ? 1 : 0);
