// Supabase의 anon key는 브라우저에 공개되는 값이다. 데이터 격리는 이 키가 아니라
// supabase/schema.sql의 RLS 정책이 담당한다. service_role key는 여기에 넣지 않는다.
window.SUPABASE_CONFIG = Object.freeze({
  url: "https://jsbxqzrkawnxmjadtwgo.supabase.co",
  anonKey: "sb_publishable__FTVy-UZbEmj_2MK--vyXw_x5IsNIe8",
});
