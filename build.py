#!/usr/bin/env python3
"""Build the positive-news site.

Reads the RSS feeds in config.json, keeps only uplifting local stories,
sorts them into sections, and writes a static site to --out:
  index.html     the front page (all sections, tab filter)
  s/<id>.html    one share page per story (Open Graph tags for Facebook)
  feed.xml       RSS feed people can subscribe to

We only republish the headline, a short summary, and a link back to the
original article. Full articles are never copied.

  python3 build.py                  # live feeds -> ./site
  python3 build.py --sample         # sample_stories.json -> ./site (preview)
"""
import argparse
import email.utils
import hashlib
import html
import json
import base64
import os
import re
import shutil
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

SECTIONS = [
    ("good", "Good News"),
    ("hoods", "Neighborhoods"),
    ("biz", "Local Business"),
    ("sales", "Garage Sales"),
    ("seniors", "Seniors"),
    ("life", "Celebrations of Life"),
    ("vote", "Voting"),
    ("research", "Cool Research"),
]
SECTION_NAMES = dict(SECTIONS)

# A story with any of these words is dropped, however upbeat the rest is.
NEGATIVE = r"""shoot|shot|killed|kill|murder|homicide|stabb|dead\b|death|died|dies|fatal|crash|
wreck|arrest|charged|indict|sentenc|convict|prison|jail|inmate|police|sheriff|deputies|
assault|rape|abuse|theft|stolen|robber|burglar|overdose|drug|fentanyl|meth|fire\b|blaze|
lawsuit|sued|lawsuit|layoff|laid off|closing|closes|shut down|bankrupt|missing|injur|
victim|weapon|gun|trial|guilty|scam|fraud|evict|warning|threat|lockdown|outage|crime|
investigat|suspect|accident|hospitalized|emergency"""

# A story needs at least one of these to count as good news.
POSITIVE = r"""celebrat|award|honor|win\b|wins|won\b|champion|record|first place|scholarship|
graduat|donat|volunteer|grant|raise[sd]? \$|fundrais|opens|opening|grand opening|ribbon|
expan|new\b|launch|milestone|anniversary|birthday|festival|parade|concert|fair\b|
celebration|thank|hero|kindness|helps|helping|rescue|adopt|garden|plant|park|mural|
restor|renovat|reopen|welcome|proud|success|achiev|inspir|hiring|jobs|community|free\b|
students|kids|library|cheer|smile|gift|joy|family|reunion|bloom|harvest|sanctuary"""

# Section rules, checked in order. First match wins; "good" is the fallback.
SECTION_RULES = [
    ("sales", r"garage sale|yard sale|rummage|flea market|estate sale|porch sale|swap meet"),
    ("vote", r"\bvot(e|er|ers|ing)\b|ballot|election|polls?\b|candidate|levy|board of elections"),
    ("research", r"research|study|studies|scientist|discover|university|osu mansfield|ohio state|"
                 r"north central state|nc state|professor|nasa|experiment|data show"),
    ("seniors", r"senior|retire|centenarian|100th birthday|\b(9\d|1\d\d)(th)? birthday|older adults|"
                r"aging|grandparent|veteran|nursing home|assisted living"),
    ("biz", r"business|grand opening|ribbon cutting|shop\b|store|restaurant|cafe|bakery|brewery|"
            r"owner|entrepreneur|downtown mansfield|chamber|hiring|expan|small business|boutique"),
    ("hoods", r"neighborhood|block party|residents|ocie hill|north end|woodland|springmill|"
              r"madison|lexington|ontario|bellville|shelby|butler|lucas|plymouth|shiloh|"
              r"crestline|mifflin|clear fork|lexington|street"),
]
LIFE_HINTS = r"obituar|celebration of life|remember|in memory|life of|passed away|memorial"


def rx(pattern):
    return re.compile(pattern.replace("\n", ""), re.I)


NEG, POS, LIFE = rx(NEGATIVE), rx(POSITIVE), rx(LIFE_HINTS)
RULES = [(key, rx(p)) for key, p in SECTION_RULES]


def classify(story, forced=None):
    """Return a section key, or None if the story should be skipped."""
    text = f"{story['title']} {story['summary']}"
    if forced == "life" or (forced is None and LIFE.search(story["title"])):
        return "life"
    if NEG.search(text):
        return None
    for key, pattern in RULES:
        if pattern.search(text):
            # Voting and garage sales are useful even when they aren't "uplifting".
            if key in ("vote", "sales") or POS.search(text):
                return key
    return "good" if POS.search(text) else None


def clean(text, limit=220):
    text = re.sub(r"<[^>]+>", " ", html.unescape(text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return text


def parse_date(value):
    try:
        dt = email.utils.parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return datetime.now(timezone.utc)


def fetch_feed(feed):
    req = urllib.request.Request(feed["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        root = ET.fromstring(resp.read())
    items = []
    for item in root.iter():
        if item.tag.split("}")[-1] not in ("item", "entry"):
            continue
        def get(*names):
            # Elements without children are falsy, so compare to None explicitly.
            for name in names:
                found = next((c for c in item if c.tag.split("}")[-1] == name), None)
                if found is not None:
                    return found
            return None
        title, link = get("title"), get("link")
        href = (link.text or link.get("href")) if link is not None else None
        desc = get("description", "summary", "encoded")
        date = get("pubDate", "published", "updated")
        source = get("source")
        media = get("content", "thumbnail", "enclosure")
        image = media.get("url") if media is not None else None
        if not image and desc is not None and desc.text:
            found = re.search(r'<img[^>]+src="([^"]+)"', desc.text)
            image = found.group(1) if found else None
        if title is None or not href:
            continue
        name = feed["name"]
        title_text = title.text or ""
        # Google News titles end with " - Publisher"; use that as the source.
        if source is not None and source.text:
            name = source.text
            title_text = re.sub(r"\s+-\s+" + re.escape(source.text) + r"$", "", title_text)
        items.append({
            "title": clean(title_text, 200),
            "summary": clean(desc.text if desc is not None else ""),
            "link": href.strip(),
            "source": name,
            "image": image if feed.get("photos", True) and image and image.startswith("https://") else None,
            "photos": feed.get("photos", True),
            "date": parse_date(date.text if date is not None else None),
            "forced": feed.get("section"),
        })
    return items


def load_sample():
    now = datetime.now(timezone.utc)
    rows = json.loads((HERE / "sample_stories.json").read_text())
    return [{**r, "date": now - timedelta(hours=r["hours_ago"]), "forced": r.get("section")} for r in rows]


def collect(config, sample):
    raw = load_sample() if sample else []
    if not sample:
        for feed in config["feeds"]:
            try:
                got = fetch_feed(feed)
                note(f"{feed['name']}: {len(got)} items, {sum(1 for g in got if g['image'])} with photos")
                raw += got
            except Exception as exc:  # one bad feed shouldn't stop the build
                note(f"{feed['name']}: skipped ({exc})")
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.get("max_age_days", 21))
    seen, stories = set(), []
    for s in sorted(raw, key=lambda s: s["date"], reverse=True):
        key = re.sub(r"\W+", "", s["title"].lower())[:60]
        if key in seen or s["date"] < cutoff:
            continue
        section = classify(s, s.get("forced"))
        if not section:
            continue
        seen.add(key)
        s["section"] = section
        s["id"] = hashlib.sha1((s["link"] + s["title"]).encode()).hexdigest()[:10]
        stories.append(s)
    if not sample:
        add_photos(stories)
    return stories


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")
STATS = {}


def note(msg):
    """Log a line; on GitHub Actions it also shows as a notice on the run."""
    print(("::notice::" if os.environ.get("GITHUB_ACTIONS") else "  ") + msg, file=sys.stderr)


def fetch(url, data=None, limit=400_000):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept-Language": "en-US,en"})
    if data:
        req.add_header("Content-Type", "application/x-www-form-urlencoded;charset=UTF-8")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.geturl(), resp.read(limit).decode("utf-8", "ignore")


def google_news_url(link):
    """Google News links hide the real article; ask Google for it, like its own redirect page does."""
    gid = urllib.parse.urlparse(link).path.rstrip("/").split("/")[-1]
    _, page = fetch(f"https://news.google.com/rss/articles/{gid}")
    sig = re.search(r'data-n-a-sg="([^"]+)"', page)
    ts = re.search(r'data-n-a-ts="([^"]+)"', page)
    if not (sig and ts):
        return None
    req = ["Fbv4je", json.dumps(["garturlreq", [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None,
            None, None, None, 0, 1], "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0], gid, int(ts.group(1)), sig.group(1)])]
    body = urllib.parse.urlencode({"f.req": json.dumps([[req]])}).encode()
    _, text = fetch("https://news.google.com/_/DotsSplashUi/data/batchexecute", data=body)
    payload = json.loads(text.split("\n\n")[1])
    return json.loads(payload[0][2])[1]


def add_photos(stories, limit=80):
    """Fill in missing photos from each article's own share image (og:image)."""
    todo = [s for s in stories if not s.get("image") and s.get("photos", True)][:limit]
    fails = {}
    for s in todo:
        try:
            if "news.google.com" in s["link"]:
                real = google_news_url(s["link"])
                if not real:
                    raise ValueError("google link not decoded")
                s["link"] = real
                STATS["google_decoded"] = STATS.get("google_decoded", 0) + 1
            _, page = fetch(s["link"])
            found = (re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', page)
                     or re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image', page)
                     or re.search(r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)', page))
            if not found:
                raise ValueError("no og:image")
            image = html.unescape(found.group(1))
            if image.startswith("//"):
                image = "https:" + image
            if image.startswith("https://"):
                s["image"] = image
        except Exception as exc:
            key = f"{urllib.parse.urlparse(s['link']).netloc}: {type(exc).__name__} {str(exc)[:60]}"
            fails[key] = fails.get(key, 0) + 1
    have = sum(1 for s in stories if s.get("image"))
    note(f"Photos: {have} of {len(stories)} stories ({len(todo)} looked up, "
         f"{STATS.get('google_decoded', 0)} Google links decoded)")
    for key, n in sorted(fails.items(), key=lambda kv: -kv[1])[:8]:
        note(f"Photo lookup failed x{n}: {key}")


# ---------- rendering ----------

e = lambda s: html.escape(str(s), quote=True)
FONTS = ("https://fonts.googleapis.com/css2?family=Yellowtail&family=Libre+Caslon+Text:wght@400;700"
         "&family=Libre+Franklin:wght@400;500;600;700&family=Josefin+Sans:wght@600;700&display=swap")
BRAND = HERE / "brand"

# Sunny Mansfield design system: flat, sharp, bordered; brown ink, one orange accent, cream paper. Light in every theme.
CSS = """
/* Sunny Mansfield design system (VoiceBox structure, Sunny branding).
   Layout: a flat paper sheet with 2px rules over the photo collage. Nameplate, sticky section bar,
   lead story with "Also today", bordered story cards, then memories, civic and submit blocks. Radius 0, no shadows. */
:root{
  --primary:#2e1a10; --secondary:#d9531e; --secondary-dk:#b8431a; --secondary-xdk:#963614; --tertiary:#fbf6ec;
  --surface:#f3e6cf; --surface-raised:#ead8b8;
  --text:#2e1a10; --text-2:#6f5443; --text-3:#a8927f;
  --border-subtle:#e6d6bd; --border-medium:#d8c2a0; --border-strong:#2e1a10;
  --serif:"Libre Caslon Text",Georgia,"Times New Roman",serif;
  --sans:"Libre Franklin",-apple-system,"Segoe UI",Helvetica,sans-serif;
  --caps:"Josefin Sans","Futura","Century Gothic",sans-serif;
  --ring:0 0 0 2px var(--tertiary),0 0 0 4px var(--primary);
  color-scheme:light;
}
body{color:var(--text);font:400 16px/1.7 var(--sans);
  background:linear-gradient(rgba(243,230,207,.74),rgba(243,230,207,.74)),url(/assets/collage.jpg) center/cover fixed,var(--surface)}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--tertiary)}
a{color:inherit}
[hidden]{display:none!important}
:focus-visible{outline:none;box-shadow:var(--ring)}
.sheet{max-width:1200px;margin:32px auto;background:rgba(251,246,236,.86);border:2px solid var(--border-strong)}
@media (max-width:1264px){.sheet{margin:0;border-width:0}}
.wrap{padding-left:max(clamp(16px,4vw,48px),env(safe-area-inset-left,0px));padding-right:max(clamp(16px,4vw,48px),env(safe-area-inset-right,0px))}
.caps,.overline{font:700 11px/1.4 var(--caps);letter-spacing:.12em;text-transform:uppercase}
.muted{color:var(--text-2)}

/* nameplate */
.plate{display:grid;grid-template-columns:1fr minmax(0,500px) 1fr;align-items:center;gap:24px;padding-block:32px 24px}
.plate .logo{display:block;line-height:0}
.plate .logo svg{width:100%;height:auto}
.ear{display:grid;gap:4px;align-content:center}
.ear.right{justify-items:end;text-align:right}
@media (max-width:900px){.plate{grid-template-columns:minmax(0,1fr);justify-items:center;padding-block:24px 16px}.ear{display:none}.plate .logo{width:min(100%,420px)}}
.strip{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:8px 24px;padding-block:8px;
  border-top:2px solid var(--border-strong);border-bottom:2px solid var(--border-strong)}
.strip nav{display:flex;gap:24px}
.strip a{text-decoration:none}.strip a:hover{color:var(--secondary)}
@media (max-width:900px){.strip{justify-content:center;text-align:center}}

/* section bar: active item gets the 3px accent underline */
.sections{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:rgba(251,246,236,.97);border-bottom:2px solid var(--border-subtle)}
.sections .wrap{display:flex;gap:8px;overflow-x:auto;scrollbar-width:none}
.tab{font:700 11px/1 var(--caps);letter-spacing:.12em;text-transform:uppercase;white-space:nowrap;background:none;border:0;border-radius:0;
  border-bottom:3px solid transparent;color:var(--text-2);padding:18px 8px 13px;min-height:48px;cursor:pointer}
.tab .n{color:var(--text-3);margin-left:4px;font-variant-numeric:tabular-nums}
.tab:hover{color:var(--text)}
.tab[aria-pressed="true"]{border-bottom-color:var(--secondary);color:var(--text)}
.note{font:500 12px/1.5 var(--sans);letter-spacing:.01em;color:var(--text-2);padding-block:16px 0;margin:0}

/* story parts */
.kicker{color:var(--text)}
.meta{display:flex;flex-wrap:wrap;gap:2px 12px;font:500 12px/1.5 var(--sans);letter-spacing:.01em;color:var(--text-2)}
.photo{display:block;position:relative;overflow:hidden;aspect-ratio:3/2;max-width:100%;background:var(--surface);border-radius:0}
.photo img{width:100%;height:100%;object-fit:cover;display:block;filter:sepia(.18) contrast(1.04)}
.photo .art{position:absolute;inset:0;display:grid;place-items:center}
.photo .art svg{width:40%;max-height:62%;stroke:var(--primary);fill:none;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}
h2.hed,h3.hed{font-family:var(--serif);font-weight:700;margin:0;text-wrap:balance}
.hed a{text-decoration:none}.hed a:hover{text-decoration:underline;text-decoration-thickness:2px;text-underline-offset:4px}
.dek{margin:0;color:var(--text-2)}
.share{display:flex;flex-wrap:wrap;align-items:center;gap:8px 16px}

/* buttons: primary (ink, accent on hover), secondary (outline), ghost (text) */
.btn{font:700 14px/1 var(--sans);letter-spacing:.06em;text-transform:uppercase;display:inline-flex;align-items:center;justify-content:center;gap:8px;
  min-height:44px;padding:10px 24px;border-radius:0;border:2px solid var(--primary);background:transparent;color:var(--primary);text-decoration:none;cursor:pointer}
.btn:hover{background:var(--primary);color:var(--tertiary)}
.btn:active{background:#4a2c1c}
.btn svg{width:14px;height:14px;fill:currentColor;flex:none}
.btn.fb,.btn.solid{background:var(--primary);color:var(--tertiary)}
.btn.fb:hover,.btn.solid:hover{background:var(--secondary);border-color:var(--secondary);color:var(--tertiary)}
.btn.fb:active,.btn.solid:active{background:var(--secondary-dk);border-color:var(--secondary-dk)}
.btn.sm,.card .btn,.shop .btn{font-size:12px;padding:6px 16px}
.textbtn{font:700 12px/1 var(--sans);letter-spacing:.06em;text-transform:uppercase;background:none;border:0;border-radius:0;padding:14px 4px;color:var(--primary);
  cursor:pointer;text-decoration:none;min-height:44px;display:inline-flex;align-items:center}
.textbtn:hover{color:var(--secondary)}.textbtn:active{color:var(--secondary-dk)}

/* front page */
.front{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:48px;padding-block:32px 64px}
@media (max-width:860px){.front{grid-template-columns:minmax(0,1fr);gap:32px;padding-block:24px 48px}}
.lead{display:grid;gap:16px;align-content:start;min-width:0;background:var(--tertiary);padding:0 0 24px;border-bottom:3px solid var(--secondary);padding-bottom:24px}
.lead .hed{font-size:clamp(34px,4.6vw,56px);line-height:1.05;letter-spacing:-.02em}
.lead .dek{font-size:20px;line-height:1.65;max-width:60ch}
.also{display:grid;align-content:start;min-width:0;background:var(--tertiary);padding:16px;align-self:start}
.label{font:700 11px/1.4 var(--caps);letter-spacing:.12em;text-transform:uppercase;margin:0;padding-bottom:8px;border-bottom:2px solid var(--border-strong)}
.brief{display:grid;grid-template-columns:minmax(0,1fr) 88px;gap:16px;padding:14px 8px;margin-inline:-8px;border-bottom:1px solid var(--border-subtle)}
.brief:hover{background:var(--surface)}
.brief .hed{font-size:16px;line-height:1.35;margin-block:4px 8px}
.brief .photo{aspect-ratio:1}
.brief .photo .art svg{width:56%}

/* grid of default cards */
.latest{padding-block:0 64px;scroll-margin-top:64px}
.latest .label{margin-bottom:24px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}
@media (max-width:960px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:600px){.grid{grid-template-columns:minmax(0,1fr)}}
.card{display:grid;gap:12px;align-content:start;min-width:0;background:var(--tertiary);border:2px solid var(--border-subtle);padding:24px}
.card:hover{border-color:var(--border-strong)}
.card .photo{margin:-24px -24px 4px;max-width:none;width:calc(100% + 48px)}
.card .hed{font-size:24px;line-height:1.2;letter-spacing:-.01em}
.card .dek{font-size:14px;line-height:1.6;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.empty{grid-column:1/-1;margin:0;color:var(--text-2)}
.more-row{display:flex;justify-content:center;padding-top:32px}

/* candy-stripe frame: the "Greetings from" postcard look, used for memories and the closing call to action */
.candy{background:repeating-linear-gradient(-55deg,var(--secondary) 0 14px,var(--tertiary) 14px 30px);padding:16px}
.candy-in{background:var(--tertiary);border:3px solid var(--secondary)}
.script{font:400 clamp(26px,3vw,36px)/1 "Yellowtail","Brush Script MT",cursive;color:var(--secondary);display:block;transform:rotate(-5deg);margin-bottom:10px}
.retro{font:700 clamp(40px,7vw,84px)/.95 var(--caps);text-transform:uppercase;letter-spacing:.04em;color:var(--secondary);margin:0;
  text-shadow:2px 2px 0 var(--tertiary),5px 5px 0 var(--primary);display:block}
.retro.sm{font-size:clamp(30px,4vw,48px)}
.memories{display:grid;gap:24px;justify-items:center;text-align:center;padding:32px clamp(16px,3vw,32px);margin-bottom:64px}
@media (max-width:860px){.memories{margin-bottom:48px}}
.memories{margin-bottom:0}.candy{margin-bottom:64px}
.memories p{margin:0;color:var(--text-2);font-size:14px;line-height:1.6}
.greet{display:grid;justify-items:center}
.greet .script{margin-bottom:2px;position:relative;z-index:1}
.postcards{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;width:100%}
@media (max-width:600px){.postcards{grid-template-columns:repeat(2,minmax(0,1fr))}}
.postcard{margin:0;background:var(--tertiary);border:2px solid var(--border-medium);padding:8px;display:grid;gap:8px}
.postcard svg,.postcard img{display:block;width:100%;height:auto;aspect-ratio:300/190;object-fit:cover}
.postcard figcaption{font:500 12px/1.5 var(--sans);color:var(--text-2)}
.link{color:var(--primary);font:700 12px/1 var(--sans);letter-spacing:.06em;text-transform:uppercase;text-decoration:none;
  border-bottom:2px solid var(--primary);padding-bottom:4px}
.link:hover{color:var(--secondary);border-color:var(--secondary)}

/* civic: elevated card for voting, list for shops */
.civic{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.3fr);gap:48px;padding-block:0 64px}
@media (max-width:860px){.civic{grid-template-columns:minmax(0,1fr);gap:32px;padding-block:0 48px}}
.box{align-self:start;background:var(--tertiary);border:2px solid var(--border-subtle);border-top:4px solid var(--secondary);padding:24px;display:grid;gap:16px;align-content:start}
.box .hed{font-size:24px;line-height:1.2}
.steps{margin:0;padding:0;list-style:none;display:grid;gap:0;font-size:14px;line-height:1.6}
.steps li{display:grid;grid-template-columns:24px minmax(0,1fr);gap:8px;padding-block:14px;border-bottom:1px solid var(--border-subtle)}
.steps li:last-child{border-bottom:0}
.steps .n{font:700 14px/1.6 var(--sans)}
.shoplist{display:grid}
.shop{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:4px 16px;align-items:center;padding-block:14px;border-bottom:1px solid var(--border-subtle)}
.shop:last-child{border-bottom:0}
.shop .hed{font-size:20px}
.shop p{margin:0;color:var(--text-2);font-size:14px;line-height:1.6;grid-column:1}
.shop .share{grid-row:1/span 3;grid-column:2}
@media (max-width:600px){.shop{grid-template-columns:minmax(0,1fr)}.shop .share{grid-row:auto;grid-column:1}}

/* submit: the last call to action */
.submit{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:16px 32px;padding:28px clamp(16px,3vw,32px)}
.submit .script{margin-bottom:22px}
.submit p{margin:12px 0 0;max-width:56ch;color:var(--text-2)}
#submit .candy{margin-bottom:48px}

/* candy stripes: the one decorative brand element, used as trim only */
.stripes{height:14px;background:repeating-linear-gradient(-55deg,var(--secondary) 0 9px,var(--tertiary) 9px 18px)}
footer{background:var(--primary);color:var(--tertiary)}
footer .sky{display:block;width:100%;height:auto;color:var(--tertiary);opacity:.12}
footer .wrap{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:16px 48px;padding-block:24px 32px}
footer .logo{width:220px;line-height:0}footer .logo svg{width:100%;height:auto}
footer p{margin:0;max-width:56ch;font-size:14px;line-height:1.6;opacity:.85}

.toast{position:fixed;left:50%;bottom:calc(24px + env(safe-area-inset-bottom,0px));transform:translateX(-50%);z-index:20;max-width:min(92vw,440px);
  background:var(--primary);color:var(--tertiary);padding:8px 16px;border-radius:0;font:600 14px/1.5 var(--sans);text-align:center}
"""

SEPIA = {"sky": "#ead6ae", "light": "#d8b98a", "mid": "#a97b4f", "dark": "#5b3a22", "paper": "#f6ead2"}


def halftone(uid):
    return (f'<defs><pattern id="ht{uid}" width="4" height="4" patternUnits="userSpaceOnUse">'
            f'<circle cx="2" cy="2" r=".9" fill="{SEPIA["dark"]}"/></pattern></defs>')


def scene(kind, uid):
    c = SEPIA
    windows = lambda x0, x1, y0, y1, dx=12, dy=14: "".join(
        f'<rect x="{x}" y="{y}" width="5" height="8" fill="{c["sky"]}" opacity=".8"/>'
        for x in range(x0, x1, dx) for y in range(y0, y1, dy))
    if kind == "main":
        art = f"""<rect width="300" height="190" fill="{c['sky']}"/>
<rect x="128" y="86" width="14" height="26" fill="{c['mid']}"/><rect x="146" y="78" width="10" height="34" fill="{c['mid']}"/><rect x="158" y="90" width="16" height="22" fill="{c['mid']}"/>
<polygon points="0,190 300,190 172,112 128,112" fill="{c['light']}"/>
<polygon points="0,22 128,98 128,114 0,182" fill="{c['mid']}"/><polygon points="300,14 172,98 172,114 300,184" fill="{c['dark']}"/>
{''.join(f'<line x1="{x}" y1="{22 + x*0.59:.0f}" x2="{x}" y2="{182 - x*0.53:.0f}" stroke="{c["dark"]}" stroke-width="2"/>' for x in (34, 70, 100))}
{''.join(f'<line x1="0" y1="{y}" x2="128" y2="{98 + (y-22)*0.21:.0f}" stroke="{c["sky"]}" stroke-width="2" stroke-dasharray="5 6" opacity=".7"/>' for y in (50, 80, 110, 140))}
{''.join(f'<line x1="300" y1="{y}" x2="172" y2="{98 + (y-14)*0.19:.0f}" stroke="{c["sky"]}" stroke-width="2" stroke-dasharray="5 6" opacity=".55"/>' for y in (44, 76, 108, 140))}
<path d="M0 168l110-52M300 172l-112-56" stroke="{c['sky']}" stroke-width="3"/>
<line x1="70" y1="152" x2="70" y2="112" stroke="{c['dark']}" stroke-width="3"/><circle cx="70" cy="108" r="5" fill="{c['paper']}" stroke="{c['dark']}" stroke-width="2"/>
<line x1="236" y1="160" x2="236" y2="114" stroke="{c['sky']}" stroke-width="3"/><circle cx="236" cy="110" r="5" fill="{c['paper']}"/>
<path d="M150 170h58v-12l-10-10h-30l-8 10h-10z" fill="{c['dark']}"/><circle cx="160" cy="172" r="7" fill="{c['dark']}" stroke="{c['sky']}" stroke-width="2"/><circle cx="198" cy="172" r="7" fill="{c['dark']}" stroke="{c['sky']}" stroke-width="2"/>"""
    elif kind == "carrousel":
        horses = "".join(f'<g fill="{c["dark"]}"><ellipse cx="{x}" cy="{118 + (i % 2) * 8}" rx="12" ry="6"/><path d="M{x+9} {114 + (i % 2) * 8}l8-10 4 3-6 9z"/>'
                         f'<path d="M{x-8} {122 + (i % 2) * 8}l-3 10M{x+6} {122 + (i % 2) * 8}l3 10" stroke="{c["dark"]}" stroke-width="2.5"/></g>'
                         for i, x in enumerate(range(82, 230, 36)))
        art = f"""<rect width="300" height="190" fill="{c['sky']}"/><rect y="150" width="300" height="40" fill="{c['light']}"/>
<circle cx="30" cy="120" r="26" fill="{c['mid']}"/><circle cx="276" cy="116" r="30" fill="{c['mid']}"/>
<polygon points="50,84 150,34 250,84" fill="{c['dark']}"/><rect x="146" y="14" width="3" height="22" fill="{c['dark']}"/><path d="M149 14l14 5-14 5z" fill="{c['mid']}"/>
{''.join(f'<circle cx="{x}" cy="86" r="9" fill="{c["mid"]}"/>' for x in range(58, 250, 17))}
<rect x="50" y="84" width="200" height="8" fill="{c['dark']}"/>
{''.join(f'<line x1="{x}" y1="92" x2="{x}" y2="150" stroke="{c["dark"]}" stroke-width="3"/>' for x in range(64, 244, 36))}
{horses}<rect x="44" y="146" width="212" height="8" fill="{c['dark']}"/>"""
    elif kind == "reformatory":
        art = f"""<rect width="300" height="190" fill="{c['sky']}"/><rect y="158" width="300" height="32" fill="{c['light']}"/>
<rect x="26" y="96" width="248" height="64" fill="{c['mid']}"/>
<polygon points="20,98 60,74 100,98" fill="{c['dark']}"/><polygon points="200,98 240,74 280,98" fill="{c['dark']}"/>
<rect x="66" y="66" width="30" height="94" fill="{c['mid']}"/><polygon points="62,68 81,30 100,68" fill="{c['dark']}"/>
<rect x="204" y="66" width="30" height="94" fill="{c['mid']}"/><polygon points="200,68 219,30 238,68" fill="{c['dark']}"/>
<rect x="128" y="44" width="44" height="116" fill="{c['mid']}"/><polygon points="122,46 150,6 178,46" fill="{c['dark']}"/>
{windows(34, 62, 106, 156)}{windows(104, 126, 106, 156)}{windows(178, 200, 106, 156)}{windows(240, 270, 106, 156)}
{windows(72, 94, 76, 156, 12, 16)}{windows(210, 232, 76, 156, 12, 16)}{windows(134, 170, 56, 150, 12, 16)}
<rect x="140" y="132" width="20" height="28" rx="10" fill="{c['dark']}"/>
<path d="M150 190l-6-30h12z" fill="{c['paper']}"/>"""
    else:  # park fountain
        art = f"""<rect width="300" height="190" fill="{c['sky']}"/><rect y="120" width="300" height="70" fill="{c['light']}"/>
<circle cx="40" cy="90" r="38" fill="{c['mid']}"/><circle cx="84" cy="70" r="30" fill="{c['dark']}" opacity=".85"/>
<circle cx="258" cy="84" r="40" fill="{c['mid']}"/><circle cx="214" cy="66" r="26" fill="{c['dark']}" opacity=".85"/>
<path d="M120 190l20-60h20l20 60z" fill="{c['paper']}" opacity=".6"/>
<ellipse cx="150" cy="150" rx="64" ry="14" fill="{c['dark']}"/><ellipse cx="150" cy="146" rx="58" ry="10" fill="{c['sky']}"/>
<rect x="144" y="104" width="12" height="44" fill="{c['dark']}"/><ellipse cx="150" cy="106" rx="30" ry="7" fill="{c['dark']}"/>
<rect x="146" y="78" width="8" height="28" fill="{c['dark']}"/><ellipse cx="150" cy="80" rx="16" ry="4" fill="{c['dark']}"/>
<path d="M150 74c-6-14-18-16-26-4M150 74c6-14 18-16 26 4M126 106c-10 4-16 16-18 30M174 106c10 4 16 16 18 30" fill="none" stroke="{c['paper']}" stroke-width="2.5"/>
<line x1="104" y1="160" x2="104" y2="118" stroke="{c['dark']}" stroke-width="3"/><circle cx="104" cy="114" r="5" fill="{c['paper']}" stroke="{c['dark']}" stroke-width="2"/>
<line x1="196" y1="160" x2="196" y2="118" stroke="{c['dark']}" stroke-width="3"/><circle cx="196" cy="114" r="5" fill="{c['paper']}" stroke="{c['dark']}" stroke-width="2"/>"""
    return (f'<svg viewBox="0 0 300 190" aria-hidden="true">{halftone(uid)}{art}'
            f'<rect width="300" height="190" fill="url(#ht{uid})" opacity=".14"/></svg>')


POSTCARDS = [("main", "Main Street, looking south"), ("carrousel", "The carrousel downtown"),
             ("reformatory", "The old Reformatory"), ("park", "Central Park fountain")]


def skyline():
    """Silhouette of downtown: storefront blocks, the carrousel, the courthouse dome, the Reformatory towers."""
    shapes = []
    x = 0
    for w, h in [(70, 70), (46, 96), (80, 60), (54, 110), (60, 84)]:
        shapes.append(f'<rect x="{x}" y="{160-h}" width="{w}" height="{h}"/><rect x="{x-3}" y="{160-h-6}" width="{w+6}" height="8"/>')
        x += w + 4
    # carrousel tent
    shapes.append('<path d="M330 160v-46h120v46zM318 116l72-46 72 46zM388 70v-22l18 6-18 6"/>')
    x = 470
    for w, h in [(58, 76), (40, 120), (72, 66)]:
        shapes.append(f'<rect x="{x}" y="{160-h}" width="{w}" height="{h}"/><rect x="{x-3}" y="{160-h-6}" width="{w+6}" height="8"/>')
        x += w + 4
    # courthouse with dome
    shapes.append('<path d="M650 160V96h130v64zM640 98l75-26 75 26zM690 74a25 25 0 0 1 50 0zM712 50h6v10h-6z"/>')
    # reformatory: central tower and wings with pointed roofs
    shapes.append('<path d="M800 160V102h70v58zM800 104l35-30 35 30zM880 160V60h52v100zM874 62l32-42 32 42zM942 160V102h70v58zM942 104l35-30 35 30z"/>')
    x = 1022
    for w, h in [(60, 88), (44, 64), (70, 100)]:
        shapes.append(f'<rect x="{x}" y="{160-h}" width="{w}" height="{h}"/><rect x="{x-3}" y="{160-h-6}" width="{w+6}" height="8"/>')
        x += w + 4
    return f'<svg class="skyline" viewBox="0 0 1200 160" preserveAspectRatio="xMidYMax meet" aria-hidden="true" fill="currentColor">{"".join(shapes)}</svg>'


ART = {
    "good": '<circle cx="50" cy="50" r="16"/><path d="M50 18v-8M50 90v-8M18 50h-8M90 50h-8M27 27l-6-6M79 79l-6-6M27 73l-6 6M79 21l-6 6"/>',
    "hoods": '<path d="M8 86h84M16 86V52l20-16 20 16v34M56 86V44l18-14 18 14v42M28 86V68h16v18M66 58h16M66 70h16"/>',
    "biz": '<path d="M12 86h76M18 86V44h64v42M12 44l8-24h60l8 24M12 44c0 6 6 10 12 10s12-4 12-10c0 6 6 10 14 10s14-4 14-10c0 6 6 10 12 10s12-4 12-10M42 86V64h16v22"/>',
    "sales": '<path d="M52 12h34v34L46 86 12 52z"/><circle cx="72" cy="26" r="5"/><path d="M34 58l12 12M42 50l12 12"/>',
    "seniors": '<path d="M50 84S16 64 16 38c0-12 9-20 19-20 7 0 12 4 15 9 3-5 8-9 15-9 10 0 19 8 19 20 0 26-34 46-34 46z"/>',
    "life": '<path d="M50 88V48M50 48c-10-2-20-12-20-26 12 0 20 10 20 26zM50 48c10-2 20-12 20-26-12 0-20 10-20 26zM50 66c-8 0-16-6-18-16 10 0 16 6 18 16zM50 66c8 0 16-6 18-16-10 0-16 6-18 16z"/>',
    "vote": '<path d="M14 54h72v34H14zM30 54V18h40v36M40 36l8 8 14-16"/>',
    "research": '<path d="M38 12h24M42 12v28L18 82c-2 4 1 8 5 8h54c4 0 7-4 5-8L58 40V12M28 66h44"/>',
}


def fb_share(url):
    return "https://www.facebook.com/sharer/sharer.php?u=" + urllib.request.quote(url, safe="")


def ago(dt):
    hours = int((datetime.now(timezone.utc) - dt).total_seconds() // 3600)
    if hours < 1:
        return "Just now"
    if hours < 24:
        return f"{hours}h ago"
    return dt.strftime("%b %-d")


def page_url(cfg, s):
    return f"{cfg['site_url'].rstrip('/')}/s/{s['id']}.html"


def render_feed(cfg, stories):
    site = cfg["site_url"].rstrip("/")
    items = "".join(f"""<item><title>{e(s['title'])}</title><link>{e(page_url(cfg, s))}</link>
<guid isPermaLink="false">{s['id']}</guid><category>{e(SECTION_NAMES[s['section']])}</category>
<pubDate>{email.utils.format_datetime(s['date'])}</pubDate>
<description>{e(s['summary'] + ' (via ' + s['source'] + ')')}</description></item>""" for s in stories[:50])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>{e(cfg['site_name'])}</title><link>{e(site)}/</link>
<description>{e(cfg['tagline'])}</description>{items}</channel></rss>"""


FB_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 8h3V4h-3c-2.8 0-4 1.7-4 4.3V10H7v4h3v8h4v-8h3l1-4h-4V8.6c0-.4.3-.6 1-.6z"/></svg>'


def logo(name="logo-horizontal.svg"):
    """Inline one of the outlined logo files from brand/ (made by tools/make_logo.py)."""
    return (BRAND / name).read_text().replace('xmlns="http://www.w3.org/2000/svg" ', "")


def head(cfg, title, desc, url, image=None):
    image = image or cfg["site_url"].rstrip("/") + "/og.png"
    return f"""<title>{e(title)}</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="description" content="{e(desc)}">
<meta property="og:type" content="article"><meta property="og:site_name" content="{e(cfg['site_name'])}">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(url)}"><meta property="og:image" content="{e(image)}">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" type="image/svg+xml" href="/brand/mark.svg">
<link rel="alternate" type="application/rss+xml" title="{e(cfg['site_name'])}" href="{e(cfg['site_url'].rstrip('/'))}/feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<style>{CSS}</style>"""


def photo(s):
    src = s.get("image") or ""
    src = src if src.startswith("https://") else "/" + src  # sample photos live under /assets
    inner = (f'<img src="{e(src)}" alt="" loading="lazy" referrerpolicy="no-referrer">' if s.get("image")
             else f'<span class="art"><svg viewBox="0 0 100 100" aria-hidden="true">{ART[s["section"]]}</svg></span>')
    return f'<a class="photo" href="{e(s["link"])}" target="_blank" rel="noopener" tabindex="-1" aria-hidden="true">{inner}</a>'


def kicker(s):
    return f'<span class="caps kicker">{e(SECTION_NAMES[s["section"]])}</span>'


def meta(s):
    return f'<div class="meta"><span>{e(s["source"])}</span><span>{ago(s["date"])}</span></div>'


def share(cfg, s, read=False):
    url = page_url(cfg, s)
    out = (f'<a class="btn fb" href="{e(fb_share(url))}" target="_blank" rel="noopener">{FB_ICON}Share</a>'
           f'<button class="textbtn" type="button" data-copy="{e(url)}">Copy link</button>')
    if read:
        out += f'<a class="textbtn" href="{e(s["link"])}" target="_blank" rel="noopener">Read at {e(s["source"])}</a>'
    return f'<div class="share">{out}</div>'


def hed(s, tag="h3"):
    return f'<{tag} class="hed"><a href="{e(s["link"])}" target="_blank" rel="noopener">{e(s["title"])}</a></{tag}>'


def lead(cfg, s):
    dek = f'<p class="dek">{e(s["summary"])}</p>' if s["summary"] else ""
    return f'<article class="lead">{photo(s)}{kicker(s)}{hed(s, "h2")}{dek}{meta(s)}{share(cfg, s, read=True)}</article>'


def brief(s):
    return f'<article class="brief"><div>{kicker(s)}{hed(s)}<div style="margin-top:8px">{meta(s)}</div></div>{photo(s)}</article>'


def card(cfg, s, front=False):
    dek = f'<p class="dek">{e(s["summary"])}</p>' if s["summary"] else ""
    flag = ' data-front="1"' if front else ""
    return (f'<article class="card" data-sec="{s["section"]}"{flag}>{photo(s)}{kicker(s)}{hed(s)}{dek}{meta(s)}'
            f'{share(cfg, s)}</article>')


def masthead(cfg, site, stories):
    today = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y")
    return f"""<header>
<div class="wrap plate">
<div class="ear"><span class="caps">{today}</span><span class="caps muted">{len(stories)} good stories this week</span></div>
<a class="logo" href="{e(site)}/" aria-label="{e(cfg['site_name'])} home">{logo("logo-retro.svg")}</a>
<div class="ear right"><a class="btn fb" href="{e(fb_share(site + '/'))}" target="_blank" rel="noopener">{FB_ICON}Share the paper</a>
<span class="caps muted">Free · Updated every 2 hours</span></div>
</div>
<div class="wrap strip"><span class="caps">Good news for all seasons · Mansfield &amp; Richland County, Ohio</span>
<nav class="caps"><a href="#subscribe">Subscribe</a><a href="#submit">Submit news</a></nav></div>
</header>"""


def memories(cfg):
    real = cfg.get("postcards") or []
    if real:
        cards = "".join(f'<figure class="postcard"><img src="{e(p["image"])}" alt="{e(p["caption"])}" loading="lazy">'
                        f'<figcaption>{e(p["caption"])}</figcaption></figure>' for p in real[:4])
    else:
        cards = "".join(f'<figure class="postcard">{scene(k, i)}<figcaption>{e(cap)}</figcaption></figure>'
                        for i, (k, cap) in enumerate(POSTCARDS))
    return f"""<section class="wrap" aria-labelledby="mem-h"><div class="candy"><div class="candy-in memories">
<div class="greet" id="mem-h"><span class="script">Greetings from</span><span class="retro">Mansfield</span></div>
<div class="postcards">{cards}</div>
<p>Postcards and snapshots from around town. Got an old photo in a shoebox? <a class="link" href="#submit">Send it in</a></p>
</div></div></section>"""


def civic(cfg, businesses):
    shops = "".join(f"""<div class="shop"><span class="caps kicker">{e(b.get('neighborhood', ''))}</span><h3 class="hed">{e(b['name'])}</h3><p>{e(b['what'])}</p>
<div class="share"><a class="btn fb" href="{e(fb_share(b['link']))}" target="_blank" rel="noopener">{FB_ICON}Share</a>
<a class="textbtn" href="{e(b['link'])}" target="_blank" rel="noopener">Visit</a></div></div>""" for b in businesses[:3])
    return f"""<section class="wrap civic">
<div class="box" id="vote"><span class="caps kicker">Election Day · Tuesday, Nov. 3, 2026</span><h2 class="hed">Your vote, made easy</h2>
<ol class="steps">
<li><span class="n">1</span><span><a href="https://voteohio.gov/" target="_blank" rel="noopener">Check your registration and polling place</a> at VoteOhio.gov.</span></li>
<li><span class="n">2</span><span>Vote early at the <a href="https://www.boe.ohio.gov/richland/" target="_blank" rel="noopener">Richland County Board of Elections</a>, or on Election Day from 6:30 a.m. to 7:30 p.m.</span></li>
<li><span class="n">3</span><span>Bring a photo ID: an Ohio driver's license or state ID, a U.S. passport, or a military ID.</span></li>
</ol></div>
<div id="shops"><h2 class="label">Shop local</h2><div class="shoplist">{shops}</div>
<p class="note">Free spotlights for Richland County businesses. Email {e(cfg['submit_email'])}.</p></div>
</section>"""


def submit_band(cfg, site):
    return f"""<section class="wrap" id="submit"><div class="candy"><div class="candy-in submit">
<div><span class="script">Hey neighbor!</span><h2 class="retro sm">Got good news?</h2>
<p>Ribbon cuttings, 100th birthdays, block parties, garage sales and kids doing great things. Send it to <b>{e(cfg['submit_email'])}</b>.</p></div>
<div class="share" id="subscribe"><a class="btn solid" href="{e(site)}/feed.xml" target="_blank" rel="noopener">Subscribe by RSS</a></div>
</div></div></section>"""


def footer(cfg):
    return f"""<footer>{skyline().replace('class="skyline"', 'class="sky"')}<div class="wrap">
<span class="logo">{logo("logo-retro-reverse.svg")}</span>
<p>We share headlines, short summaries and source photos, and every story links to the original reporting.
Please read and support Richland Source, the Mansfield News Journal and our other local outlets.</p>
<span class="caps">Updated {datetime.now(timezone.utc).strftime('%b %-d, %Y')}</span></div></footer>"""


SCRIPT = """<script>
(()=>{const PAGE=9;const tabs=[...document.querySelectorAll('.sections .tab')];
const cards=[...document.querySelectorAll('.grid .card')];const empty=document.querySelector('.grid .empty');
const more=document.getElementById('more');const label=document.querySelector('#latest .label');
let current='all',limit=PAGE;
function store(k,v){try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v)}catch(_){return null}}
function render(){let n=0,shown=0;
 cards.forEach(c=>{const match=current==='all'?!c.dataset.front:c.dataset.sec===current;n+=match;
  const on=match&&shown<limit;shown+=on;c.hidden=!on});
 if(empty)empty.hidden=n>0;if(more){more.hidden=shown>=n;more.textContent=`Show ${Math.min(PAGE,n-shown)} more stories`}}
function show(sec,scroll){sec=(sec||'').toLowerCase();const t=tabs.find(x=>x.dataset.sec===sec)||tabs[0];if(!t)return;
 current=t.dataset.sec;limit=PAGE;tabs.forEach(x=>x.setAttribute('aria-pressed',x===t));
 if(label)label.firstChild.textContent=current==='all'?'More good news':t.firstChild.textContent.trim();render();
 if(scroll)document.getElementById('latest').scrollIntoView({behavior:'smooth',block:'start'})}
tabs.forEach(t=>t.addEventListener('click',()=>{show(t.dataset.sec,true);history.replaceState(null,'','#'+t.dataset.sec)}));
if(more)more.addEventListener('click',()=>{limit+=PAGE;render()});
show(decodeURIComponent(location.hash.slice(1))||'all',false);
const toast=document.getElementById('toast');let timer;
function say(msg){if(!toast)return;toast.textContent=msg;toast.hidden=false;clearTimeout(timer);timer=setTimeout(()=>toast.hidden=true,3600)}
function shared(){const now=new Date();const week=now.getFullYear()+'-'+Math.floor((now-new Date(now.getFullYear(),0,1))/6048e5);
 let d={};try{d=JSON.parse(store('shares')||'{}')}catch(_){}if(d.week!==week)d={week,n:0};d.n++;store('shares',JSON.stringify(d));
 return d.n<3?`Thanks for spreading good news! ${d.n} of 3 shares this week.`:d.n===3?'Three shares this week. You are an official Sunny Spreader!':`Shared ${d.n} stories this week. Mansfield thanks you!`}
document.addEventListener('click',ev=>{
 if(ev.target.closest('.btn.fb')){say(shared());return}
 const b=ev.target.closest('[data-copy]');if(!b)return;
 const done=t=>{b.textContent=t;setTimeout(()=>b.textContent='Copy link',1800)};
 if(navigator.clipboard){navigator.clipboard.writeText(b.dataset.copy).then(()=>{done('Copied');say('Link copied. Paste it into a post, a text or a group chat.')},()=>done('Copy failed'))}});
})();
</script>"""


def render_index(cfg, stories, businesses, preview):
    site = cfg["site_url"].rstrip("/")
    news = [s for s in stories if s["section"] != "life"]
    top, also = (news[0], news[1:5]) if news else (None, [])
    front_ids = {s["id"] for s in ([top] if top else []) + also}
    counts = {k: sum(1 for s in stories if s["section"] == k) for k, _ in SECTIONS}
    tabs = f'<button class="tab" type="button" data-sec="all">All <span class="n">{len(stories)}</span></button>' + "".join(
        f'<button class="tab" type="button" data-sec="{k}">{e(n)} <span class="n">{counts[k]}</span></button>'
        for k, n in SECTIONS if counts[k])
    note = ""
    front = ""
    if top:
        front = (f'<section class="wrap front">{lead(cfg, top)}<aside class="also"><h2 class="label">Also today</h2>'
                 f'{"".join(brief(s) for s in also)}</aside></section>')
    grid = "".join(card(cfg, s, front=s["id"] in front_ids) for s in stories)
    return f"""{head(cfg, cfg['site_name'], cfg['tagline'], site + '/')}
<div class="sheet"><div class="stripes" aria-hidden="true"></div>
{masthead(cfg, site, stories)}
<nav class="sections" aria-label="Sections"><div class="wrap">{tabs}</div></nav>
<main>{note}{front}
<section class="wrap latest" id="latest"><h2 class="label">More good news</h2>
<div class="grid">{grid}<p class="empty" hidden>Nothing here this week yet. Know a story? Email {e(cfg['submit_email'])}.</p></div>
<div class="more-row"><button class="btn" type="button" id="more" hidden>Show more stories</button></div></section>
{memories(cfg)}
{civic(cfg, businesses)}
{submit_band(cfg, site)}
<div class="stripes" aria-hidden="true"></div>
</main>
{footer(cfg)}
</div>
<div class="toast" id="toast" role="status" aria-live="polite" hidden></div>
{SCRIPT}"""


def render_story_page(cfg, s, stories):
    site = cfg["site_url"].rstrip("/")
    desc = s["summary"] or f"Good news from {cfg['site_name']}"
    image = s["image"] if (s.get("image") or "").startswith("https://") else None
    more = "".join(card(cfg, o) for o in [o for o in stories if o is not s][:3])
    body = f"""<div class="sheet"><div class="stripes" aria-hidden="true"></div>{masthead(cfg, site, stories)}<main>
<section class="wrap front" style="grid-template-columns:minmax(0,1fr)">{lead(cfg, s)}</section>
<section class="wrap latest"><h2 class="label">More good news</h2><div class="grid">{more}</div>
<div class="more-row"><a class="btn" href="{e(site)}/">See all stories</a></div></section>
{submit_band(cfg, site)}<div class="stripes" aria-hidden="true"></div></main>{footer(cfg)}</div>
<div class="toast" id="toast" role="status" aria-live="polite" hidden></div>"""
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
            f"{head(cfg, s['title'], desc, page_url(cfg, s), image)}</head><body>{body}{SCRIPT}</body></html>")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="use sample_stories.json instead of live feeds")
    ap.add_argument("--out", default=str(HERE / "site"))
    args = ap.parse_args()
    cfg = json.loads((HERE / "config.json").read_text())
    cfg["site_url"] = os.environ.get("SITE_URL") or cfg["site_url"]  # set by the GitHub workflow
    # Sites served from a sub-folder (like user.github.io/sunny-mansfield) need it on local file paths.
    base = urllib.parse.urlparse(cfg["site_url"]).path.rstrip("/")
    rebase = lambda text: re.sub(r'(["(])/(assets|brand)/', rf"\1{base}/\2/", text)
    businesses = json.loads((HERE / "businesses.json").read_text())
    stories = collect(cfg, args.sample)
    out = Path(args.out)
    (out / "s").mkdir(parents=True, exist_ok=True)
    page = render_index(cfg, stories, businesses, args.sample)
    (out / "index.html").write_text(rebase(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
        + page.replace('<div class="sheet">', '</head><body><div class="sheet">', 1) + "</body></html>"))
    for folder in ("assets", "brand"):
        shutil.copytree(HERE / folder, out / folder, dirs_exist_ok=True)
    if (HERE / "brand" / "og.png").exists():
        shutil.copy(HERE / "brand" / "og.png", out / "og.png")
    if (HERE / "postcards").is_dir():
        shutil.copytree(HERE / "postcards", out / "postcards", dirs_exist_ok=True)
    # The private preview can't load files, so inline every /assets image and drop the favicon link.
    inline = lambda m: "data:image/jpeg;base64," + base64.b64encode((HERE / m.group(1)).read_bytes()).decode()
    preview = re.sub(r"/(assets/[\w./-]+\.jpg)", inline, page).replace('<link rel="icon" type="image/svg+xml" href="/brand/mark.svg">\n', "")
    (out / "artifact.html").write_text(preview)
    for s in stories:
        (out / "s" / f"{s['id']}.html").write_text(rebase(render_story_page(cfg, s, stories)))
    (out / "feed.xml").write_text(render_feed(cfg, stories))
    print(f"Built {len(stories)} stories -> {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
