// 기록 테이블의 목 서버. `test-auth.mjs`의 목은 `user_asset_sessions`만 가로채므로,
// 원칙과 결정 기록의 **왕복**은 검증되지 않은 채 남는다 — 화면이 그려지는 것과 저장한
// 것이 다시 읽히는 것은 다른 이야기다.
//
// user_id로 거르는 이유는 격리를 흉내 내려는 것이 아니다. 그건 실제 계정 두 개로만
// 확인할 수 있고 `test-journal-rls.mjs`가 한다. 여기서 거르는 이유는 **앱이 보내는
// 질의가 실제로 user_id를 담고 있는지**를 이 목이 드러내기 때문이다 — 안 담으면
// 결과가 비어 시험이 깨진다.
export async function installJournalMock(page) {
  await page.addInitScript(() => {
    // 새로 고침을 넘어 살아 있어야 한다. addInitScript는 이동할 때마다 다시 도는데,
    // 메모리에만 두면 저장한 것이 새로 고침에서 사라져 **앱의 결함처럼 보인다.**
    const KEY = "assetflow.test.journal";
    const load = () => {
      try { return JSON.parse(sessionStorage.getItem(KEY) || "null"); }
      catch (error) { return null; }
    };
    const store = load() || { user_investment_philosophy: [], user_philosophy_revisions: [], user_decision_records: [] };
    const persist = () => sessionStorage.setItem(KEY, JSON.stringify(store));
    let seq = Number(sessionStorage.getItem(KEY + ".seq") || 0);
    window.__journalStore = store;

    const previous = window.fetch.bind(window);
    window.fetch = (input, options) => {
      const url = String(input);
      const match = url.match(/^https:\/\/test\.supabase\.co\/rest\/v1\/(user_investment_philosophy|user_philosophy_revisions|user_decision_records)(\?(.*))?$/);
      if (!match) return previous(input, options);

      const table = match[1];
      const params = new URLSearchParams(match[3] || "");
      const method = (options && options.method) || "GET";
      const body = options && options.body ? JSON.parse(options.body) : null;
      const prefer = (options && options.headers && options.headers.Prefer) || "";
      const reply = (payload, status) => Promise.resolve(new Response(
        payload === null ? "" : JSON.stringify(payload),
        { status: status || 200, headers: { "Content-Type": "application/json" } }
      ));

      // eq.<value> 형태의 필터만 쓴다. 앱이 그것만 보내기 때문이고, 더 흉내 내면
      // 목이 앱보다 똑똑해져서 앱의 실수를 덮는다.
      const matches = (row) => {
        for (const [key, raw] of params.entries()) {
          if (["select", "order", "limit", "on_conflict"].includes(key)) continue;
          if (!raw.startsWith("eq.")) continue;
          if (String(row[key]) !== raw.slice(3)) return false;
        }
        return true;
      };

      if (method === "GET") {
        let rows = store[table].filter(matches);
        const order = params.get("order");
        if (order) {
          const [field, direction] = order.split(".");
          rows = rows.slice().sort((a, b) => (direction === "desc" ? 1 : -1) * (String(a[field]) < String(b[field]) ? 1 : -1));
        }
        const limit = Number(params.get("limit") || 0);
        if (limit > 0) rows = rows.slice(0, limit);
        return reply(rows);
      }

      if (method === "POST") {
        const now = new Date(Date.now() + (seq += 1)).toISOString();
        const row = Object.assign({ id: `${table}-${seq}` }, body);
        if (table === "user_philosophy_revisions") row.changed_at = row.changed_at || now;
        if (table === "user_decision_records") row.decided_at = row.decided_at || now;
        if (params.get("on_conflict") === "user_id") {
          const at = store[table].findIndex((existing) => existing.user_id === row.user_id);
          if (at >= 0) store[table][at] = Object.assign({}, store[table][at], row);
          else store[table].push(row);
        } else {
          store[table].push(row);
        }
        persist();
        sessionStorage.setItem(KEY + ".seq", String(seq));
        return reply(prefer.includes("return=representation") ? [row] : null, 201);
      }

      if (method === "DELETE") {
        store[table] = store[table].filter((row) => !matches(row));
        persist();
        return reply(null, 204);
      }

      return reply({ message: `목이 다루지 않는 메서드: ${method}` }, 405);
    };
  });
}
