// Rebuild the site right away: the admin page calls this after an editor approves, takes down or deletes a post,
// so the change shows in about 2 minutes instead of waiting for the next 15-minute refresh.
// Only signed-in admins can use it. It starts the "Refresh good news" GitHub workflow.
// Setup is in BACKEND.md, step 9. The only secret to add is GH_ACTIONS_TOKEN.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const SITE = Deno.env.get("SITE_URL") ?? "https://sunnymansfield.com";
const REPO = Deno.env.get("GH_REPO") ?? "BashFromOhio/sunny-mansfield";
const WORKFLOW = Deno.env.get("GH_WORKFLOW") ?? "update.yml";
const ORIGINS = [SITE, SITE.replace("https://", "https://www.")];

function headers(req: Request) {
  const origin = req.headers.get("origin") ?? "";
  return {
    "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : SITE,
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Content-Type": "application/json",
  };
}

Deno.serve(async (req) => {
  const h = headers(req);
  const reply = (status: number, body: object) => new Response(JSON.stringify(body), { status, headers: h });
  if (req.method === "OPTIONS") return new Response("ok", { headers: h });
  if (req.method !== "POST") return reply(405, { error: "Use POST." });

  // Check the caller is a signed-in admin, using their own sign-in (not the service key).
  const user = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_ANON_KEY")!, {
    global: { headers: { Authorization: req.headers.get("Authorization") ?? "" } },
  });
  const { data: admin, error } = await user.rpc("is_admin");
  if (error || admin !== true) return reply(403, { error: "Editors only." });

  const token = Deno.env.get("GH_ACTIONS_TOKEN");
  if (!token) return reply(500, { error: "The rebuild key isn't set up yet (BACKEND.md step 9)." });
  const res = await fetch(`https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "sunny-mansfield-rebuild",
      "X-GitHub-Api-Version": "2022-11-28",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (!res.ok) {
    console.error("GitHub dispatch failed", res.status, await res.text());
    return reply(502, { error: "Couldn't start the rebuild. The site will still update within 15 minutes." });
  }
  return reply(200, { ok: true });
});
