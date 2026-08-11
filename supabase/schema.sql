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
