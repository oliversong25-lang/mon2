-- 공개 anon key를 가진 클라이언트가 테이블에 직접 접근하므로 RLS가 데이터 격리의 경계다.
create table if not exists public.user_asset_sessions (
  user_id uuid primary key references auth.users(id) on delete cascade,
  schema_version integer not null,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

alter table public.user_asset_sessions enable row level security;
alter table public.user_asset_sessions force row level security;

revoke all on table public.user_asset_sessions from anon;
grant select, insert, update, delete on table public.user_asset_sessions to authenticated;

drop policy if exists "본인 세션 조회" on public.user_asset_sessions;
drop policy if exists "본인 세션 등록" on public.user_asset_sessions;
drop policy if exists "본인 세션 수정" on public.user_asset_sessions;
drop policy if exists "본인 세션 삭제" on public.user_asset_sessions;

create policy "본인 세션 조회" on public.user_asset_sessions
for select to authenticated using ((select auth.uid()) = user_id);

create policy "본인 세션 등록" on public.user_asset_sessions
for insert to authenticated with check ((select auth.uid()) = user_id);

create policy "본인 세션 수정" on public.user_asset_sessions
for update to authenticated using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

create policy "본인 세션 삭제" on public.user_asset_sessions
for delete to authenticated using ((select auth.uid()) = user_id);

-- payload 안에 assets·selectedGroups·진행 상태·snapshots를 한 문서로 둔다.
-- 현재 화면이 이 단위를 원자적으로 읽고 쓰므로 자산과 스냅샷이 서로 다른 시점으로 갈라지지 않는다.

-- ─────────────────────────────────────────────────────────────────────────────
-- 투자 원칙과 의사결정 기록
--
-- 목적은 결과를 채점하는 것이 아니다. 좋은 결정이 나쁜 결과를 낳고 나쁜 결정이 좋은
-- 결과를 낳으므로, 결과로 판단력을 매기면 틀린 것을 배운다. 목적은 **결정 당시의 근거를
-- 되찾을 수 있게** 하는 것이다. 사람은 결과를 알기 전에 무엇을 생각했는지 실제로 기억하지
-- 못하고 기억은 결과 쪽으로 재구성된다. 글로 남긴 기록만 그것을 견딘다.
--
-- 원칙이 먼저인 이유: 매도 조건은 원칙에서 나온다. 틀이 없는 상태에서 "언제 팔 것인가"를
-- 물으면 임의로 적게 되고, 임의로 적은 조건은 지켜지지 않는다.
-- ─────────────────────────────────────────────────────────────────────────────

-- 현재 원칙 한 벌. 사용자가 쓴 자유 문장만 담고 우리가 고른 선택지는 담지 않는다.
create table if not exists public.user_investment_philosophy (
  user_id uuid primary key references auth.users(id) on delete cascade,
  answers jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

-- 원칙이 바뀐 기록. **덧붙이기만 한다.**
--
-- 하락장에서 원칙을 고치는 것은 흔한 실패이고, 언제 고쳤는지 자체가 정보다. 그러나 그것을
-- 해석하지는 않는다 — 저장만 한다. 판단은 사람이 나중에 자기 기록을 보고 한다.
create table if not exists public.user_philosophy_revisions (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  changed_at timestamptz not null default now(),
  reason text not null default '',
  answers jsonb not null default '{}'::jsonb
);

create index if not exists user_philosophy_revisions_user_time
  on public.user_philosophy_revisions (user_id, changed_at desc);

-- 의사결정 기록.
--
-- 형태가 트랙 29와 다르다. 그때는 근거를 자유 문장 네 칸으로 받았는데, "무엇이 불확실한가"는
-- 얼마든지 뭉뚱그려 답할 수 있고 대개 그렇게 된다. 지금은 **검토 중인 행동 하나를 적고,
-- 해야 할 이유와 하지 말아야 할 이유를 각각 목록으로 쓴 뒤에** 결정을 고른다. 반대 이유를
-- 채우지 못하는 사람은 그 결정을 살펴보지 않은 것이고, 목록은 물러설 자리를 남기지 않는다.
--
-- ── 이유를 낱개로 담는 이유 ────────────────────────────────────────────────
-- 한 덩어리 문장으로 담으면 나중에 "그때 세 번째 이유가 어떻게 됐나"를 물을 수 없다.
-- 각 항목이 자기 id를 갖고, 반증 표시도 항목에 붙는다.
--
-- ── 반증 조건은 반대 목록에서 나온다 ───────────────────────────────────────
-- 따로 칸을 두지 않는다. 반대 이유 중 "이게 실제로 일어나면 내 판단이 틀린 것"에 표시하면
-- 그것이 반증 조건이다. 자연스러운 자리이고 칸이 하나 줄어든다. 기계가 확인할 수 있는
-- 것과 사람이 판단할 것의 구분은 표시된 항목에 그대로 남는다.
--
-- ── 네 가지 행동 종류가 사라진 이유 ────────────────────────────────────────
-- 위에 행동을 적고 아래에서 결정을 고르면 넷이 다 덮인다.
--   "삼성전자 매수" + 실행 안 함 = 사지 않기로 함
--   "삼성전자 매도" + 실행 안 함 = 계속 들고 있기로 함
--
-- ── 보류는 안 하기로 함과 다르다 ───────────────────────────────────────────
-- `deferred`는 결정에 이르지 못한 것이고 `not_executed`는 결정에 이르렀는데 하지 않기로
-- 한 것이다. 앞의 것은 **열린 고리**라 다시 돌아와야 하고 뒤의 것은 끝난 기록이다.
-- 그래서 상태를 합치지 않고, 나중에 "정한 조건을 지켰는가"를 셀 때 보류는 결정으로
-- 세지 않는다.
create table if not exists public.user_decision_records (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  decided_at timestamptz not null default now(),
  -- 검토 중인 행동. "삼성전자 매수"처럼 한 줄이다.
  action_statement text not null default '',
  -- 이유 목록. 각 항목은 {id, text}이고, 반대 쪽은 {falsifies, kind, rule}을 더 갖는다.
  reasons_for jsonb not null default '[]'::jsonb,
  reasons_against jsonb not null default '[]'::jsonb,
  -- 두 목록을 다 쓴 뒤에 고른다.
  decision text not null check (decision in ('executed', 'not_executed', 'deferred')),
  -- 무엇을 기대했나. 이유가 아니라 **결과에 대한 예상**이라 어느 목록에도 들어가지 않는다.
  -- 목록에 섞으면 나중에 되돌아볼 때 이유와 구분되지 않는다.
  expectation text not null default '',
  -- 보류의 열린 고리. 해소되면 그때 채운다. `decision = 'deferred' and resolved_at is null`이
  -- 아직 돌아와야 하는 기록이다.
  resolved_at timestamptz,
  superseded_by uuid references public.user_decision_records(id) on delete set null,
  holding_id text,
  holding_label text not null default '',
  context jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- 트랙 29 모양으로 이미 적용한 데이터베이스를 위한 전환. 새로 만드는 곳에서는 아무 일도
-- 하지 않는다. 브랜치가 main에 합쳐지기 전이라 지금은 값이 거의 없다.
alter table public.user_decision_records add column if not exists action_statement text not null default '';
alter table public.user_decision_records add column if not exists reasons_for jsonb not null default '[]'::jsonb;
alter table public.user_decision_records add column if not exists reasons_against jsonb not null default '[]'::jsonb;
alter table public.user_decision_records add column if not exists decision text;
alter table public.user_decision_records add column if not exists expectation text not null default '';
alter table public.user_decision_records add column if not exists resolved_at timestamptz;
alter table public.user_decision_records add column if not exists superseded_by uuid references public.user_decision_records(id) on delete set null;
alter table public.user_decision_records drop column if exists action;
alter table public.user_decision_records drop column if exists reasoning;
alter table public.user_decision_records drop column if exists uncertainty;
alter table public.user_decision_records drop column if exists falsification_kind;
alter table public.user_decision_records drop column if exists falsification_text;
alter table public.user_decision_records drop column if exists falsification_rule;

create index if not exists user_decision_records_user_time
  on public.user_decision_records (user_id, decided_at desc);
create index if not exists user_decision_records_holding
  on public.user_decision_records (user_id, holding_id);
-- 아직 돌아와야 하는 보류 기록. 표면화 방식은 다음 단계가 정하지만 자료는 지금 받쳐 둔다.
create index if not exists user_decision_records_open_deferred
  on public.user_decision_records (user_id, decided_at)
  where decision = 'deferred' and resolved_at is null;

alter table public.user_investment_philosophy enable row level security;
alter table public.user_investment_philosophy force row level security;
alter table public.user_philosophy_revisions enable row level security;
alter table public.user_philosophy_revisions force row level security;
alter table public.user_decision_records enable row level security;
alter table public.user_decision_records force row level security;

revoke all on table public.user_investment_philosophy from anon;
revoke all on table public.user_philosophy_revisions from anon;
revoke all on table public.user_decision_records from anon;

grant select, insert, update, delete on table public.user_investment_philosophy to authenticated;
-- 원칙 이력은 고치거나 지울 수 없다. 바뀐 기록을 나중에 손볼 수 있으면 기록이 아니다.
-- Supabase가 authenticated에 이미 준 기본 권한이 남을 수 있으므로 grant만 좁혀 쓰면 안 된다.
revoke all on table public.user_philosophy_revisions from authenticated;
grant select, insert on table public.user_philosophy_revisions to authenticated;
grant select, insert, update, delete on table public.user_decision_records to authenticated;

drop policy if exists "본인 원칙 조회" on public.user_investment_philosophy;
drop policy if exists "본인 원칙 등록" on public.user_investment_philosophy;
drop policy if exists "본인 원칙 수정" on public.user_investment_philosophy;
drop policy if exists "본인 원칙 삭제" on public.user_investment_philosophy;

create policy "본인 원칙 조회" on public.user_investment_philosophy
for select to authenticated using ((select auth.uid()) = user_id);

create policy "본인 원칙 등록" on public.user_investment_philosophy
for insert to authenticated with check ((select auth.uid()) = user_id);

create policy "본인 원칙 수정" on public.user_investment_philosophy
for update to authenticated using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

create policy "본인 원칙 삭제" on public.user_investment_philosophy
for delete to authenticated using ((select auth.uid()) = user_id);

drop policy if exists "본인 원칙 이력 조회" on public.user_philosophy_revisions;
drop policy if exists "본인 원칙 이력 등록" on public.user_philosophy_revisions;

create policy "본인 원칙 이력 조회" on public.user_philosophy_revisions
for select to authenticated using ((select auth.uid()) = user_id);

create policy "본인 원칙 이력 등록" on public.user_philosophy_revisions
for insert to authenticated with check ((select auth.uid()) = user_id);

drop policy if exists "본인 결정 기록 조회" on public.user_decision_records;
drop policy if exists "본인 결정 기록 등록" on public.user_decision_records;
drop policy if exists "본인 결정 기록 수정" on public.user_decision_records;
drop policy if exists "본인 결정 기록 삭제" on public.user_decision_records;

create policy "본인 결정 기록 조회" on public.user_decision_records
for select to authenticated using ((select auth.uid()) = user_id);

create policy "본인 결정 기록 등록" on public.user_decision_records
for insert to authenticated with check ((select auth.uid()) = user_id);

create policy "본인 결정 기록 수정" on public.user_decision_records
for update to authenticated using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

create policy "본인 결정 기록 삭제" on public.user_decision_records
for delete to authenticated using ((select auth.uid()) = user_id);
