// 실제 계정 두 개로 교차 조회까지 수행한다. 정책 SQL이 있다는 사실만 검사하지 않는다.
const required=["SUPABASE_URL","SUPABASE_ANON_KEY","RLS_TEST_A_EMAIL","RLS_TEST_A_PASSWORD","RLS_TEST_B_EMAIL","RLS_TEST_B_PASSWORD"];
const missing=required.filter(key=>!process.env[key]);
if(missing.length){console.error(`[RLS 검증] 환경 변수가 없습니다: ${missing.join(", ")}`);process.exit(1);}
const url=process.env.SUPABASE_URL.replace(/\/$/,"");
const anon=process.env.SUPABASE_ANON_KEY;
const baseHeaders={apikey:anon,"Content-Type":"application/json"};
async function call(path,options={}){const response=await fetch(url+path,{...options,headers:{...baseHeaders,...options.headers}});const text=await response.text();const body=text?JSON.parse(text):null;if(!response.ok)throw new Error(`${response.status} ${body?.message||body?.msg||text}`);return body;}
async function login(email,password){return call("/auth/v1/token?grant_type=password",{method:"POST",body:JSON.stringify({email,password})});}
function auth(token,extra={}){return {...extra,Authorization:`Bearer ${token}`};}
const a=await login(process.env.RLS_TEST_A_EMAIL,process.env.RLS_TEST_A_PASSWORD);
const b=await login(process.env.RLS_TEST_B_EMAIL,process.env.RLS_TEST_B_PASSWORD);
const marker={schema:7,assets:[{id:`rls-${Date.now()}`,group:"cash",fields:{currency:"KRW",amount:"1234"},autoFields:{},isEstimated:false}],snapshots:[{date:"2099-01-01",total:1234}]};
await call("/rest/v1/user_asset_sessions?on_conflict=user_id",{method:"POST",headers:auth(a.access_token,{Prefer:"resolution=merge-duplicates,return=minimal"}),body:JSON.stringify({user_id:a.user.id,schema_version:7,payload:marker})});
const own=await call(`/rest/v1/user_asset_sessions?select=payload&user_id=eq.${a.user.id}`,{headers:auth(a.access_token)});
const crossed=await call(`/rest/v1/user_asset_sessions?select=payload&user_id=eq.${a.user.id}`,{headers:auth(b.access_token)});
let forged=false;
try{await call("/rest/v1/user_asset_sessions",{method:"POST",headers:auth(b.access_token),body:JSON.stringify({user_id:a.user.id,schema_version:7,payload:{schema:7,assets:[]}})});forged=true;}catch(error){if(!/401|403|42501|row-level security/i.test(error.message))throw error;}
if(own.length!==1||crossed.length!==0||forged){console.error(`[RLS 검증] 실패: 본인 ${own.length}건, 타인 ${crossed.length}건, 위조 insert ${forged}`);process.exit(1);}
if(own[0].payload.snapshots?.[0]?.total!==1234){console.error("[RLS 검증] 스냅샷이 계정 데이터에 저장되지 않았습니다.");process.exit(1);}
console.log("[RLS 검증] 통과: A 본인 조회 1건, B의 A 조회 0건, B의 A 행 삽입 차단, 스냅샷 유지");
