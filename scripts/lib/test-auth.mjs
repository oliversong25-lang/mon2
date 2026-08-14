// 기존 화면 회귀 테스트는 인증 자체가 아니라 입력·계산을 검증한다. 테스트 계정 토큰을
// 네트워크에서 만들지 않고 보호 화면만 통과시키며, localStorage.clear()가 세션은 지워도
// 테스트 인증 표식은 보존하게 한다. 실제 RLS는 test-rls.mjs가 별도로 검증한다.
export async function installTestAuth(page) {
  await page.addInitScript(() => {
    Object.defineProperty(window, "SUPABASE_CONFIG", { value: { url: "https://test.supabase.co", anonKey: "test-anon-key" }, writable: false, configurable: false });
    const originalFetch = window.fetch.bind(window);
    window.fetch = (input, options) => {
      const url = String(input);
      if (url.startsWith("https://test.supabase.co/rest/v1/user_asset_sessions")) {
        const method=options?.method||"GET";
        if(method==="GET"){
          const saved=sessionStorage.getItem("assetflow.test.remote");
          const rows=saved?[{schema_version:JSON.parse(saved).schema||7,payload:JSON.parse(saved),updated_at:new Date().toISOString()}]:[];
          return Promise.resolve(new Response(JSON.stringify(rows),{status:200,headers:{"Content-Type":"application/json"}}));
        }
        if(options?.body){const row=JSON.parse(options.body);sessionStorage.setItem("assetflow.test.remote",JSON.stringify(row.payload));}
        return Promise.resolve(new Response("",{status:201,headers:{"Content-Type":"application/json"}}));
      }
      return originalFetch(input, options);
    };
    const auth = JSON.stringify({ access_token: "test-token", refresh_token: "test-refresh", expires_at: 4102444800, user: { id: "00000000-0000-0000-0000-000000000001", email: "test@example.com" } });
    const id = "00000000-0000-0000-0000-000000000001";
    // 로컬에 쓴 세션은 목 원격에도 같이 반영한다. 실제 앱은 로컬 저장과 원격 저장을
    // 함께 하는데, 테스트가 localStorage만 심어 두면 다음 로드에서 원격에 남아 있던
    // 옛 값이 이겨 방금 심은 세션이 사라진다 — 화면에는 앞선 테스트의 자산이 그대로
    // 남고, 실패는 엉뚱한 곳에서 뜬다(실제로 '확인이 필요한 항목' 검사가 그렇게 깨졌다).
    const setItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function (key, value) {
      setItem.call(this, key, value);
      if (this === localStorage && key === "assetInput.session") {
        sessionStorage.setItem("assetflow.test.remote", value);
      }
    };
    const clear = Storage.prototype.clear;
    Storage.prototype.clear = function () {
      if (this !== localStorage) return clear.call(this);
      clear.call(this);
      // 목 원격 사본도 함께 비운다. 기존 회귀 테스트는 localStorage.clear()를 "깨끗한
      // 상태에서 시작"이라는 뜻으로 쓰는데, 원격 사본이 남아 있으면 다음 로드에서 앱이
      // 직전 세션을 되살려 이전 테스트의 입력이 그대로 이어진다 — 실제로 통합 테스트
      // 두 건이 그렇게 깨졌다(초기화한 줄 알았던 화면에 옛 평가금액이 남아 있었다).
      sessionStorage.removeItem("assetflow.test.remote");
      this.setItem("assetflow.auth", auth);
      this.setItem("assetInput.owner", id);
    };
    localStorage.setItem("assetflow.auth", auth);
    localStorage.setItem("assetInput.owner", id);
  });
}

export async function launchTestBrowser(chromium) {
  try { return await chromium.launch(); }
  catch (error) {
    if (!/Executable doesn't exist/.test(error.message)) throw error;
    return chromium.launch({ channel: "chrome" });
  }
}
