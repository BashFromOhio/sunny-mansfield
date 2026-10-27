#!/usr/bin/env python3
"""Write the Sunny Mansfield advertiser media kit as HTML, ready to print to PDF.

  python3 tools/media_kit.py <ad screenshots dir> <out html>
Screenshots come from tools/shoot_ads.js. Spots, packages and rules come from build.py, so the kit
always matches the Advertise page.
"""
import html
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from build import AD_DEALS, AD_RULES, AD_SPECIALS, AD_SPOTS, AD_TIERS  # noqa: E402

SHOTS = Path(sys.argv[1]).resolve()
OUT = Path(sys.argv[2]).resolve()
e = html.escape
year = date.today().year

css = (HERE / "tools" / "social_guide.css").read_text() + """
.tiers{display:grid;grid-template-columns:repeat(4,1fr);gap:10pt;margin:8pt 0 14pt}
.tier{border:2px solid #2E1A10;padding:10pt 9pt;position:relative;break-inside:avoid}
.tier.pick{border:3px solid #D9531E;background:#FBF6EC}
.tier .picked{position:absolute;top:-9pt;left:8pt;background:#D9531E;color:#FBF6EC;font:700 7pt/1 "Josefin Sans";letter-spacing:.1em;text-transform:uppercase;padding:3pt 5pt}
.tier h3{margin:0 0 4pt;font-size:13pt}
.tier .cost{font:700 24pt/1 "Libre Caslon Text",serif;color:#D9531E;margin:0}
.tier .per{font-size:8.5pt;color:#6F5443}
.tier .best{font-size:8.5pt;color:#6F5443;margin:6pt 0}
.tier ul{padding-left:12pt;margin:0;font-size:8.4pt;line-height:1.38}
.tier li{margin-bottom:3pt}
.shot{border:1px solid #D8C2A0;width:100%;display:block}
.spotrow{display:grid;grid-template-columns:2.6in 1fr;gap:14pt;align-items:start;margin-bottom:14pt;break-inside:avoid}
.spotrow h3{margin-top:0}
.code{font:700 9pt/1 "Josefin Sans";letter-spacing:.12em;color:#D9531E;text-transform:uppercase}
.price{display:inline-block;background:#D9531E;color:#FBF6EC;font:700 9pt/1 "Libre Franklin";padding:4pt 6pt;margin-top:4pt}
.big{font-size:11.5pt;line-height:1.55}
table td{padding:4pt 6pt}
.pill{display:inline-block;border:1px solid #D9531E;color:#D9531E;font:700 8pt/1 "Josefin Sans";letter-spacing:.1em;text-transform:uppercase;padding:4pt 6pt;margin:0 4pt 4pt 0}
"""

parts = [f"""<section class="cover"><div class="stripes"></div><div class="covin">
<img src="file://{HERE}/brand/post-logo.png" style="width:4.6in">
<h1>Media kit</h1>
<p class="lead">Advertise with the paper Richland County actually shares. Packages, prices, ad spots and requirements.</p>
<p class="small">Founding sponsor rates · {year}</p>
<p class="small">Sunny Mansfield is a publication of Ofgeneration LLC. &copy; {year} Ofgeneration LLC. All rights reserved.</p></div>
<div class="stripes bottom"></div></section>"""]

parts.append("""<section><h2>Why Sunny Mansfield</h2>
<p class="big">Sunny Mansfield is a free online paper with only good news from Mansfield and Richland County, Ohio: neighbors helping
neighbors, new shops, student and athlete wins, community events, free stuff and local jobs.</p>
<h3>Why it works for local businesses</h3>
<ul class="big">
<li><b>Safe next to your brand.</b> No crime, crashes or arguing. Your ad never sits beside bad news.</li>
<li><b>Made to be shared.</b> Every story has a Facebook share button and its own framed post image. People share good news about
their kids, their church and their street, and your ad travels with it.</li>
<li><b>Local only.</b> Every reader is here for Richland County. No wasted national clicks.</li>
<li><b>Updated all day.</b> New stories arrive every 30 minutes, so people come back.</li>
<li><b>Honest labels.</b> Every ad says Advertisement or Sponsor. Readers trust that, and sponsors never decide what we cover.</li>
</ul>
<h3>Who reads</h3>
<p class="big">Families, grandparents, students, parents of athletes, church members, small business owners and anyone who loves the
county. We share current reader and follower numbers with every sponsor before they buy.</p>
<p><span class="pill">Mansfield</span><span class="pill">Ontario</span><span class="pill">Lexington</span><span class="pill">Madison</span>
<span class="pill">Shelby</span><span class="pill">Bellville</span><span class="pill">Butler</span><span class="pill">Lucas</span>
<span class="pill">Plymouth</span><span class="pill">Crestline</span><span class="pill">Shiloh</span></p></section>""")

tiers = "".join(f"""<div class="tier{' pick' if t.get('pick') else ''}">{'<span class="picked">Most popular</span>' if t.get('pick') else ''}
<h3>{e(t['name'])}</h3><p class="cost">{t['price']}</p><span class="per">{t['per']}</span><p class="best">{e(t['best'])}</p>
<ul>{''.join(f'<li>{e(g)}</li>' for g in t['gets'])}</ul></div>""" for t in AD_TIERS)
specials = "".join(f"<tr><td><b>{e(n)}</b></td><td>{e(d)}</td><td style='white-space:nowrap'><b>{e(p)}</b></td></tr>"
                   for n, p, d in AD_SPECIALS)
deals = "".join(f"<li>{e(d)}</li>" for d in AD_DEALS)
parts.append(f"""<section><h2>Packages and prices</h2>
<p>Four tiers. Every package runs month to month, and you can move up or down any month.</p>
<div class="tiers">{tiers}</div>
<h3>Specialty spots</h3>
<table><tr><th>Spot</th><th>What you get</th><th>Price</th></tr>{specials}</table>
<h3>Ways to save</h3><ul style="margin:0;font-size:9.5pt">{deals}</ul></section>""")

alacarte = "".join(f"<tr><td class='code'>{x['spot']}</td><td><b>{e(x['name'])}</b><br>{e(x['where'])}</td>"
                   f"<td>{e(x['size'])}</td><td style='white-space:nowrap'>{e(x['price'])}</td></tr>" for x in AD_SPOTS)
parts.append(f"""<section><h2>Every ad spot</h2>
<p>Buy any spot on its own at these founding rates, or get it inside a package above.</p>
<table><tr><th>Spot</th><th>Where it shows</th><th>Your file</th><th>Price</th></tr>{alacarte}</table></section>""")

rows = []
for x in AD_SPOTS:
    shot = SHOTS / f"{x['spot']}.png"
    pic = (f'<img class="shot" src="file://{shot}">' if shot.exists()
           else f'<img class="shot" src="file://{HERE}/brand/og.png">')
    rows.append(f"""<div class="spotrow">{pic}<div><span class="code">Spot {x['spot']}</span><h3>{e(x['name'])}</h3>
<p>{e(x['where'])}.</p><p style="font-size:9pt;color:#6F5443">{e(x['size'])}</p>
{f"<p style='font-size:9pt'>Made for: {e(x['for'])}</p>" if x.get('for') else ''}<span class="price">{e(x['price'])}</span></div></div>""")
parts.append(f"""<section><h2>Where your ad shows</h2><p>Real screenshots of the site. The orange box is your spot.</p>
{''.join(rows)}</section>""")

rules = "".join(f"<li><b>{t}</b> {d}</li>" for t, d in AD_RULES)
parts.append(f"""<section><h2>Ad requirements</h2><ol style="font-size:9.8pt;line-height:1.45">{rules}</ol>
<h3>File checklist</h3>
<table class="kv">
<tr><td>Banners (A1, A4, A5, A8)</td><td>1456 × 180 px for computers and 640 × 200 px for phones. JPG or PNG, under 1 MB each.</td></tr>
<tr><td>Cards (A2, A7)</td><td>1200 × 800 px photo, your business name, and one line of up to 80 characters.</td></tr>
<tr><td>Section logo (A3)</td><td>600 × 200 px PNG with a see-through background.</td></tr>
<tr><td>Events card (A6)</td><td>1080 × 1350 px, like a flyer.</td></tr>
<tr><td>Social post (S1)</td><td>1080 × 1350 px image and up to 300 characters of text, or send photos and we design it.</td></tr>
<tr><td>Every ad</td><td>The link it should open, the dates it should run, and a one-sentence description of the image.</td></tr>
</table>
<p>No designer? We'll make a simple ad in Sunny Mansfield style from your logo and a photo, free with any package.</p></section>""")

parts.append(f"""<section><h2>How to get started</h2>
<ol class="big">
<li>Pick a package or spot. Not sure? Ask, and we'll suggest one that fits your budget.</li>
<li>Fill out the short form on the Advertise page of Sunny Mansfield, or reply to whoever sent you this kit.</li>
<li>We confirm your dates and send an invoice. Ads are prepaid by the month.</li>
<li>Send your files at least three business days before your start date.</li>
<li>Your ad goes live, and we send you a screenshot of it on the site.</li>
</ol>
<h3>Our promise</h3>
<p class="big">Sunny Mansfield stays positive and independent. Every ad is labeled, we never write a story because someone paid,
and we take down any ad that breaks the rules above and refund the unused days.</p>
<p style="margin-top:28pt;color:#6F5443;font-size:9pt">Sunny Mansfield is a publication of Ofgeneration LLC. &copy; {year} Ofgeneration LLC.
All rights reserved. Prices are founding sponsor rates and may change for new sponsors. Stories from other outlets belong to their publishers.</p></section>""")

OUT.write_text(f"""<!doctype html><html><head><meta charset="utf-8"><title>Sunny Mansfield media kit</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Libre+Caslon+Text:wght@700&family=Libre+Franklin:wght@400;600;700&family=Josefin+Sans:wght@700&display=swap">
<style>{css}</style></head><body>{''.join(parts)}</body></html>""")
print("media kit written")
