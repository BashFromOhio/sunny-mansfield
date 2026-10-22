// Render every page listed in <out>/assets.json to <out>/kit/<folder>/<name>.png
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");

(async () => {
  const out = process.argv[2];
  const assets = JSON.parse(fs.readFileSync(path.join(out, "assets.json"), "utf8"));
  const browser = await chromium.launch();
  for (const a of assets) {
    const page = await browser.newPage({ viewport: { width: a.w, height: a.h } });
    await page.goto("file://" + a.html);
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(250);
    const file = path.join(out, "kit", a.folder, a.name + ".png");
    fs.mkdirSync(path.dirname(file), { recursive: true });
    await page.screenshot({ path: file, omitBackground: a.transparent });
    a.png = file;
    await page.close();
  }
  fs.writeFileSync(path.join(out, "assets.json"), JSON.stringify(assets, null, 1));
  await browser.close();
  console.log("rendered", assets.length);
})();
