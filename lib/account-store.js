// 인증과 계정별 세션 동기화의 단일 출처. Supabase SDK를 번들에 넣지 않고 Auth/REST
// 공개 API만 호출해 기존의 빌드 없는 정적 사이트 구조를 유지한다.
(function (global) {
  "use strict";

  var AUTH_KEY = "assetflow.auth";
  var OWNER_KEY = "assetInput.owner";
  var SESSION_KEY = "assetInput.session";
  var TABLE = "user_asset_sessions";
  var saveTimer = null;
  var saving = false;
  var queued = null;
  var bootPromise = null;

  function config() {
    var value = global.SUPABASE_CONFIG || {};
    var ready = /^https:\/\/.+\.supabase\.co$/.test(value.url || "") && value.anonKey && value.anonKey !== "YOUR_SUPABASE_ANON_KEY";
    return { url: String(value.url || "").replace(/\/$/, ""), anonKey: String(value.anonKey || ""), ready: Boolean(ready) };
  }

  function parse(key) {
    try { return JSON.parse(localStorage.getItem(key) || "null"); }
    catch (error) { console.error("[계정 저장] 로컬 데이터 해석 실패:", key, error); return null; }
  }

  function auth() { return parse(AUTH_KEY); }
  function userId() { return auth() && auth().user && auth().user.id || null; }
  function pendingKey(id) { return "assetflow.pending." + id; }
  function returnUrl() { return location.pathname.split("/").pop() + location.search + location.hash; }

  function headers(token, extra) {
    var cfg = config();
    return Object.assign({ apikey: cfg.anonKey, Authorization: "Bearer " + (token || cfg.anonKey), "Content-Type": "application/json" }, extra || {});
  }

  async function request(path, options) {
    var cfg = config();
    if (!cfg.ready) throw Object.assign(new Error("Supabase 연결 정보가 설정되지 않았습니다."), { code: "config" });
    var response;
    try { response = await fetch(cfg.url + path, options); }
    catch (error) { throw Object.assign(new Error("네트워크에 연결할 수 없습니다."), { code: "network", cause: error }); }
    var text = await response.text();
    var body = null;
    try { body = text ? JSON.parse(text) : null; }
    catch (error) { body = { message: text || "응답을 해석하지 못했습니다." }; }
    if (!response.ok) {
      var message = body && (body.msg || body.message || body.error_description || body.error) || "요청을 처리하지 못했습니다.";
      throw Object.assign(new Error(message), { code: body && (body.code || body.error_code) || String(response.status), status: response.status });
    }
    return body;
  }

  function persistAuth(value) {
    var normalized = Object.assign({}, value, {
      expires_at: value.expires_at || Math.floor(Date.now() / 1000) + Number(value.expires_in || 3600),
    });
    localStorage.setItem(AUTH_KEY, JSON.stringify(normalized));
    return normalized;
  }

  async function signIn(email, password) {
    var cfg = config();
    var result = await request("/auth/v1/token?grant_type=password", {
      method: "POST", headers: headers(null), body: JSON.stringify({ email: email, password: password }),
    });
    persistAuth(result);
    // 로그인 기능 도입 전의 출처별 localStorage는 계정 데이터로 승격하지 않는다.
    // 서버에 행이 없으면 반드시 비워 새 계정이 예전 테스트 값을 물려받지 않게 한다.
    return loadRemote({ required: true });
  }

  async function signUp(email, password) {
    var result = await request("/auth/v1/signup", {
      method: "POST", headers: headers(null), body: JSON.stringify({ email: email, password: password }),
    });
    // 이메일 확인을 켠 프로젝트는 session이 오지 않는다. 이 경우 확인 안내만 하고
    // 로그인된 것처럼 다음 화면으로 보내지 않는다.
    if (!result.access_token) return { confirmationRequired: true, user: result.user };
    persistAuth(result);
    var remote = await loadRemote({ required: true });
    return { confirmationRequired: false, user: result.user, remote: remote };
  }

  async function refresh() {
    var current = auth();
    if (!current || !current.refresh_token) throw Object.assign(new Error("로그인이 만료되었습니다."), { code: "expired" });
    var result = await request("/auth/v1/token?grant_type=refresh_token", {
      method: "POST", headers: headers(null), body: JSON.stringify({ refresh_token: current.refresh_token }),
    });
    return persistAuth(result);
  }

  async function validAuth() {
    var current = auth();
    if (!current || !current.user || !current.user.id) return null;
    if (Number(current.expires_at || 0) > Math.floor(Date.now() / 1000) + 30) return current;
    try { return await refresh(); }
    catch (error) { expire(error); return null; }
  }

  function clearAccountCache() {
    localStorage.removeItem(AUTH_KEY);
    localStorage.removeItem(OWNER_KEY);
    localStorage.removeItem(SESSION_KEY);
  }

  function expire(error) {
    console.error("[인증] 로그인 만료:", error);
    clearAccountCache();
    if (!/login\.html$/.test(location.pathname)) location.replace("login.html?expired=1&return=" + encodeURIComponent(returnUrl()));
  }

  async function signOut() {
    var current = auth();
    try {
      if (current && current.access_token) await request("/auth/v1/logout", { method: "POST", headers: headers(current.access_token) });
    } catch (error) {
      // 서버 로그아웃이 실패해도 이 기기 토큰과 자산 사본은 반드시 제거한다.
      console.error("[로그아웃] 서버 처리 실패:", error);
    } finally {
      clearAccountCache();
      location.replace("login.html?signedout=1");
    }
  }

  async function loadRemote(options) {
    options = options || {};
    var current = await validAuth();
    if (!current) throw Object.assign(new Error("로그인이 필요합니다."), { code: "expired" });
    var rows = await request("/rest/v1/" + TABLE + "?select=schema_version,payload,updated_at&user_id=eq." + encodeURIComponent(current.user.id) + "&limit=1", {
      method: "GET", headers: headers(current.access_token),
    });
    var row = rows && rows[0];
    var local = parse(SESSION_KEY);
    if (options.preserveLocalMismatch && row && local && local.schema != null && Number(local.schema) !== Number(row.schema_version)) {
      console.warn("[계정 조회] 로컬과 원격의 스키마 버전이 달라 어느 쪽도 덮어쓰지 않습니다.", { local: local.schema, remote: row.schema_version });
      showStatus("저장된 데이터의 버전이 달라 자동으로 덮어쓰지 않았어요. 현재 기기의 내용을 유지합니다.", "error");
      return { status: "schema-mismatch", session: local, schema: local.schema, remoteSchema: row.schema_version };
    }
    if (row && row.payload && typeof row.payload === "object") {
      localStorage.setItem(SESSION_KEY, JSON.stringify(row.payload));
    } else if (options.required) {
      localStorage.removeItem(SESSION_KEY);
    }
    localStorage.setItem(OWNER_KEY, current.user.id);
    return { status: row ? "ok" : "empty", session: row ? row.payload : null, schema: row ? row.schema_version : null };
  }

  function showStatus(message, kind, retry) {
    if (!document.body) {
      document.addEventListener("DOMContentLoaded", function () { showStatus(message, kind, retry); }, { once: true });
      return;
    }
    var node = document.getElementById("account-sync-status");
    if (!node) {
      node = document.createElement("div");
      node.id = "account-sync-status";
      node.className = "account-status";
      node.setAttribute("role", "status");
      document.body.appendChild(node);
    }
    node.className = "account-status " + (kind || "");
    node.innerHTML = "<span></span>" + (retry ? '<button type="button">다시 시도</button>' : "");
    node.querySelector("span").textContent = message;
    var button = node.querySelector("button");
    if (button) button.addEventListener("click", retry, { once: true });
    node.hidden = false;
  }

  function hideStatus() {
    var node = document.getElementById("account-sync-status");
    if (node) node.hidden = true;
  }

  function showStorageError() { showStatus("이 기기에 입력 내용을 보관하지 못했습니다. 저장 공간을 확인해 주세요.", "error"); }

  async function flush() {
    if (saving || !queued) return;
    var current = await validAuth();
    if (!current) return;
    var value = queued;
    queued = null;
    saving = true;
    try {
      await request("/rest/v1/" + TABLE + "?on_conflict=user_id", {
        method: "POST",
        headers: headers(current.access_token, { Prefer: "resolution=merge-duplicates,return=minimal" }),
        body: JSON.stringify({ user_id: current.user.id, schema_version: Number(value.schema || 0), payload: value, updated_at: new Date().toISOString() }),
      });
      localStorage.removeItem(pendingKey(current.user.id));
      hideStatus();
    } catch (error) {
      queued = value;
      localStorage.setItem(pendingKey(current.user.id), JSON.stringify(value));
      console.error("[계정 저장] Supabase 저장 실패:", error);
      showStatus("저장하지 못했어요. 입력 내용은 이 기기에 보관했으며 연결되면 다시 저장합니다.", "error", flush);
    } finally {
      saving = false;
      if (queued && navigator.onLine) { clearTimeout(saveTimer); saveTimer = setTimeout(flush, 1500); }
    }
  }

  function queueSave(value) {
    var id = userId();
    if (!id) return;
    queued = value;
    try { localStorage.setItem(pendingKey(id), JSON.stringify(value)); }
    catch (error) { showStorageError(); }
    clearTimeout(saveTimer);
    saveTimer = setTimeout(flush, 800);
  }

  function hasLocalAccountSession() {
    var current = auth();
    return Boolean(current && current.user && current.user.id && localStorage.getItem(OWNER_KEY) === current.user.id && localStorage.getItem(SESSION_KEY));
  }

  async function bootstrapProtected() {
    if (bootPromise) return bootPromise;
    bootPromise = (async function () {
      var current = await validAuth();
      if (!current) return false;
      var pending = parse(pendingKey(current.user.id));
      if (pending) queued = pending;
      try {
        // 로그인 화면에서 내려받은 로컬 사본으로 먼저 그리되, 다른 기기 변경분은 곧바로
        // 다시 받아 다음 렌더에 반영한다. 로컬 미저장분이 있으면 원격으로 덮지 않는다.
        if (!queued) {
          var before = localStorage.getItem(SESSION_KEY);
          await loadRemote({ required: true, preserveLocalMismatch: true });
          var after = localStorage.getItem(SESSION_KEY);
          if (before !== after && !sessionStorage.getItem("assetflow.remote-reloaded")) {
            sessionStorage.setItem("assetflow.remote-reloaded", "1");
            location.reload();
            return false;
          }
        } else flush();
      } catch (error) {
        console.error("[계정 조회] Supabase 조회 실패:", error);
        showStatus("계정 데이터를 불러오지 못했어요. 현재 기기에 보관된 내용을 표시합니다.", "error", function () { location.reload(); });
      }
      return true;
    })();
    return bootPromise;
  }

  global.addEventListener("online", function () { if (queued || parse(pendingKey(userId()))) { queued = queued || parse(pendingKey(userId())); flush(); } });

  global.AccountStore = {
    AUTH_KEY: AUTH_KEY, OWNER_KEY: OWNER_KEY, config: config, auth: auth, userId: userId,
    signIn: signIn, signUp: signUp, signOut: signOut, loadRemote: loadRemote,
    queueSave: queueSave, flush: flush, bootstrapProtected: bootstrapProtected,
    hasLocalAccountSession: hasLocalAccountSession, showStatus: showStatus,
    showStorageError: showStorageError, clearAccountCache: clearAccountCache,
  };
})(window);
