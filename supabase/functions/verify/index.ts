// Email check for birthday shout-outs: the sender proves the email is theirs before the shout-out reaches the queue.
// The site saves the shout-out (unverified), then calls { action: "send", id, email } and we email a 6-digit code
// through Resend. The sender types it in and the site calls { action: "check", id, code }; a match marks the shout-out
// verified, and only then does it show in the admin queue. Setup is in BACKEND.md, step 8. It uses the same
// RESEND_API_KEY secret as the "reply" function.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const SITE = Deno.env.get("SITE_URL") ?? "https://sunnymansfield.com";
const FROM = Deno.env.get("VERIFY_FROM") ?? "Sunny Mansfield <hello@sunnymansfield.com>";
const ORIGINS = [SITE, SITE.replace("https://", "https://www.")];
const SECTIONS = ["Birthday"]; // sections that need an email check
const CODE_MINUTES = 15;
const LIMITS = { sendsPerPost: 3, triesPerCode: 5, sendsPerIpHour: 6 };

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

async function hash(text: string) {
  const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(SALT + text));
  return Array.from(new Uint8Array(bytes), (b) => b.toString(16).padStart(2, "0")).join("");
}

function newCode() {
  const n = crypto.getRandomValues(new Uint32Array(1))[0] % 1_000_000;
  return String(n).padStart(6, "0");
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

  const { data: p } = await db.from("submissions").select("id, section, title, email, status, verified_at, created_at")
    .eq("id", id).maybeSingle();
  if (!p || !SECTIONS.includes(p.section) || p.status !== "pending" || !p.email ||
      new Date(p.created_at).getTime() < Date.now() - 86400e3) {
    return reply(404, { error: "We couldn't find that shout-out. Please send it again." });
  }
  if (p.verified_at) return reply(200, { ok: true, verified: true });

  if (body.action === "send") {
    const email = String(body.email ?? "").trim().toLowerCase();
    if (email !== String(p.email).toLowerCase()) return reply(400, { error: "That isn't the email on this shout-out." });
    const ipHash = await hash("ip:" + (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim());
    const { count } = await db.from("email_codes").select("submission_id", { count: "exact", head: true })
      .eq("ip_hash", ipHash).gte("sent_at", new Date(Date.now() - 3600e3).toISOString());
    const { data: old } = await db.from("email_codes").select("sends").eq("submission_id", id).maybeSingle();
    if ((count ?? 0) >= LIMITS.sendsPerIpHour || (old?.sends ?? 0) >= LIMITS.sendsPerPost) {
      return reply(429, { error: "Too many codes. Please try again later." });
    }
    const code = newCode();
    await db.from("email_codes").upsert({
      submission_id: id, code_hash: await hash(id + ":" + code), ip_hash: ipHash, tries: 0,
      sends: (old?.sends ?? 0) + 1, sent_at: new Date().toISOString(),
      expires_at: new Date(Date.now() + CODE_MINUTES * 60e3).toISOString(),
    });
    const title = String(p.title).replace(/[\r\n\t]+/g, " ").slice(0, 120);
    const text = [
      `Your Sunny Mansfield code is ${code}`,
      "",
      `Type it on the birthday page to send "${title}" to our editors. It works for ${CODE_MINUTES} minutes.`,
      "",
      "Didn't ask for this? Ignore this email and nothing will be posted.",
    ].join("\n");
    const sent = await fetch("https://api.resend.com/emails", {
      method: "POST",
      headers: { Authorization: `Bearer ${Deno.env.get("RESEND_API_KEY")}`, "Content-Type": "application/json" },
      body: JSON.stringify({ from: FROM, to: [p.email], subject: `${code} is your Sunny Mansfield code`, text }),
    });
    if (!sent.ok) {
      console.error("Resend failed", sent.status, await sent.text());
      return reply(502, { error: "We couldn't email the code just now. Please try again in a few minutes." });
    }
    return reply(200, { ok: true });
  }

  if (body.action === "check") {
    const code = String(body.code ?? "").replace(/\D/g, "");
    const { data: c } = await db.from("email_codes").select("*").eq("submission_id", id).maybeSingle();
    if (!c || new Date(c.expires_at).getTime() < Date.now()) return reply(400, { error: "That code ran out. Press \"Email me a new code\"." });
    if (c.tries >= LIMITS.triesPerCode) return reply(429, { error: "Too many tries. Press \"Email me a new code\"." });
    if (code.length !== 6 || await hash(id + ":" + code) !== c.code_hash) {
      await db.from("email_codes").update({ tries: c.tries + 1 }).eq("submission_id", id);
      return reply(400, { error: "That code doesn't match. Check the email and try again." });
    }
    await db.from("submissions").update({ verified_at: new Date().toISOString() }).eq("id", id);
    await db.from("email_codes").delete().eq("submission_id", id);
    return reply(200, { ok: true, verified: true });
  }

  return reply(400, { error: "Something was missing. Please try again." });
});
