# Setting up the review queue (about 10 minutes, free)

Submissions are stored in Supabase, a free hosted database. Every post starts in **review mode**. Editors approve it on the site's admin page (`/admin.html`), and the site publishes approved posts within 20 minutes.

## 1. Create the database

1. Go to [supabase.com](https://supabase.com) and sign up. The free plan is enough.
2. Click **New project**. Name it `sunny-mansfield`, pick a strong database password (keep it somewhere safe), and choose the **East US** region.
3. Wait about a minute for the project to finish setting up.

## 2. Add the tables

1. In the left menu, open **SQL Editor** and click **New query**.
2. Open [`supabase/schema.sql`](supabase/schema.sql) in this repository and copy all of it into the query box.
3. Find the last line, `-- insert into public.admins ...`. Remove the `-- ` at its start and put your own email in place of `you@example.com`.
4. Click **Run**. You should see "Success. No rows returned."

To add another admin later, run `insert into public.admins (email) values ('their@email.com');` the same way.

## 3. Allow sign-in links back to the site

1. Open **Authentication → URL Configuration**.
2. Set **Site URL** to `https://bashfromohio.github.io/sunny-mansfield/`. Once you buy the domain, change it to `https://sunnymansfield.com/`.
3. Under **Redirect URLs**, add `https://bashfromohio.github.io/sunny-mansfield/**`. Once you have the domain, also add `https://sunnymansfield.com/**`.

## 4. Send two values

Open **Project Settings → API** (in newer dashboards it's under **Data API** and **API Keys**). Copy these two values:

- **Project URL**, which looks like `https://abcdefgh.supabase.co`
- **anon public** key, a long string starting with `eyJ`

The anon key is meant to be public. Security comes from the rules in `schema.sql`: anyone can send a post, only admins can read or approve them, and email addresses are never published. **Never share the `service_role` key.**

Both values go in `config.json` as `supabase_url` and `supabase_anon_key`. The forms switch on with the next build.

## 5. Give editors a password

Supabase's free email only sends about 2 sign-in emails an hour, so editors and verified contributors sign in with a password instead.

1. In Supabase, open **Authentication → Users** and click **Add user → Create new user**.
2. Type their email and a password, leave **Auto Confirm User** checked, and click **Create user**.
3. For an editor, also add their email to the admins list (step 2). For a verified contributor, add them on the admin page under **Verified people**.
4. Give them the password. After signing in, they can change it on the admin page with **Set password**.

## 6. Lock down sign-ups

Editors and verified contributors only ever get accounts that you create by hand in step 5, so nobody else needs to sign up.

1. In Supabase, open **Authentication → Sign In / Providers**.
2. Turn **off** "Allow new users to sign up".
3. Make sure **Confirm email** is **on**.
4. Under **Authentication → URL Configuration**, list only your site (like `https://sunnymansfield.com/**`) in Redirect URLs.

Editor and verified powers only work for confirmed email addresses (`confirmed_email()` in `schema.sql`), so this is a second lock on the same door.

## 7. Turn on private replies (Free Stuff and jobs)

People answer Free Stuff and job posts through a form, and the poster gets the message by email without anyone seeing their address. The form calls a small Supabase function that sends the email through Resend. Until you finish these steps, the Reply buttons show an error and nothing is sent.

1. Run the updated `supabase/schema.sql` again (SQL Editor > paste > Run). It adds the reply log and the nightly clean-up.
2. Optional: turn on nightly clean-up. Database > Extensions > search **pg_cron** > enable, then run `schema.sql` once more.
3. In Supabase, go to **Edge Functions > Deploy a new function > Via Editor**. Name it exactly `reply`, paste all of `supabase/functions/reply/index.ts`, and deploy. Leave **Verify JWT** on.
4. Go to **Edge Functions > Secrets** and add `RESEND_API_KEY`. Paste your Resend API key there, and only there.
5. Test it: open a Free Stuff post on the site, press **Message the giver**, and send yourself a note.

Replies come from `hello@sunnymansfield.com` (sent through Resend), with Reply-To set to the person writing. Each person can send 8 replies an hour, 2 a day to the same post, and a post takes at most 40 a day.

## 8. Turn on birthday shout-outs (about 5 minutes)

People confirm their email with a 6-digit code before a birthday shout-out reaches the queue. The code is sent by a small
Supabase function through Resend, like private replies (step 7), so finish step 7 first.

1. Run the updated `supabase/schema.sql` once more (SQL Editor > paste > Run). It adds the email-code table, makes birthday
   shout-outs always wait for an editor (even from verified people), and lists each one publicly only on the birthday itself.
2. Go to **Edge Functions > Deploy a new function > Via Editor**. Name it exactly `verify`, paste all of
   `supabase/functions/verify/index.ts`, and deploy. Leave **Verify JWT** on. It uses the `RESEND_API_KEY` secret you already added.
3. Test it: send a shout-out on `birthdays.html` with your own email, type the code, and look for it under **Waiting**.

Codes come from `hello@sunnymansfield.com` and work for 15 minutes. Each shout-out can get 3 codes and 5 tries per code.
Shout-outs nobody confirms never reach the queue and are deleted after 2 days.

## 9. Make admin changes show right away (optional, about 5 minutes)

Without this, the site picks up approvals, take-downs and deletes on its regular refresh, every 15 minutes
(GitHub sometimes runs it a little late). With it, the admin page asks for a rebuild and changes show in about 2 minutes.

1. On GitHub, open **Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
   Name it `sunny-rebuild`, pick an expiration, and under **Repository access** choose **Only select repositories →
   sunny-mansfield**. Under **Permissions → Repository permissions**, set **Actions** to **Read and write**. Click
   **Generate token** and copy it.
2. In Supabase, go to **Edge Functions → Secrets** and add `GH_ACTIONS_TOKEN` with that token.
3. Go to **Edge Functions → Deploy a new function → Via Editor**. Name it exactly `rebuild`, paste all of
   `supabase/functions/rebuild/index.ts`, and deploy. Leave **Verify JWT** on.
4. Test it: approve or take down a post on the admin page. A few seconds later it says "Updating the site now".

Only signed-in admins can start a rebuild.

## How it works day to day

- **Waiting**: new posts. Choose **Approve** or **Reject**. **Approve and verify sender** also adds the sender to the verified list.
- **Team applications**: choose **Approve and verify** to make someone a verified contributor.
- **Ad inquiries**: messages from businesses about advertising. Reply to them by email.
- **Verified people**: add or remove people. A verified person who signs in on the submit page (with the "Email me a sign-in link" button) goes live without review, except for fundraisers, which always wait for an editor.
- **Birthday shout-outs** show up in **Waiting** once the sender confirms their email, with the birthday shown as a date. Approve one and it shows on the front page and `birthdays.html` for two days starting on the person's next birthday, and posts to Facebook at 9 a.m. on the birthday. Each one runs once.
- **Approved**: everything on the site. Choose **Take down** to remove a post.

Event flyers stay up until the day after the event. Free Stuff and job posts stay up for 30 days. Jobs always wait for an editor, even from verified people. Free Stuff only shows posts with no price: anything mentioning a dollar amount, a trade or a payment app is left off.

Ads are paused (`"ads_open": false` in `config.json`), so each ad spot shows a Sunny Mansfield promo at the same size. To show a paid ad, add it to `config.json`. It takes its spot even while ads are paused:

```json
"ads": [{"name": "Sample Bank", "text": "Proud to keep Mansfield sunny", "image": "https://.../ad.jpg", "link": "https://..."}]
```

Note: Supabase's built-in email only sends a few sign-in emails per hour. After you sign in, you stay signed in on that device. The site checks the database every 15 minutes, which also keeps the free project from pausing.

## Homes for Rent or Sale (added November 2026)

Home listings use the same private replies as Free Stuff and jobs. After this change is live, do these two things once:

1. In Supabase, open **SQL Editor**, paste all of `supabase/schema.sql` again and click **Run**. Editors then review every home listing, even from verified contributors, and the poster's email is erased after 60 days.
2. Go to **Edge Functions**, open `reply`, paste all of the new `supabase/functions/reply/index.ts` over the old code and deploy. Until then, the "Ask about it" button on home listings shows an error.
