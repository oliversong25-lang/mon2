// 인증 화면·보호 경로·원격 조회 실패·오프라인 대기열을 실제 브라우저에서 검증한다.
// 계정 간 RLS 자체는 실제 Supabase가 필요한 test-rls.mjs가 담당한다.
import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { once } from "node:events";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import { launchTestBrowser } from "./lib/test-auth.mjs";

const ROOT=resolve(dirname(fileURLToPath(import.meta.url)),"..");
const PORT=4340,BASE=`http://127.0.0.1:${PORT}`;
const server=spawn(process.platform==="win32"?"python":"python3",["-m","http.server",String(PORT),"--bind","127.0.0.1"],{cwd:ROOT,stdio:"pipe"});
await Promise.race([once(server.stdout,"data"),once(server.stderr,"data"),new Promise(done=>setTimeout(done,800))]);
const browser=await launchTestBrowser(chromium);
let failures=0;
function check(ok,label){console.log(`${ok?"PASS":"FAIL"}  ${label}`);if(!ok)failures++;}
async function configured(page){await page.addInitScript(()=>Object.defineProperty(window,"SUPABASE_CONFIG",{value:{url:"https://test.supabase.co",anonKey:"anon"},writable:false,configurable:false}));}
try{
  const direct=await browser.newPage();await configured(direct);await direct.goto(`${BASE}/home.html`);await direct.waitForURL(/login\.html/);check(/return=home\.html/.test(direct.url()),"미로그인 home.html 직접 접근이 로그인으로 차단됨");
  for(const path of ["assets.html","asset-input.html"]){const page=await browser.newPage();await configured(page);await page.goto(`${BASE}/${path}`);await page.waitForURL(/login\.html/);check(page.url().includes(`return=${encodeURIComponent(path)}`),`미로그인 ${path} 직접 접근이 차단됨`);await page.close();}

  const page=await browser.newPage();await configured(page);
  let offline=false;
  await page.route("https://test.supabase.co/**",async route=>{
    if(offline)return route.abort("internetdisconnected");
    const request=route.request(),url=request.url();
    if(url.includes("/auth/v1/token"))return route.fulfill({status:200,contentType:"application/json",body:JSON.stringify({access_token:"token-a",refresh_token:"refresh-a",expires_in:3600,user:{id:"aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",email:"a@example.com"}})});
    if(url.includes("/rest/v1/user_asset_sessions")&&request.method()==="GET")return route.fulfill({status:200,contentType:"application/json",body:"[]"});
    if(url.includes("/rest/v1/user_asset_sessions"))return route.fulfill({status:201,body:""});
    return route.fulfill({status:200,contentType:"application/json",body:"{}"});
  });
  await page.goto(`${BASE}/login.html`);
  await page.evaluate(()=>localStorage.setItem("assetInput.session",JSON.stringify({schema:7,assets:[{id:"legacy",group:"cash",fields:{amount:"999"}}]})));
  await page.getByLabel("이메일").fill("a@example.com");await page.getByLabel("비밀번호").fill("password1");await page.getByRole("button",{name:"로그인",exact:true}).click();
  await page.waitForURL(/asset-input\.html/);await page.waitForLoadState("domcontentloaded");
  const firstSession=await page.evaluate(()=>JSON.parse(localStorage.getItem("assetInput.session")||"null"));
  check(!firstSession||!firstSession.assets?.length,"계정 행이 없으면 도입 전 localStorage 자산을 폐기함");

  offline=true;
  await page.evaluate(()=>SessionStore.write({schema:7,selectedGroups:["cash"],currentGroupIndex:0,assets:[{id:"offline",group:"cash",fields:{currency:"KRW",amount:"5000"},autoFields:{},isEstimated:false}],snapshots:[{date:"2026-08-11",total:5000}]}));
  await page.waitForTimeout(1200);
  const offlineState=await page.evaluate(()=>({session:JSON.parse(localStorage.getItem("assetInput.session")),pending:[...Object.keys(localStorage)].find(key=>key.startsWith("assetflow.pending.")),notice:document.getElementById("account-sync-status")?.textContent||""}));
  check(offlineState.session.assets[0].id==="offline"&&Boolean(offlineState.pending),"네트워크 실패 시 입력과 스냅샷이 계정별 로컬 대기열에 보존됨");
  check(offlineState.notice.includes("저장하지 못했어요"),"저장 실패를 사용자에게 알림");
} finally {await browser.close();server.kill();}
if(failures)process.exit(1);
