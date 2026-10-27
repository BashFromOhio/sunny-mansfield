-- Sunny Mansfield backend. Paste this whole file into Supabase > SQL Editor and click Run.
-- It is safe to run again after changes.

-- Everything people send: stories, flyers, Free Stuff, jobs, spotlights, team applications, ad inquiries.
create table if not exists public.submissions (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  section text not null,
  title text not null check (char_length(title) between 3 and 200),
  summary text check (char_length(summary) <= 2000),
  photo_url text,
  link text,
  name text,
  role text,
  business text,
  town text,
  event_date date,
  price text,
  contact text,
  email text,
  status text not null default 'pending' check (status in ('pending', 'approved', 'rejected')),
  reviewed_at timestamptz,
  reviewed_by text
);
-- What the sender confirmed (rules, photo permission, a guardian's OK for minors). Editors only, never published.
alter table public.submissions add column if not exists consent text check (char_length(consent) <= 1000);
create index if not exists submissions_status_idx on public.submissions (status, created_at desc);

-- People who can use the admin page, and people whose posts skip the review queue.
create table if not exists public.admins (email text primary key);
create table if not exists public.verified (
  email text primary key,
  name text,
  role text,
  added_at timestamptz not null default now()
);

-- The signed-in person's email, but only once they have confirmed it. Editor and verified powers hang on this,
-- so an account someone opened with another person's address and never confirmed gets nothing.
create or replace function public.confirmed_email() returns text
language sql stable security definer set search_path = public as $$
  select lower(u.email) from auth.users u where u.id = auth.uid() and u.email_confirmed_at is not null;
$$;

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from admins where lower(email) = public.confirmed_email());
$$;

-- New posts always start in review, unless the sender is signed in with a confirmed email on the verified list.
-- Team applications, ad inquiries, fundraisers, jobs, brackets, headline picks and pasted links always wait for an admin.
create or replace function public.on_submit() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  signed_in text := lower(auth.jwt() ->> 'email');
  confirmed text := public.confirmed_email();
begin
  new.status := 'pending';
  new.reviewed_at := null;
  new.reviewed_by := null;
  new.created_at := now();
  if signed_in is not null then
    new.email := signed_in;  -- a signed-in sender can't claim someone else's address
    if new.section not in ('Join the team', 'Advertise', 'Fundraiser', 'Job', 'Bracket', 'Headline', 'Link')
       and confirmed = signed_in
       and exists (select 1 from verified where lower(email) = confirmed) then
      new.status := 'approved';
      new.reviewed_at := now();
      new.reviewed_by := 'verified';
    end if;
  end if;
  return new;
end $$;
drop trigger if exists submissions_on_submit on public.submissions;
create trigger submissions_on_submit before insert on public.submissions
  for each row execute function public.on_submit();

alter table public.submissions enable row level security;
alter table public.admins enable row level security;
alter table public.verified enable row level security;

drop policy if exists "anyone can submit" on public.submissions;
create policy "anyone can submit" on public.submissions for insert to anon, authenticated with check (true);
drop policy if exists "admins read" on public.submissions;
create policy "admins read" on public.submissions for select to authenticated using (public.is_admin());
drop policy if exists "admins review" on public.submissions;
create policy "admins review" on public.submissions for update to authenticated using (public.is_admin()) with check (public.is_admin());
drop policy if exists "admins delete" on public.submissions;
create policy "admins delete" on public.submissions for delete to authenticated using (public.is_admin());

drop policy if exists "admins see admins" on public.admins;
create policy "admins see admins" on public.admins for select to authenticated using (public.is_admin());

drop policy if exists "admins manage verified" on public.verified;
create policy "admins manage verified" on public.verified for all to authenticated using (public.is_admin()) with check (public.is_admin());

-- What the public site reads: approved posts only, without anyone's email address or phone.
-- (The old Buy & Sell "contact" column is no longer published: Free Stuff and jobs use private replies instead.)
drop view if exists public.published;
create view public.published as
  select id, created_at, section, title, summary, photo_url, link, name, role, business, town, event_date, price
  from public.submissions
  where status = 'approved' and section not in ('Join the team', 'Advertise');
grant select on public.published to anon, authenticated;

-- Private replies to Free Stuff and job posts (the "reply" Edge Function writes here to stop spam).
-- Only scrambled (hashed) senders are kept, never the message, and nobody but the function can read it.
create table if not exists public.relay_log (
  id bigint generated always as identity primary key,
  created_at timestamptz not null default now(),
  post_id uuid not null,
  ip_hash text not null,
  sender_hash text not null
);
create index if not exists relay_log_ip_idx on public.relay_log (ip_hash, created_at desc);
create index if not exists relay_log_post_idx on public.relay_log (post_id, created_at desc);
alter table public.relay_log enable row level security;  -- no policies: closed to the public

-- Housekeeping: declined posts go after 30 days, reply records after 30 days, and the poster's email comes off
-- Free Stuff and job posts after 60 days (replies stop at 30). The consent record stays as proof of permission.
create or replace function public.tidy_up() returns void
language sql security definer set search_path = public as $$
  delete from submissions where status = 'rejected' and created_at < now() - interval '30 days';
  delete from relay_log where created_at < now() - interval '30 days';
  update submissions set email = null
    where section in ('Free Stuff', 'Job', 'Buy & Sell') and email is not null and created_at < now() - interval '60 days';
$$;
revoke all on function public.tidy_up() from public, anon, authenticated;
-- Run it every night at 3 a.m. if the pg_cron extension is on (Database > Extensions > pg_cron). Safe to skip.
do $$ begin
  if exists (select 1 from pg_extension where extname = 'pg_cron') then
    perform cron.schedule('sunny-tidy-up', '0 8 * * *', 'select public.tidy_up()');
  end if;
end $$;

-- Photo and flyer uploads: public to view, 6 MB images only, anyone can add, only admins can remove.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('photos', 'photos', true, 6291456, array['image/jpeg', 'image/png', 'image/webp', 'image/gif'])
on conflict (id) do update set public = true, file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;
drop policy if exists "anyone can upload photos" on storage.objects;
create policy "anyone can upload photos" on storage.objects for insert to anon, authenticated
  with check (bucket_id = 'photos');
drop policy if exists "admins remove photos" on storage.objects;
create policy "admins remove photos" on storage.objects for delete to authenticated
  using (bucket_id = 'photos' and public.is_admin());

-- Last step: put your own email here, then run this file.
-- insert into public.admins (email) values ('you@example.com') on conflict do nothing;
