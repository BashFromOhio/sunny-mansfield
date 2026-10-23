// Private replies for Free Stuff and job posts, Craigslist style: the person replying never sees the poster's email.
// The site sends { post_id, name, email, message }. We look up the poster's email with the service key (which Supabase
// gives this function on its own), email them through Resend with Reply-To set to the sender, and log a hashed record
// to stop spam. Setup is in BACKEND.md, step 7. The only secret to add is RESEND_API_KEY.
import { createClient } from "npm:@supabase/supabase-js@2.45.4";

const SITE = Deno.env.get("SITE_URL") ?? "https://sunnymansfield.com";
const FROM = Deno.env.get("RELAY_FROM") ?? "Sunny Mansfield <replies@sunnymansfield.com>";
const REPORT = Deno.env.get("REPORT_EMAIL") ?? "hello@sunnymansfield.com";
const ORIGINS = [SITE, SITE.replace("https://", "https://www.")];
const SECTIONS = ["Free Stuff", "Job"];
const OPEN_DAYS = 31; // posts come off the site after 30 days
const LIMITS = { perIpHour: 8, perSenderPostDay: 2, perPostDay: 40 };

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

const oneLine = (s: string) => s.replace(/[\r\n\t]+/g, " ").trim();
const since = (ms: number) => new Date(Date.now() - ms).toISOString();

async function count(column: string, value: string, after: string, post?: string) {
  let q = db.from("relay_log").select("id", { count: "exact", head: true }).eq(column, value).gte("created_at", after);
  if (post) q = q.eq("post_id", post);
  const { count: n } = await q;
  return n ?? 0;
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
  const post = String(body.post_id ?? "");
  const name = oneLine(String(body.name ?? "")).slice(0, 100);
  const email = oneLine(String(body.email ?? "")).toLowerCase();
  const message = String(body.message ?? "").trim();
  if (!/^[0-9a-f-]{36}$/i.test(post)) return reply(400, { error: "That post can't take replies." });
  if (!name) return reply(400, { error: "Add your name." });
  if (email.length > 200 || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) return reply(400, { error: "Check your email address." });
  if (message.length < 10 || message.length > 3000) return reply(400, { error: "Write a message between 10 and 3,000 characters." });

  const ipHash = await hash("ip:" + (req.headers.get("x-forwarded-for") ?? "").split(",")[0].trim());
  const senderHash = await hash("from:" + email);
  if (await count("ip_hash", ipHash, since(3600e3)) >= LIMITS.perIpHour ||
      await count("sender_hash", senderHash, since(86400e3), post) >= LIMITS.perSenderPostDay ||
      await count("post_id", post, since(86400e3)) >= LIMITS.perPostDay) {
    return reply(429, { error: "Too many messages. Please try again later." });
  }

  const { data: p } = await db.from("submissions").select("id, section, title, email, status, created_at").eq("id", post).maybeSingle();
  if (!p || p.status !== "approved" || !SECTIONS.includes(p.section) || !p.email ||
      new Date(p.created_at).getTime() < Date.now() - OPEN_DAYS * 86400e3) {
    return reply(404, { error: "That post is no longer taking replies." });
  }

  const title = oneLine(p.title).slice(0, 120);
  const what = p.section === "Job" ? "your job post" : "your Free Stuff post";
  const text = [
    `${name} replied to ${what} on Sunny Mansfield: "${title}"`,
    "",
    message,
    "",
    "---",
    `To answer, just reply to this email. It goes to ${name} at the address they gave, and they'll see your email address when you do.`,
    "Not interested? Ignore it. We never showed your email to them.",
    "Stay safe: meet in a public place, and never send money, gift cards or bank details to someone you haven't met.",
    `Spam or something wrong? Forward this to ${REPORT}.`,
  ].join("\n");

  const sent = await fetch("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${Deno.env.get("RESEND_API_KEY")}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: FROM,
      to: [p.email],
      reply_to: email,
      subject: `Reply about "${title}" from Sunny Mansfield`,
      text,
    }),
  });
  if (!sent.ok) {
    console.error("Resend failed", sent.status, await sent.text());
    return reply(502, { error: "We couldn't send it just now. Please try again in a few minutes." });
  }
  await db.from("relay_log").insert({ post_id: post, ip_hash: ipHash, sender_hash: senderHash });
  return reply(200, { ok: true });
});
