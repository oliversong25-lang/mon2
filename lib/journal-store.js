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
    return { answers: (row && row.answers) || {}, updatedAt: (row && row.updated_at) || null, exists: Boolean(row) };
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

  async function saveRecord(record) {
    var current = await session();
    var row = {
      user_id: current.user.id,
      decided_at: record.decidedAt || new Date().toISOString(),
      action: record.action,
      reasoning: String(record.reasoning || ""),
      expectation: String(record.expectation || ""),
      uncertainty: String(record.uncertainty || ""),
      falsification_kind: record.falsificationKind === "machine" ? "machine" : "human",
      falsification_text: String(record.falsificationText || ""),
      falsification_rule: record.falsificationRule || null,
      // 보유 자산이 없는 결정(사지 않기로 함)도 저장돼야 한다. 빈 문자열을 null로 바꿔
      // 붙은 것과 안 붙은 것을 데이터에서 구분한다.
      holding_id: record.holdingId || null,
      holding_label: String(record.holdingLabel || ""),
      context: record.context || {},
    };
    var saved = await request("/rest/v1/" + RECORDS, {
      method: "POST",
      headers: headers(current.access_token, { Prefer: "return=representation" }),
      body: JSON.stringify(row),
    });
    return saved && saved[0];
  }

  async function loadRecords(options) {
    options = options || {};
    var current = await session();
    var query = "/rest/v1/" + RECORDS + "?select=*&user_id=eq." + encodeURIComponent(current.user.id);
    if (options.holdingId) query += "&holding_id=eq." + encodeURIComponent(options.holdingId);
    query += "&order=decided_at.desc&limit=" + (options.limit || 100);
    return await request(query, { method: "GET", headers: headers(current.access_token) });
  }

  async function deleteRecord(id) {
    var current = await session();
    await request("/rest/v1/" + RECORDS + "?id=eq." + encodeURIComponent(id), {
      method: "DELETE",
      headers: headers(current.access_token, { Prefer: "return=minimal" }),
    });
    return true;
  }

  global.JournalStore = {
    PHILOSOPHY: PHILOSOPHY,
    REVISIONS: REVISIONS,
    RECORDS: RECORDS,
    loadPhilosophy: loadPhilosophy,
    savePhilosophy: savePhilosophy,
    loadRevisions: loadRevisions,
    saveRecord: saveRecord,
    loadRecords: loadRecords,
    deleteRecord: deleteRecord,
  };
})(window);
