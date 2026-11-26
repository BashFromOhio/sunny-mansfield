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
-- When the sender confirmed their email with a code (birthday shout-outs). Only the "verify" function sets it.
alter table public.submissions add column if not exists verified_at timestamptz;
-- Why an editor rejected or took down a post (picked from a list on the admin page). Editors only, never published.
alter table public.submissions add column if not exists review_note text check (char_length(review_note) <= 500);
-- Free Stuff: when the post was marked given away (greyed out for a few days, then down), and when the poster was last
-- emailed the "mark it given away" link (the "given" function sends at most one every 30 minutes).
alter table public.submissions add column if not exists given_at timestamptz;
alter table public.submissions add column if not exists given_link_at timestamptz;
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
-- Team applications, ad inquiries, fundraisers, jobs, birthday shout-outs, brackets, headline picks, pasted links
-- and story removals (Hide) always wait for an admin.
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
  new.verified_at := null;  -- only the "verify" Edge Function can mark an email as confirmed
  if signed_in is not null then
    new.email := signed_in;  -- a signed-in sender can't claim someone else's address
    if new.section not in ('Join the team', 'Advertise', 'Fundraiser', 'Job', 'Home for Rent or Sale', 'Birthday', 'Bracket', 'Headline', 'Link', 'Hide')
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
-- Birthday shout-outs are listed only on the birthday and the day after (Mansfield time), so nobody can pull a list
-- of everyone's birthdays. The site shows each one once, for two days from the first birthday after it was sent.
-- event_date holds the birthday with the year 2000 (the form never asks for the year); Feb 29 is also listed on Feb 28 and Mar 1.
drop view if exists public.published;
create view public.published as
  select id, created_at, section, title, summary, photo_url, link, name, role, business, town, event_date, price, given_at
  from public.submissions
  where status = 'approved' and section not in ('Join the team', 'Advertise')
    and (section <> 'Birthday' or (
      verified_at is not null and event_date is not null and created_at > now() - interval '370 days'
      and to_char(event_date, 'MM-DD') in (
        to_char((now() at time zone 'America/New_York')::date, 'MM-DD'),
        to_char((now() at time zone 'America/New_York')::date - 1, 'MM-DD'),
        case when to_char((now() at time zone 'America/New_York')::date, 'MM-DD') in ('02-28', '03-01') then '02-29' end)));
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

-- Email codes for birthday shout-outs (the "verify" Edge Function writes here). Only a scrambled code is kept,
-- it works for 15 minutes, and nobody but the function can read it.
create table if not exists public.email_codes (
  submission_id uuid primary key references public.submissions (id) on delete cascade,
  code_hash text not null,
  ip_hash text not null,
  tries int not null default 0,
  sends int not null default 1,
  sent_at timestamptz not null default now(),
  expires_at timestamptz not null
);
create index if not exists email_codes_ip_idx on public.email_codes (ip_hash, sent_at desc);
alter table public.email_codes enable row level security;  -- no policies: closed to the public

-- Housekeeping: declined posts go after 30 days, reply records after 30 days, and the poster's email comes off
-- Free Stuff, job and home posts after 60 days (replies stop at 30). Birthday shout-outs go a year and a month after
-- they were sent (they run once), or after 2 days if nobody confirmed the email. The consent record stays as proof of permission.
create or replace function public.tidy_up() returns void
language sql security definer set search_path = public as $$
  delete from submissions where status = 'rejected' and created_at < now() - interval '30 days';
  delete from relay_log where created_at < now() - interval '30 days';
  update submissions set email = null
    where section in ('Free Stuff', 'Job', 'Home for Rent or Sale', 'Buy & Sell') and email is not null and created_at < now() - interval '60 days';
  delete from submissions where section = 'Birthday' and created_at < now() - interval '400 days';
  delete from submissions where section = 'Birthday' and verified_at is null and created_at < now() - interval '2 days';
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

-- Live reach for the Advertise page: page views per day, counted by the site itself.
-- No cookies, no IP addresses, nothing about who visited: only the day (Mansfield time), the page address and a count.
create table if not exists public.page_views (
  day date not null,
  path text not null check (char_length(path) <= 200),
  views integer not null default 0,
  primary key (day, path)
);
alter table public.page_views enable row level security;  -- no policies: only the function and views below touch it

-- Every page calls this once per visit (per browser tab). Only plain site paths count, so junk can't pile up.
create or replace function public.count_view(p text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if p is null or p !~ '^/[A-Za-z0-9/_.~-]{0,199}$' then
    return;
  end if;
  insert into page_views (day, path, views)
  values ((now() at time zone 'America/New_York')::date, p, 1)
  on conflict (day, path) do update set views = page_views.views + 1;
end $$;
revoke all on function public.count_view(text) from public;
grant execute on function public.count_view(text) to anon, authenticated;

-- What the Advertise page shows. Visit sources are counted as "/_from/<source>" (once per visit) and kept out of page totals.
drop view if exists public.reach_daily;
create view public.reach_daily as
  select day, sum(views)::int as views from public.page_views
  where day > (now() at time zone 'America/New_York')::date - 400 and path not like '/\_from/%'
  group by day;
drop view if exists public.reach_pages;
create view public.reach_pages as
  select path, sum(views)::int as views from public.page_views
  where day > (now() at time zone 'America/New_York')::date - 30 and path not like '/\_from/%'
  group by path;
-- Last 30 days in a few numbers: all page views, story pages, the front page, and visits by where they came from.
drop view if exists public.reach_summary;
create view public.reach_summary as
  with recent as (select path, views from public.page_views where day > (now() at time zone 'America/New_York')::date - 30)
  select 'all'::text as k, coalesce(sum(views), 0)::int as views from recent where path not like '/\_from/%'
  union all select 'story', coalesce(sum(views), 0)::int from recent where path like '/s/%'
  union all select 'front', coalesce(sum(views), 0)::int from recent where path = '/'
  union all select substring(path from 8), sum(views)::int from recent where path like '/\_from/%' group by path;
grant select on public.reach_daily, public.reach_pages, public.reach_summary to anon, authenticated;
-- For the Facebook posts the counter powers: page views in the last 7 days (Friday's "most read this week" and the
-- weekly neighbor shout-out) and today so far (the 6 p.m. "trending now" link post).
drop view if exists public.reach_week;
create view public.reach_week as
  select path, sum(views)::int as views from public.page_views
  where day > (now() at time zone 'America/New_York')::date - 7 and path not like '/\_from/%'
  group by path;
drop view if exists public.reach_today;
create view public.reach_today as
  select path, views from public.page_views
  where day = (now() at time zone 'America/New_York')::date and path not like '/\_from/%';
grant select on public.reach_week, public.reach_today to anon, authenticated;
