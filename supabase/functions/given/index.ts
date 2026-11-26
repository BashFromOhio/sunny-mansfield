// "Given away" for Free Stuff posts. Anyone can press "Mark as given away" on a listing page, but only the poster can
// finish it: { action: "send", id } emails the poster a one-tap link, and { action: "mark", id, t } (from given.html)
// checks that link's token and greys the post out. The token is a salted hash of the post id and the poster's email,
// so it can't be guessed and nothing extra is stored. Uses the same RESEND_API_KEY secret as "reply" and "verify".
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const SITE = Deno.env.get("SITE_URL") ?? "https://sunnymansfield.com";
const FROM = Deno.env.get("VERIFY_FROM") ?? "Sunny Mansfield <hello@sunnymansfield.com>";
const ORIGINS = [SITE, SITE.replace("https://", "https://www.")];
const RESEND_MINUTES = 30; // one email per post per half hour, so the button can't be used to flood an inbox

const db = createClient(Deno.env.get("SUPABASE_URL")!, Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!);
const SALT = Deno.env.get("RELAY_SALT") ?? Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!.slice(-24);

function headers(req: Request) {
  const origin = req.headers.get("origin") ?? "";
  return {
    "Access-Control-Allow-Origin": ORIGINS.includes(origin) ? origin : SITE,
    "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Content-Type": "application/json",
  };
}

async function token(id: string, email: string) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(SALT + "given:" + id + ":" + email.toLowerCase()));
  return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("").slice(0, 32);
}

Deno.serve(async (req) => {
  const h = headers(req);
  const reply = (status: number, body: object) => new Response(JSON.stringify(body), { status, headers: h });
  if (req.method === "OPTIONS") return new Response("ok", { headers: h });
  if (req.method !== "POST") return reply(405, { error: "Use POST." });
  let body: Record<string, unknown>;
  try {
    body = await req.json();
  } catch {
    return reply(400, { error: "Something was missing. Please try again." });
  }
  const id = String(body.id ?? "");
  if (!/^[0-9a-f-]{36}$/i.test(id)) return reply(400, { error: "Something was missing. Please try again." });
  const { data: p } = await db.from("submissions").select("id, section, title, email, status, given_at, given_link_at")
    .eq("id", id).maybeSingle();
  if (!p || p.section !== "Free Stuff" || p.status !== "approved") return reply(404, { error: "We couldn't find that post." });
  if (p.given_at) return reply(200, { ok: true, given: true });
  if (!p.email) return reply(400, { error: "This post has no email on file. Ask an editor to mark it given away." });

  if (body.action === "send") {
    if (p.given_link_at && new Date(p.given_link_at).getTime() > Date.now() - RESEND_MINUTES * 60e3) {
      return reply(200, { ok: true, sent: true }); // already sent recently; don't send another
    }
    const link = `${SITE}/given.html?id=${id}&t=${await token(id, p.email)}`;
    const title = String(p.title).replace(/[\r\n\t]+/g, " ").slice(0, 120);
    const text = [
      `Did "${title}" find a new home?`,
      "",
      "Tap this link to mark it given away on Sunny Mansfield. It stays on the site, greyed out, for a few days and then comes down:",
      link,
      "",
      "Still have it? Ignore this email and your post stays up as it is.",
    ].join("\n");
    const sent = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${Deno.env.get("RESEND_API_KEY")}`, "Content-Type": "application/json" },
      body: JSON.stringify({ from: FROM, to: [p.email], subject: `Mark "${title.slice(0, 60)}" as given away`, text }),
    });
    if (!sent.ok) {
      console.error("Resend failed", sent.status, await sent.text());
      return reply(502, { error: "We couldn't send the email just now. Please try again in a few minutes." });
    }
    await db.from("submissions").update({ given_link_at: new Date().toISOString() }).eq("id", id);
    return reply(200, { ok: true, sent: true });
  }

  if (body.action === "mark") {
    if (String(body.t ?? "") !== await token(id, p.email)) return reply(403, { error: "That link doesn't match this post." });
    await db.from("submissions").update({ given_at: new Date().toISOString() }).eq("id", id);
    return reply(200, { ok: true, given: true, title: p.title });
  }
  return reply(400, { error: "Something was missing. Please try again." });
});
