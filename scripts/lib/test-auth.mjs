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
    const clear = Storage.prototype.clear;
    Storage.prototype.clear = function () {
      if (this !== localStorage) return clear.call(this);
      clear.call(this);
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
