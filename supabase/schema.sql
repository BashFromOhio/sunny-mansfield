-- Sunny Mansfield backend. Paste this whole file into Supabase > SQL Editor and click Run.
-- It is safe to run again after changes.

-- Everything people send: stories, flyers, Buy & Sell, spotlights, team applications, ad inquiries.
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
create index if not exists submissions_status_idx on public.submissions (status, created_at desc);

-- People who can use the admin page, and people whose posts skip the review queue.
create table if not exists public.admins (email text primary key);
create table if not exists public.verified (
  email text primary key,
  name text,
  role text,
  added_at timestamptz not null default now()
);

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from admins where lower(email) = lower(auth.jwt() ->> 'email'));
$$;

-- New posts always start in review, unless the sender is signed in and on the verified list.
-- Team applications and ad inquiries always wait for an admin.
create or replace function public.on_submit() returns trigger
language plpgsql security definer set search_path = public as $$
declare
  signed_in text := lower(auth.jwt() ->> 'email');
begin
  new.status := 'pending';
  new.reviewed_at := null;
  new.reviewed_by := null;
  new.created_at := now();
  if signed_in is not null then
    new.email := signed_in;  -- a signed-in sender can't claim someone else's address
    if new.section not in ('Join the team', 'Advertise')
       and exists (select 1 from verified where lower(email) = signed_in) then
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

-- What the public site reads: approved posts only, without anyone's email address.
create or replace view public.published as
  select id, created_at, section, title, summary, photo_url, link, name, role, business, town, event_date, price, contact
  from public.submissions
  where status = 'approved' and section not in ('Join the team', 'Advertise');
grant select on public.published to anon, authenticated;

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
