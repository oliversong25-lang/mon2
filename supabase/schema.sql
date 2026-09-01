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
-- `action`은 사용자가 당시 행동을 자기 말로 남긴다. 미리 정한 분류에 맞추게 하면
-- "안 팔기로 했다"처럼 중요한 결정의 맥락이 선택지 바깥에서 사라진다.
--
-- `holding_id`는 null을 허용한다. 사지 않기로 한 결정에는 붙일 보유 자산이 없다.
-- `holding_label`을 따로 두는 이유는 자산이 나중에 지워져도 기록이 읽혀야 하기 때문이다.
--
-- `context`는 앱이 채운다. 사용자가 타자로 치는 것은 행동 한 칸과 근거 네 칸뿐이다 —
-- 마찰이 의사결정 기록이 실패하는 유일한 이유이고, 한 건에 10분이 걸리면 아무도 쓰지 않는다.
create table if not exists public.user_decision_records (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references auth.users(id) on delete cascade,
  decided_at timestamptz not null default now(),
  action text not null,
  reasoning text not null default '',
  expectation text not null default '',
  uncertainty text not null default '',
  -- 반증 조건은 두 종류를 **구분해서** 담는다. 기계가 확인할 수 있는 것과 사람만 판단할
  -- 수 있는 것은 이후 단계에서 다르게 다뤄야 한다.
  falsification_kind text not null default 'human' check (falsification_kind in ('machine', 'human')),
  falsification_text text not null default '',
  falsification_rule jsonb,
  holding_id text,
  holding_label text not null default '',
  context jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

create index if not exists user_decision_records_user_time
  on public.user_decision_records (user_id, decided_at desc);
create index if not exists user_decision_records_holding
  on public.user_decision_records (user_id, holding_id);

-- 초기 버전의 네 가지 선택 제한을 이미 만든 프로젝트에서도 제거한다.
alter table public.user_decision_records
  drop constraint if exists user_decision_records_action_check;

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
