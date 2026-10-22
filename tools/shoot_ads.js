// Screenshot every ad spot on a local build, outlined and labeled, for the media kit.
//   node tools/shoot_ads.js <base url, like http://localhost:8765/sunny-mansfield> <out dir> <story page path>
const { chromium } = require("playwright");
const fs = require("fs");
const [base, out, story] = process.argv.slice(2);
const SHOTS = [
  ["A1", "advertise.html", ".adrow", 0, "header"],
  ["A2", "index.html", "#shelves .ad.house", 0, ".railbox"],
  ["A3", "index.html", "#shelves .sponsortag.house", 0, ".railbox"],
  ["A4", story, ".adrow", 1, null],
  ["A5", "sports.html", ".adrow", 1, null],
  ["A6", "index.html", ".evad", 0, ".railbox"],
  ["A7", "market.html", ".ad.house", 0, null],
  ["A8", "celebrations.html", ".adrow", 1, null],
];
(async () => {
  fs.mkdirSync(out, { recursive: true });
  const b = await chromium.launch();
  for (const [spot, page, sel, nth, around] of SHOTS) {
    const p = await b.newPage({ viewport: { width: 1200, height: 900 }, deviceScaleFactor: 1.5 });
    await p.route(/jsdelivr|supabase/, r => r.abort());
    await p.addInitScript(() => localStorage.setItem("aboutSeen", "1"));
    await p.goto(`${base}/${page}`);
    await p.evaluate(() => document.fonts.ready);
    const box = await p.evaluate(([sel, nth, around, spot]) => {
      const el = document.querySelectorAll(sel)[nth];
      const target = el.matches(".adrow") ? el.firstElementChild.nextElementSibling || el.firstElementChild : el;
      target.style.outline = "5px solid #D9531E"; target.style.outlineOffset = "4px";
      const tag = document.createElement("span");
      tag.textContent = "Spot " + spot;
      tag.style.cssText = "position:absolute;z-index:50;background:#D9531E;color:#FBF6EC;font:700 15px/1 'Josefin Sans',sans-serif;letter-spacing:.12em;text-transform:uppercase;padding:8px 10px";
      const r = target.getBoundingClientRect();
      tag.style.left = (r.left + scrollX - 4) + "px"; tag.style.top = (r.top + scrollY - 40) + "px";
      document.body.appendChild(tag);
      const ctx = around ? el.closest(around) || el : el;
      const c = ctx.getBoundingClientRect(), t = target.getBoundingClientRect();
      const top = Math.min(c.top, t.top - 50) + scrollY, bottom = Math.max(c.bottom, t.bottom + 20) + scrollY;
      return { x: 0, y: Math.max(0, top - 20), width: 1200, height: Math.min(900, bottom - top + 40) };
    }, [sel, nth, around, spot]);
    await p.waitForTimeout(300);
    await p.screenshot({ path: `${out}/${spot}.png`, clip: box, fullPage: true });
    await p.close();
  }
  await b.close();
  console.log("shot", SHOTS.length);
})();
