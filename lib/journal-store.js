// 투자 원칙과 의사결정 기록의 저장. `account-store.js`와 같은 방식으로 Supabase REST를
// 직접 부른다 — `window.supabase`는 없고, SDK를 번들에 넣지 않는 구조를 그대로 따른다.
//
// ── 왜 별도 테이블인가 ─────────────────────────────────────────────────────
// 자산 세션은 문서 하나를 통째로 읽고 쓴다. 기록은 다르다 — 한 건씩 쌓이고, 자산 화면이
// 저장을 트리거할 때마다 기록까지 함께 덮어쓰면 안 된다. 그래서 행 단위 테이블이다.
//
// ── 원칙 이력은 지울 수 없다 ───────────────────────────────────────────────
// 스키마에서 update·delete 권한을 주지 않았다. 나중에 손볼 수 있는 이력은 이력이 아니고,
// 하락장에서 원칙을 고친 기록이 특히 그렇다.
(function (global) {
  "use strict";

  var PHILOSOPHY = "user_investment_philosophy";
  var REVISIONS = "user_philosophy_revisions";
  var RECORDS = "user_decision_records";

  // ── 원칙 문서의 모양 ──────────────────────────────────────────────────────
  // v1 (2026-09-02): 고정 질문 여섯에 대한 자유 문장 답 — { buy, sell, position, horizon, loss, unknown }
  // v2 (트랙 40):    사용자가 한 줄씩 적는 목록        — { version: 2, items: [...] }
  // 저장 칸(user_investment_philosophy.answers jsonb)은 그대로다. 모양만 바뀐다 — 그래서
  // 데이터베이스 전환이 필요 없고, 이력 테이블(덧붙기만 한다)에는 v1 스냅숏이 그대로 남는다.
  var PRINCIPLES_VERSION = 2;
  var LEGACY_QUESTIONS = [
    { id: "buy", text: "무엇을 보면 사기로 합니까?" },
    { id: "sell", text: "무엇을 보면 팔기로 합니까?" },
    { id: "position", text: "한 종목에 자산의 얼마까지 넣습니까?" },
    { id: "horizon", text: "한 번 사면 얼마나 들고 있을 생각입니까?" },
    { id: "loss", text: "어느 정도의 손실까지 견딜 수 있습니까?" },
    { id: "unknown", text: "이해하지 못하는 것에도 투자합니까?" },
  ];
  var KINDS = { principle: "원칙", question: "검토 질문" };

  function newPrincipleId() {
    return "p-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 7);
  }

  function cleanItem(item) {
    if (!item || !item.id) return null;
    var text = String(item.text || "").trim();
    if (!text) return null;
    return {
      id: String(item.id),
      text: text,
      kind: item.kind === "question" ? "question" : "principle",
      core: Boolean(item.core),
      origin: item.origin || null,
      createdAt: item.createdAt || null,
      updatedAt: item.updatedAt || null,
    };
  }

  // v1을 v2로 옮긴다. **답을 버리지 않는다.** 비어 있지 않은 답 하나가 항목 하나가 된다.
  // 우리 질문은 항목 문구에 섞지 않고 origin에 따로 둔다 — 질문 문장을 사용자의 원칙에
  // 끼워 넣으면 우리 말이 그 사람의 원칙이 된다. 원본은 migratedFrom에 통째로 남긴다.
  //
  // 옮긴 항목의 id는 "legacy-<질문>"으로 고정한다. 사용자가 원칙 화면에서 한 번도 저장하지
  // 않은 채 결정을 기록해도, 매번 같은 id로 읽혀 그 답이 같은 항목을 가리킨다.
  function normalizePrinciples(answers, updatedAt) {
    var raw = answers || {};
    if (raw.version === PRINCIPLES_VERSION) {
      return {
        version: PRINCIPLES_VERSION,
        items: (Array.isArray(raw.items) ? raw.items : []).map(cleanItem).filter(Boolean),
        migratedFrom: raw.migratedFrom || null,
        migrated: false,
      };
    }
    var items = [];
    LEGACY_QUESTIONS.forEach(function (q) {
      var text = String(raw[q.id] || "").trim();
      if (!text) return;
      items.push({
        id: "legacy-" + q.id, text: text, kind: "principle", core: false,
        origin: { version: 1, question: q.id, prompt: q.text },
        createdAt: updatedAt || null, updatedAt: updatedAt || null,
      });
    });
    return {
      version: PRINCIPLES_VERSION,
      items: items,
      migratedFrom: items.length ? { version: 1, answers: raw } : null,
      migrated: items.length > 0,
    };
  }

  function cfg() {
    return AccountStore.config();
  }

  function headers(token, extra) {
    return Object.assign(
      { apikey: cfg().anonKey, Authorization: "Bearer " + token, "Content-Type": "application/json" },
      extra || {}
    );
  }

  async function session() {
    var current = AccountStore.auth();
    if (!current || !current.access_token || !current.user) {
      throw Object.assign(new Error("로그인이 필요합니다."), { code: "expired" });
    }
    return current;
  }

  async function request(path, options) {
    var config = cfg();
    if (!config.ready) throw Object.assign(new Error("Supabase 연결 정보가 설정되지 않았습니다."), { code: "config" });
    var response;
    try { response = await fetch(config.url + path, options); }
    catch (error) { throw Object.assign(new Error("네트워크에 연결할 수 없습니다."), { code: "network", cause: error }); }
    var text = await response.text();
    var body = null;
    try { body = text ? JSON.parse(text) : null; }
    catch (error) { body = { message: text }; }
    if (!response.ok) {
      var message = (body && (body.message || body.msg || body.error_description)) || "요청을 처리하지 못했습니다.";
      throw Object.assign(new Error(message), { code: (body && body.code) || String(response.status), status: response.status });
    }
    return body;
  }

  // ── 원칙 ─────────────────────────────────────────────────────────────────

  async function loadPhilosophy() {
    var current = await session();
    var rows = await request(
      "/rest/v1/" + PHILOSOPHY + "?select=answers,updated_at&user_id=eq." + encodeURIComponent(current.user.id) + "&limit=1",
      { method: "GET", headers: headers(current.access_token) }
    );
    var row = rows && rows[0];
    var answers = (row && row.answers) || {};
    var updatedAt = (row && row.updated_at) || null;
    return { answers: answers, doc: normalizePrinciples(answers, updatedAt), updatedAt: updatedAt, exists: Boolean(row) };
  }

  // v2 문서를 저장한다. 저장 경로(이력 먼저, 그다음 현재 원칙)는 savePhilosophy 그대로다.
  function savePrinciples(doc, reason) {
    var out = {
      version: PRINCIPLES_VERSION,
      items: (doc && doc.items ? doc.items : []).map(cleanItem).filter(Boolean),
    };
    if (doc && doc.migratedFrom) out.migratedFrom = doc.migratedFrom;
    return savePhilosophy(out, reason);
  }

  // 저장은 **현재 원칙 갱신 + 이력 한 줄**을 함께 한다. 둘이 갈라지면 "언제 바뀌었나"를
  // 잃는다. 이력을 먼저 쓰는 이유는, 갱신이 성공하고 이력이 실패하는 쪽이 그 반대보다
  // 되돌리기 어렵기 때문이다 — 이력만 남으면 다음 저장에서 맞춰지지만, 현재 원칙만
  // 바뀌면 그 변경은 영원히 기록되지 않는다.
  async function savePhilosophy(answers, reason) {
    var current = await session();
    await request("/rest/v1/" + REVISIONS, {
      method: "POST",
      headers: headers(current.access_token, { Prefer: "return=minimal" }),
      body: JSON.stringify({ user_id: current.user.id, reason: String(reason || ""), answers: answers }),
    });
    await request("/rest/v1/" + PHILOSOPHY + "?on_conflict=user_id", {
      method: "POST",
      headers: headers(current.access_token, { Prefer: "resolution=merge-duplicates,return=minimal" }),
      body: JSON.stringify({ user_id: current.user.id, answers: answers, updated_at: new Date().toISOString() }),
    });
    return true;
  }

  async function loadRevisions(limit) {
    var current = await session();
    return await request(
      "/rest/v1/" + REVISIONS + "?select=id,changed_at,reason,answers&user_id=eq." +
      encodeURIComponent(current.user.id) + "&order=changed_at.desc&limit=" + (limit || 50),
      { method: "GET", headers: headers(current.access_token) }
    );
  }

  // ── 의사결정 기록 ────────────────────────────────────────────────────────

  // ── 1단계 · 원칙에 비추어 ─────────────────────────────────────────────────
  // 원칙 하나에 답 하나. **한 덩어리로 묶지 않는다.** 묶어 두면 나중에 분류를 붙일 때
  // 옮겨 담아야 한다(전환 없이는 못 붙인다).
  //
  //   skipped:true  · answer:null → 손대지 않고 넘겼다
  //   skipped:false · answer:""   → 손댔지만 비워 뒀다
  //   skipped:false · answer:"…"  → 답했다
  // 앞의 둘을 가르는 이유: 어떤 원칙을 늘 건너뛰는지는 나중에 볼 만한 사실이다.
  //
  // text는 **답할 당시의 문구**다. 원칙을 나중에 고쳐도 이 답은 고치기 전 문구에 대한
  // 답으로 남는다 — 바뀐 문구에 대한 답과는 다른 것이기 때문이다. 원칙을 지워도 이 답은
  // 지워지지 않고 이 문구로 계속 읽힌다.
  //
  // ── 분류는 비워 둔다 ──────────────────────────────────────────────────────
  // 답을 "해야 할 이유 / 하지 말아야 할 이유"로 가르는 자동 분류는 **아직 없다.** 판단할
  // 기록이 거의 없고, 잘못 가르면 사용자에게 자기 생각을 뒤집어 보여준다. 트랙 29가 그은
  // 선 — 개수는 사용자가 가른 것일 때만 사실이다 — 을 지킨다. 나중에 붙일 때:
  //   classification    "for" | "against" | "neutral" — 분류기가 쓴다
  //   classificationBy  "model:<식별자>" | "user"     — 누가 갈랐는지
  //   userOverride      사용자가 고친 값. 있으면 classification보다 우선한다
  // 집계는 userOverride가 있거나 classificationBy가 "user"인 답만 센다. 모델이 가른 것을
  // 사실처럼 세지 않는다. 지금은 셋 다 null이다.
  function principleAnswer(item, state) {
    var touched = Boolean(state && state.touched);
    var value = state && typeof state.answer === "string" ? state.answer.trim() : "";
    return {
      principleId: String(item.id),
      text: String(item.text || ""),
      kind: item.kind === "question" ? "question" : "principle",
      core: Boolean(item.core),
      skipped: !touched,
      answer: touched ? value : null,
      classification: null,
      classificationBy: null,
      userOverride: null,
    };
  }

  // 이유 한 줄. **낱개로 담는다** — 한 덩어리 문장이면 나중에 "그때 세 번째 이유가
  // 어떻게 됐나"를 물을 수 없다.
  function reason(item, side) {
    var out = { id: String(item.id || ""), text: String(item.text || "") };
    if (side !== "against") return out;
    // 반증 표시는 반대 목록의 항목에만 붙는다. 표시된 항목이 곧 "이게 일어나면 틀린 것"이다.
    out.falsifies = Boolean(item.falsifies);
    if (!out.falsifies) return out;
    out.kind = item.kind === "machine" ? "machine" : "human";
    out.rule = out.kind === "machine" ? item.rule || null : null;
    return out;
  }

  async function saveRecord(record) {
    var current = await session();
    var row = {
      user_id: current.user.id,
      decided_at: record.decidedAt || new Date().toISOString(),
      action_statement: String(record.actionStatement || ""),
      reasons_for: (record.reasonsFor || []).map(function (item) { return reason(item, "for"); }),
      reasons_against: (record.reasonsAgainst || []).map(function (item) { return reason(item, "against"); }),
      decision: record.decision,
      expectation: String(record.expectation || ""),
      // 보유 자산이 없는 결정(사지 않기로 함)도 저장돼야 한다. 빈 문자열을 null로 바꿔
      // 붙은 것과 안 붙은 것을 데이터에서 구분한다.
      holding_id: record.holdingId || null,
      holding_label: String(record.holdingLabel || ""),
      context: record.context || {},
      // 1단계가 생긴 뒤의 기록은 원칙이 없어도 []로 남는다. null은 "이 단계가 생기기 전".
      principle_answers: Array.isArray(record.principleAnswers) ? record.principleAnswers : [],
    };
    try {
      var saved = await request("/rest/v1/" + RECORDS, {
        method: "POST",
        headers: headers(current.access_token, { Prefer: "return=representation" }),
        body: JSON.stringify(row),
      });
      return saved && saved[0];
    } catch (error) {
      // 서버에 principle_answers 칸이 아직 없을 때(supabase/schema.sql 적용 전). 앱은
      // push하는 순간 배포되지만 데이터베이스 전환은 사람이 적용한다 — 그 사이에 기록이
      // 통째로 실패하면 안 되고, 원칙 답을 버려서도 안 된다. 맥락 칸에 담아 저장하고,
      // 그렇게 했다는 사실을 돌려준다(화면이 밝힌다). 읽을 때는 두 자리를 다 본다.
      if (!/principle_answers/.test(String(error && error.message))) throw error;
      var fallback = Object.assign({}, row, { context: Object.assign({}, row.context, { principleAnswers: row.principle_answers }) });
      delete fallback.principle_answers;
      var again = await request("/rest/v1/" + RECORDS, {
        method: "POST",
        headers: headers(current.access_token, { Prefer: "return=representation" }),
        body: JSON.stringify(fallback),
      });
      return Object.assign({}, again && again[0], { principleAnswersFallback: true });
    }
  }

  // 원칙 답이 어느 자리에 있든 한 자리로 모은다. null은 1단계가 생기기 전의 기록이다.
  function normalizeRecord(row) {
    if (!row) return row;
    var answers = Array.isArray(row.principle_answers) ? row.principle_answers
      : row.context && Array.isArray(row.context.principleAnswers) ? row.context.principleAnswers
      : null;
    return Object.assign({}, row, { principle_answers: answers });
  }

  async function loadRecords(options) {
    options = options || {};
    var current = await session();
    var query = "/rest/v1/" + RECORDS + "?select=*&user_id=eq." + encodeURIComponent(current.user.id);
    if (options.holdingId) query += "&holding_id=eq." + encodeURIComponent(options.holdingId);
    // 아직 돌아와야 하는 보류 기록만. 표면화 방식은 다음 단계가 정하지만 질의는 지금 있다.
    if (options.openDeferred) query += "&decision=eq.deferred&resolved_at=is.null";
    query += "&order=decided_at.desc&limit=" + (options.limit || 100);
    var rows = await request(query, { method: "GET", headers: headers(current.access_token) });
    return (rows || []).map(normalizeRecord);
  }

  // 보류를 닫는다. 결정에 이르렀으면 그 기록을 가리키고, 그냥 접었으면 가리킬 것이 없다.
  async function resolveDeferred(id, supersededBy) {
    var current = await session();
    await request("/rest/v1/" + RECORDS + "?id=eq." + encodeURIComponent(id), {
      method: "PATCH",
      headers: headers(current.access_token, { Prefer: "return=minimal" }),
      body: JSON.stringify({ resolved_at: new Date().toISOString(), superseded_by: supersededBy || null }),
    });
    return true;
  }

  // 보류는 결정이 아니다. 나중에 "정한 조건을 지켰는가"를 셀 때 분모에 넣으면 안 된다.
  function countsAsDecision(record) {
    return Boolean(record) && record.decision !== "deferred";
  }

  async function deleteRecord(id) {
    var current = await session();
    await request("/rest/v1/" + RECORDS + "?id=eq." + encodeURIComponent(id), {
      method: "DELETE",
      headers: headers(current.access_token, { Prefer: "return=minimal" }),
    });
    return true;
  }

  // ── 작성 중인 초안 ───────────────────────────────────────────────────────
  // 서버로 가지 않고 이 기기에만 둔다. 새로 고침이나 실수로 창을 닫는 것에서 살아남기
  // 위한 것이다 — 예전에는 결정 기록 초안이 메모리에만 있어 새로 고치면 통째로 사라졌다.
  // 계정마다 따로 둔다: 같은 브라우저를 두 사람이 쓰면 남의 초안이 보이면 안 된다.
  function draftKey(name) {
    var current = AccountStore.auth && AccountStore.auth();
    var who = current && current.user && current.user.id ? current.user.id : "anon";
    return "journal.draft." + name + "." + who;
  }
  function loadDraft(name) {
    try { var raw = localStorage.getItem(draftKey(name)); return raw ? JSON.parse(raw) : null; }
    catch (error) { return null; }
  }
  function saveDraft(name, value) {
    try { localStorage.setItem(draftKey(name), JSON.stringify(value)); return true; }
    catch (error) { return false; }
  }
  function clearDraft(name) {
    try { localStorage.removeItem(draftKey(name)); } catch (error) { /* 지울 수 없어도 저장은 이미 끝났다 */ }
  }

  global.JournalStore = {
    PHILOSOPHY: PHILOSOPHY,
    REVISIONS: REVISIONS,
    RECORDS: RECORDS,
    PRINCIPLES_VERSION: PRINCIPLES_VERSION,
    LEGACY_QUESTIONS: LEGACY_QUESTIONS,
    KINDS: KINDS,
    newPrincipleId: newPrincipleId,
    normalizePrinciples: normalizePrinciples,
    savePrinciples: savePrinciples,
    principleAnswer: principleAnswer,
    loadDraft: loadDraft,
    saveDraft: saveDraft,
    clearDraft: clearDraft,
    loadPhilosophy: loadPhilosophy,
    savePhilosophy: savePhilosophy,
    loadRevisions: loadRevisions,
    saveRecord: saveRecord,
    loadRecords: loadRecords,
    resolveDeferred: resolveDeferred,
    countsAsDecision: countsAsDecision,
    deleteRecord: deleteRecord,
  };
})(window);
