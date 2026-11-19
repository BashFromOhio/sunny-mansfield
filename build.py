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
import csv
import io
import email.utils
import hashlib
import html
import json
import base64
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

SECTIONS = [
    ("good", "Good News"),
    ("sports", "Sports"),
    ("hoods", "Neighborhoods"),
    ("biz", "Local Business"),
    ("sales", "Garage Sales"),
    ("seniors", "Seniors"),
]
SECTION_NAMES = dict(SECTIONS)

# A story with any of these words is dropped, however upbeat the rest is.
NEGATIVE = r"""shoot|shot|killed|kill|murder|homicide|stabb|dead\b|death|died|dies|fatal|crash|
wreck|arrest|charged|indict|sentenc|convict|prison|jail|inmate|police|sheriff|deputies|
assault|rape|abuse|theft|stolen|robber|burglar|overdose|drug|fentanyl|meth|fire\b|blaze|
lawsuit|sued|lawsuit|layoff|laid off|closing|closes|shut down|bankrupt|missing|injur|
victim|weapon|gun|trial|guilty|scam|fraud|evict|warning|threat|lockdown|outage|crime|
investigat|suspect|accident|hospitalized|emergency|traffic impact|detour|lane restriction|road closure"""

# A story needs at least one of these to count as good news.
POSITIVE = r"""celebrat|award|honor|win\b|wins|won\b|champion|record|first place|scholarship|
graduat|donat|volunteer|grant|raise[sd]? \$|fundrais|opens|opening|grand opening|ribbon|
expan|new\b|launch|milestone|anniversary|birthday|festival|parade|concert|fair\b|
celebration|thank|hero|kindness|helps|helping|rescue|adopt|garden|plant|park|mural|
restor|renovat|reopen|welcome|proud|success|achiev|inspir|hiring|jobs|community|free\b|
students|kids|library|cheer|smile|gift|joy|family|reunion|bloom|harvest|sanctuary|
shop small|shop local|small business saturday|hall of fame|all-ohio|all-state|nominat"""

# Section rules, checked in order. First match wins; "good" is the fallback.
SECTION_RULES = [
    ("sales", r"garage sale|yard sale|rummage|flea market|estate sale|porch sale|swap meet"),
    ("sports", r"\b(football|basketball|volleyball|soccer|baseball|softball|wrestl\w*|swim\w*|cross country|"
               r"track and field|golf|tennis|bowling|hockey|lacrosse|cheerlead\w*|athlet\w*|varsity|playoffs?|"
               r"ohsaa|state champion\w*|district champion\w*|regional champion\w*|tygers|quarterback|touchdown)\b"),
    # Whole words only: a bare "aging" also matched "managing", "packaging" and "engaging".
    ("seniors", r"\b(senior citizens?|senior centers?|senior living|seniors\b(?! night| day| class| game| prom)|"
                r"retire(e|es|d|ment)?\b|centenarian|100th birthday|(9\d|1\d\d)(th)? birthday|older adults|"
                r"aging\b|grandparents?\b|veterans?\b(?! (coach|chef|player|teacher|owner|journalist))|nursing homes?\b|assisted living)"),
    ("biz", r"business|grand opening|ribbon cutting|shop\b|store|restaurant|cafe|bakery|brewery|"
            r"owner|entrepreneur|downtown mansfield|chamber|hiring|expan|small business|boutique"),
    ("hoods", r"neighborhood|block party|residents|ocie hill|north end|woodland|springmill|"
              r"madison|lexington|ontario|bellville|shelby|butler|lucas|plymouth|shiloh|"
              r"crestline|mifflin|clear fork|lexington|street"),
]
# Obituaries and memorials stay off the site.
LIFE_HINTS = (r"obituar|celebration of life|in loving memory|in memory of|life of|passed away|memorial service|funeral|"
              r"\(\d{4} ?[-\u2013] ?\d{4}\)")


def rx(pattern):
    return re.compile(pattern.replace("\n", ""), re.I)


# Elections and politics stay off the site.
VOTING = r"\bvot(e|ed|er|ers|ing)\b|ballot|election|polls?\b|candidate|levy|board of elections|campaign"

# Stories from wide searches (Google News, Bing) must name a place here to count as local.
LOCAL = r"""mansfield|richland|madison|lexington|ontario|bellville|shelby|butler|lucas|plymouth|shiloh|
crestline|mifflin|clear fork|crestview|mohican|malabar|kingwood|ashland|galion|loudonville"""

# Other towns named Mansfield (Texas, Massachusetts, Connecticut, Louisiana...) and their schools and papers.
OTHER_MANSFIELD = r"""mansfield,? (texas|tx|mass\w*|ma\b|conn\w*|ct\b|louisiana|la\b|pa\b|penn\w*|ga\b|georgia|mo\b|
missouri|ark\w*|nottinghamshire|england|uk\b)|timberview|mansfield (legacy|lake ridge|summit|isd)|southlake|
fort worth|arlington|dallas|frisco|keller|attleboro|foxboro|norton|sun chronicle|star-telegram|uconn|storrs|
desoto parish|nottingham"""

# Obituary wording, and the feed tags, authors and web addresses obituaries come with.
OBIT_TEXT = rx(r"obituar|passed away|funeral|in loving memory|survived by|preceded (him |her )?in death|calling hours|"
               r"entered (into )?(eternal )?rest|laid to rest|went (home )?to be with the lord|passed peacefully|"
               r"\bdied\b|\bR\.?I\.?P\b|rest in peace|age (of )?\d{2,3}\b.{0,40}\b(died|passed)")
OBIT_TAG = rx(r"obituar|\bobits?\b|death notice|funeral|cremation|mortuary|legacy\.com|tributearchive")
NEG, POS, LIFE, VOTE = rx(NEGATIVE), rx(POSITIVE), rx(LIFE_HINTS), rx(VOTING)
NEAR, AWAY = rx(LOCAL), rx(OTHER_MANSFIELD)
RULES = [(key, rx(p)) for key, p in SECTION_RULES]


def classify(story, forced=None):
    """Return a section key, or None if the story should be skipped."""
    text = f"{story['title']} {story['summary']}"
    if (forced == "life" or LIFE.search(story["title"]) or OBIT_TEXT.search(text)
            or OBIT_TAG.search(" | ".join(story.get("tags") or [])) or OBIT_TAG.search(story.get("link") or "")):
        return None
    if NEG.search(text) or VOTE.search(text):
        return None
    if AWAY.search(f"{text} {story.get('source', '')}") or (not story.get("local") and not NEAR.search(text)):
        return None  # another Mansfield, or a wide-search story that never names a local place
    for key, pattern in RULES:
        if pattern.search(text):
            # Garage sales are useful even when they aren't "uplifting".
            if key == "sales" or POS.search(text):
                return key
    return "good" if POS.search(text) else None


def clean(text, limit=220):
    # Strip tags before unescaping, and respect quoted attributes: WordPress puts escaped HTML inside
    # attributes like data-image-caption, which otherwise leaks "data-large-file=..." into the summary.
    tag = r"""<(?:[^>"']|"[^"]*"|'[^']*')*>"""
    text = re.sub(tag, " ", html.unescape(re.sub(tag, " ", text or "")))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"
    return text


def paragraphs(text):
    # The full submitted text for the story page, split on line breaks into paragraphs.
    parts = [clean(p, 10**6) for p in re.split(r"\r?\n\s*\n|\r?\n", text or "")]
    return [p for p in parts if p]


def parse_date(value):
    try:
        dt = email.utils.parsedate_to_datetime(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except (AttributeError, ValueError):
            return datetime.now(timezone.utc)


VIDEO = re.compile(r"\.(mp4|m4v|mov|webm|m3u8|avi|mp3|wav|pdf)(\?|$)", re.I)


def is_image(url, kind=None, medium=None):
    """True unless the address or its declared type says it's a video, audio or document."""
    kind, medium = (kind or "").lower(), (medium or "").lower()
    if kind and not kind.startswith("image"):
        return False
    return medium in ("", "image") and not VIDEO.search(url.strip())


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
        source = get("source", "Source")
        # Feed tags and author: Richland Source files obituaries under the "Obituaries" category, written by a funeral home.
        tags = [c.text.strip() for c in item if c.tag.split("}")[-1] in ("category", "creator", "author") and (c.text or "").strip()]
        # Feeds can list videos alongside photos (WordPress puts an .mp4 first); take the first real image.
        media = [c for c in item for n in ("content", "thumbnail", "enclosure", "Image") if c.tag.split("}")[-1] == n]
        image = next((u for c in media if (u := c.get("url") or c.text) and is_image(u, c.get("type"), c.get("medium"))), None)
        if image and image.startswith("http://www.bing.com/"):
            image = "https://" + image[len("http://"):]
        if not image and desc is not None and desc.text:
            found = re.search(r'<img[^>]+src="([^"]+)"', desc.text)
            image = found.group(1) if found else None
        if title is None or not href:
            continue
        # Bing News wraps the article in a click-tracking link; keep the real address.
        if "bing.com/news/apiclick" in href:
            href = urllib.parse.parse_qs(urllib.parse.urlparse(href.strip()).query).get("url", [href])[0]
        name = feed["name"]
        title_text = title.text or ""
        # Google News titles end with " - Publisher"; use that as the source.
        if source is not None and source.text:
            name = source.text
            title_text = re.sub(r"\s+-\s+" + re.escape(source.text) + r"$", "", title_text)
        summary = clean(desc.text if desc is not None else "")
        if re.sub(r"\W+", "", summary.lower()).startswith(re.sub(r"\W+", "", clean(title_text, 200).lower())[:50]):
            summary = ""  # Google News repeats the headline here
        items.append({
            "title": clean(title_text, 200),
            "summary": summary,
            "link": href.strip(),
            "source": name,
            "image": image if feed.get("photos", True) and image and image.startswith("https://") else None,
            "photos": feed.get("photos", True),
            "date": parse_date(date.text if date is not None else None),
            "forced": feed.get("section"),
            "local": feed.get("local", False),
            "tags": tags,
        })
    return items


def load_sample():
    now = datetime.now(timezone.utc)
    rows = json.loads((HERE / "sample_stories.json").read_text())
    return [{**r, "date": now - timedelta(hours=r["hours_ago"]), "forced": r.get("section"), "local": True} for r in rows]


def collect(config, sample):
    raw = load_sample() if sample else []
    if not sample:
        for feed in config["feeds"]:
            try:
                got = fetch_feed(feed)
                print(f"  {feed['name']}: {len(got)} items, {sum(1 for g in got if g['image'])} with photos", file=sys.stderr)
                raw += got
            except Exception as exc:  # one bad feed shouldn't stop the build
                note(f"{feed['name']}: skipped ({exc})")
    cutoff = datetime.now(timezone.utc) - timedelta(days=config.get("max_age_days", 21))
    # Outlets we never repost, matched on the outlet name or its web address (config.json "skip_sources").
    skip = [x.lower() for x in config.get("skip_sources", [])]
    skipped = lambda s: any(x in s["source"].lower() or x in s["link"].lower() for x in skip)
    seen, stories = set(), []
    for s in sorted(raw, key=lambda s: s["date"], reverse=True):
        key = re.sub(r"\W+", "", s["title"].lower())[:60]
        if key in seen or s["date"] < cutoff or skipped(s):
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
        stories = [s for s in stories if not skipped(s)]  # Google News links only show the real address once decoded
        if config.get("require_photos", True):
            # Some sites block photo lookups, so a brand-new story can arrive with no photo.
            # Keep the last two days' stories anyway with their section's line drawing, so today's news always shows.
            fresh = datetime.now(timezone.utc) - timedelta(days=2)
            dropped = [s for s in stories if not s.get("image") and s["date"] < fresh]
            stories = [s for s in stories if s.get("image") or s["date"] >= fresh]
            note(f"Left out {len(dropped)} older stories with no photo")
    return stories


# ---- Daily series: Tyger Paws (shelter dogs and their happy endings) ----
SERIES = [  # key, page, name, script word, what the page is about
    ("pets", "pets.html", "Tyger Paws", "Tyger Paws!",
     "Adoptable dogs at the Richland County Dog Shelter, a new dog of the day every morning, and the happy endings that follow."),
]
SERIES_NAMES = {k: name for k, _, name, _, _ in SERIES}
SERIES_RX = {
    "pets": rx(r"\b(dog shelter|animal shelter|dog warden|humane society|happy tails?|forever homes?|fur-?ever homes?|for adoption|adoption (events?|fees?|days?|specials?)|"
               r"(adopt\w*|rescue[ds]?|foster\w*|shelter) (a |an |the |your |their |our )?(dogs?|cats?|pets?|pupp(y|ies)|kittens?|animals?)|"
               r"(dogs?|cats?|pets?|pupp(y|ies)|kittens?|animals?) (adoption|rescue|shelter)s?)\b"),
}


def series_of(s):
    text = f"{s['title']} {s['summary']}"
    keys = [k for k in SERIES_RX if SERIES_RX[k].search(text)]
    if s.get("series") in SERIES_NAMES and s.get("trusted") and s["series"] not in keys:
        keys.append(s["series"])  # a series' own trusted source counts whatever its words
    return keys


def series_ok(s):
    """The same skip rules as the front page (no crime, crashes, obituaries, politics, other Mansfields), without
    needing an upbeat word: a race schedule or a restoration update is still good news for its page."""
    text = f"{s['title']} {s['summary']}"
    if LIFE.search(s["title"]) or OBIT_TEXT.search(text) or OBIT_TAG.search(" | ".join(s.get("tags") or [])):
        return False
    if NEG.search(text) or VOTE.search(text) or AWAY.search(f"{text} {s.get('source', '')}"):
        return False
    return bool(s.get("local") or NEAR.search(text))


def collect_series(cfg, stories, sample):
    """Stories for the three series pages: front-page stories that fit, plus the series' own feeds, kept longer
    (series_max_age_days) because these topics move slower than the front page."""
    raw = []
    if not sample:
        for feed in cfg.get("series_feeds", []):
            try:
                got = fetch_feed(feed)
                for g in got:
                    g["series"], g["trusted"] = feed.get("series"), feed.get("trusted", False)
                raw += got
                print(f"  {feed['name']}: {len(got)} items", file=sys.stderr)
            except Exception as exc:
                note(f"{feed['name']}: skipped ({exc})")
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.get("series_max_age_days", 60))
    skip = [x.lower() for x in cfg.get("skip_sources", [])]
    seen = {re.sub(r"\W+", "", s["title"].lower())[:60] for s in stories}
    extra = []
    for s in sorted(raw, key=lambda s: s["date"], reverse=True):
        key = re.sub(r"\W+", "", s["title"].lower())[:60]
        if key in seen or s["date"] < cutoff or any(x in s["source"].lower() or x in s["link"].lower() for x in skip):
            continue
        if not series_ok(s) or not series_of(s):
            continue
        seen.add(key)
        s["section"] = next((k for k, p in RULES if p.search(f"{s['title']} {s['summary']}")), "good")
        s["id"] = hashlib.sha1((s["link"] + s["title"]).encode()).hexdigest()[:10]
        extra.append(s)
    out = {k: [] for k in SERIES_NAMES}
    for s in sorted([s for s in stories if s["section"] != "life"] + extra, key=lambda s: s["date"], reverse=True):
        for k in series_of(s):
            out[k].append(s)
    note("Series: " + ", ".join(f"{SERIES_NAMES[k]} {len(v)}" for k, v in out.items()))
    return out, extra


SAMPLE_DOGS = [
    {"id": "1", "name": "Biscuit", "breed": "Labrador Retriever Mix", "age": "Young", "sex": "Female", "image": "", "url": "https://www.adoptapet.com/"},
    {"id": "2", "name": "Moose", "breed": "Pit Bull Terrier Mix", "age": "Adult", "sex": "Male", "image": "", "url": "https://www.adoptapet.com/"},
    {"id": "3", "name": "Pepper", "breed": "Beagle Mix", "age": "Senior", "sex": "Female", "image": "", "url": "https://www.adoptapet.com/"},
]


def fetch_shelter_dogs(cfg, sample):
    """Adoptable dogs from the shelter's Adopt-a-Pet list (the same list the county's Adoptable Dogs page shows)."""
    shelter = cfg.get("shelter") or {}
    if sample:
        return SAMPLE_DOGS
    if not shelter.get("adoptapet_id"):
        return []
    url = ("https://searchtools.adoptapet.com/cgi-bin/searchtools.cgi/portable_pet_list?"
           + urllib.parse.urlencode({"shelter_id": shelter["adoptapet_id"], "clan_name": "dog", "sort_by": "pet_name"}))
    try:
        _, page = fetch(url, limit=3_000_000)
    except Exception as exc:
        note(f"Shelter dogs: skipped ({exc})")
        return []
    dogs, seen = [], set()
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page, re.S | re.I):  # every other row has no class
        link = re.search(r'href="(?:https://[^"/]+)?(/pet/(\d+)-[\w-]+)"', row)
        name = re.search(r'class="name[^"]*"[^>]*>\s*<p>\s*<a[^>]*>(.*?)</a>', row, re.S)
        if not (link and name) or link.group(2) in seen:
            continue
        seen.add(link.group(2))
        cells = [clean(c, 80) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        breed = re.search(r'class="[^"]*breed[^"]*"[^>]*>(.*?)</td>', row, re.S)
        img = re.search(r'<img[^>]+src="(https://pet-uploads\.adoptapet\.com/[^"]+)"', row)
        if not img:
            continue  # no photo yet: leave the dog off the site and Facebook until the shelter adds one
        dogs.append({"id": link.group(2), "name": clean(name.group(1), 60), "url": "https://www.adoptapet.com" + link.group(1),
                     "breed": clean(breed.group(1), 60) if breed else "", "age": cells[-2] if len(cells) >= 5 else "",
                     "sex": cells[-1] if len(cells) >= 5 else "", "image": img.group(1) if img else "",
                     "big": big_dog_photo(img.group(1)) if img else ""})
    note(f"Shelter dogs: {len(dogs)} adoptable with photos")
    return dogs


def big_dog_photo(thumb, height=1000):
    """The list only has 136-pixel thumbnails. Adopt-a-Pet stores each upload's full-size photo five numbers
    before its thumbnail; callers fall back to the thumbnail if that ever stops being true."""
    m = re.search(r"/(\d{6,})\.jpg$", thumb or "")
    return f"https://media.adoptapet.com/image/upload/c_fit,h_{height}/f_jpg,q_auto/{int(m.group(1)) - 5}" if m else ""


def dog_of_the_day(dogs, day=None):
    """One dog a day, the same on the site and on Facebook. Goes through every dog before repeating."""
    if not dogs:
        return None
    day = day or eastern().date()
    ordered = sorted(dogs, key=lambda d: int(d["id"]) if str(d["id"]).isdigit() else 0)
    return ordered[day.toordinal() % len(ordered)]


UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")
STATS = {}


def note(msg):
    """Log a line; on GitHub Actions it also shows as a notice on the run."""
    print(("::notice::" if os.environ.get("GITHUB_ACTIONS") else "  ") + msg, file=sys.stderr)


def fetch(url, data=None, limit=400_000, headers=None):
    req = urllib.request.Request(url, data=data, headers={"User-Agent": UA, "Accept-Language": "en-US,en", **(headers or {})})
    if data:
        req.add_header("Content-Type", "application/x-www-form-urlencoded;charset=UTF-8")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return resp.geturl(), resp.read(limit).decode("utf-8", "ignore")


def google_news_url(link):
    """Google News links hide the real article; ask Google for it, like its own redirect page does."""
    gid = urllib.parse.urlparse(link).path.rstrip("/").split("/")[-1]
    _, page = fetch(f"https://news.google.com/rss/articles/{gid}", limit=5_000_000)
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


def bing_photo(s):
    """Some sites block robots; Bing News often has a thumbnail for the same headline."""
    try:
        q = urllib.parse.quote_plus(f'"{s["title"][:90]}"')
        _, feed = fetch(f"https://www.bing.com/news/search?q={q}&format=rss")
        norm = lambda x: re.sub(r"\W+", "", html.unescape(x).lower())
        want = norm(s["title"])[:40]
        for item in re.findall(r"<item>(.*?)</item>", feed, re.S):
            title = re.search(r"<title>(.*?)</title>", item, re.S)
            found = re.search(r"<News:Image>([^<]+)</News:Image>", item)
            if title and found and want in norm(title.group(1)):
                break
        else:
            return False  # only use a photo from the very same story
        url = html.unescape(found.group(1)).replace("http://", "https://", 1)
        s["image"] = re.sub(r"&pid=News.*", "", url) + "&w=800&h=450&c=14&pid=News"
        return True
    except Exception:
        return False


# Columns of the "Publish" tab in the submissions sheet (see FORMS.md). Emails never reach this tab.
SUBMIT_COLS = ["when", "section", "title", "summary", "photo", "link", "name", "role", "business", "town",
               "event_date", "price", "contact"]
SECTION_BY_LABEL = {**{n.lower(): k for k, n in SECTIONS}, "cool research": "good",  # section retired; old posts go to Good News
                    "business spotlight": "spotlight",
                    "event flyer": "flyer", "free stuff": "market", "buy & sell": "market", "buy and sell": "market",
                    "job": "job", "jobs": "job", "home for rent or sale": "home", "home": "home", "homes": "home",
                    "athlete spotlight": "athlete", "bracket": "bracket", "fundraiser": "fund",
                    "headline": "headline", "link": "link", "birthday": "birthday"}
FUND_DAYS = 90  # fundraisers without an end date come down after this
ATHLETE_FACTS = ("Sport", "School", "Class of")  # folded into the summary by the submit form
# Free Stuff categories: (key, name, words that suggest it when a listing has no category).
MARKET_CATS = [
    ("furniture", "Furniture", r"table|chair|couch|sofa|dresser|desk|bed\b|mattress|cabinet|shelf|recliner"),
    ("household", "Household & appliances", r"washer|dryer|fridge|refrigerator|stove|microwave|vacuum|dishes|lamp|rug|decor"),
    ("electronics", "Electronics", r"\btv\b|television|laptop|computer|phone|ipad|tablet|console|xbox|playstation|nintendo|speaker|camera"),
    ("kids", "Kids & baby", r"kid|baby|toddler|stroller|crib|car seat|toy"),
    ("clothing", "Clothing & shoes", r"cloth|shirt|dress|coat|jacket|shoe|boots|jeans"),
    ("tools", "Tools & yard", r"tool|drill|saw|mower|blower|trimmer|ladder|generator|compressor|snow ?blower"),
    ("vehicles", "Cars & trucks", r"\bcar\b|truck|suv|van\b|tires?|vehicle|motorcycle|atv|trailer"),
    ("bikes", "Bikes & outdoors", r"bike|bicycle|kayak|canoe|camping|tent|grill|fishing|hunting"),
    ("sports", "Sports & hobbies", r"golf|ball|sports|instrument|guitar|piano|craft|game|collect"),
    ("farm", "Farm & garden", r"farm|garden|hay|chicken|plant|seed|tractor|livestock"),
    ("pets", "Pet supplies", r"\bpet|dog|cat\b|kennel|aquarium|leash"),
    ("other", "Everything else", r""),
]
MARKET_CAT_NAMES = {k: n for k, n, _ in MARKET_CATS}
MARKET_CAT_BY_NAME = {n.lower(): k for k, n, _ in MARKET_CATS}


def market_category(title, summary, given=""):
    if given.lower() in MARKET_CAT_BY_NAME:
        return MARKET_CAT_BY_NAME[given.lower()]
    text = f"{title} {summary}".lower()
    for key, _, words in MARKET_CATS:
        if words and re.search(rf"\b(?:{words})", text):
            return key
    return "other"


LISTING_DAYS = 30  # flyers without a date and Free Stuff listings come down after this
JOB_DAYS = 30
JOB_TYPES = ["Full-time", "Part-time", "Seasonal", "Temporary", "Internship"]
# Free Stuff is free only: a price, a trade or a payment app means it isn't (old Buy & Sell posts included).
PAID = re.compile(r"\$\s?\d|\b(obo|or best offer|for sale|asking|venmo|cash ?app|zelle|paypal|wanted|wtb|iso)\b", re.I)
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def scrub(text):
    """Take phone numbers and email addresses out of Free Stuff, job and home posts. People reply through the site instead."""
    return EMAIL.sub("[removed]", PHONE.sub("[removed]", text or ""))


def parse_day(text):
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text.strip(), fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return None


BIRTHDAY_TITLE = re.compile(r"^happy birthday,?\s*(.+?)!?$", re.I | re.S)


def eastern(dt=None):
    """A time in Mansfield (Eastern), now if none is given."""
    from zoneinfo import ZoneInfo
    return (dt or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))


def birthday_on(month, day, year):
    """The day a birthday falls on in a year. February 29 birthdays get February 28 in other years."""
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return datetime(year, 2, 28).date()


BIRTHDAY_DAYS = 2  # a shout-out stays on the site for its birthday and the day after


def birthday_day(birthday, sent):
    """A shout-out runs once: on the first birthday on or after the day it was sent."""
    first = birthday_on(birthday.month, birthday.day, sent.year)
    if first < sent:
        first = birthday_on(birthday.month, birthday.day, sent.year + 1)
    return first


def drive_image(url):
    """Turn a shared Google Drive photo link into a direct image address."""
    found = re.search(r"drive\.google\.com/(?:file/d/|open\?id=|uc\?id=)([\w-]+)", url or "")
    return f"https://lh3.googleusercontent.com/d/{found.group(1)}" if found else (url or "").strip()


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "business"


def backend_on(config):
    return bool(config.get("supabase_url") and config.get("supabase_anon_key"))


def fetch_published(config):
    """Approved posts from the Supabase "published" view (no email addresses in it)."""
    key = config["supabase_anon_key"]
    url = config["supabase_url"].rstrip("/") + "/rest/v1/published?select=*&order=created_at.desc&limit=1000"
    _, text = fetch(url, limit=5_000_000, headers={"apikey": key, "Authorization": f"Bearer {key}"})
    rows = json.loads(text)
    # Birthday shout-outs can be sent months ahead, so fetch them on their own (the view only lists today's).
    _, bdays = fetch(url.split("&order=")[0] + "&section=eq.Birthday", limit=1_000_000,
                     headers={"apikey": key, "Authorization": f"Bearer {key}"})
    seen = {r.get("id") for r in rows}
    rows += [r for r in json.loads(bdays) if r.get("id") not in seen]
    rename = {"created_at": "when", "photo_url": "photo"}
    return [{rename.get(k, k): ("" if v is None else str(v)) for k, v in r.items()} for r in rows]


def load_submissions(config, sample):
    """Approved posts: from Supabase on the live site, from sample_submissions.csv in the preview."""
    got = {"stories": [], "spotlights": [], "flyers": [], "listings": [], "jobs": [], "brackets": [], "fundraisers": [],
           "headlines": [], "links": [], "birthdays": [], "homes": []}
    try:
        if sample:
            rows = list(csv.reader(io.StringIO((HERE / "sample_submissions.csv").read_text())))
        elif backend_on(config):
            rows = fetch_published(config)
        else:
            return got
    except Exception as exc:
        note(f"Submissions: skipped ({exc})")
        return got
    now = datetime.now(timezone.utc)
    for row in rows:
        if isinstance(row, list):
            row = dict(zip(SUBMIT_COLS, [c.strip() for c in row] + [""] * len(SUBMIT_COLS)))
        section = SECTION_BY_LABEL.get(row["section"].lower())
        if not section or not row["title"]:
            continue  # header row, blank row or unknown section
        try:
            date = datetime.strptime(row["when"], "%m/%d/%Y %H:%M:%S").replace(tzinfo=timezone.utc)
        except ValueError:
            date = parse_date(row["when"])
        row["link"] = full_link(row["link"])  # only http(s), so a typed javascript: or data: link never becomes an href
        image = drive_image(row["photo"])
        image = image if image.startswith("https://") or (sample and image.startswith("assets/")) else None
        sid = hashlib.sha1(("community" + row.get("id", "") + row["when"] + row["title"]).encode()).hexdigest()[:10]
        if section == "flyer":
            day = parse_day(row["event_date"])
            if (day and day + timedelta(days=1) < now) or (not day and date + timedelta(days=LISTING_DAYS) < now):
                continue  # the event is over
            got["flyers"].append({"id": sid, "title": row["title"], "summary": row["summary"], "image": image,
                                  "link": row["link"], "day": day, "town": row["town"], "date": date})
            continue
        if section == "market":
            if date + timedelta(days=LISTING_DAYS) < now:
                continue
            given, keep = "", []
            for line in row["summary"].splitlines():
                k, _, v = line.partition(": ")
                if k == "Category" and v.strip():
                    given = v.strip()
                else:
                    keep.append(line)
            text = " ".join(keep).strip()
            if PAID.search(f'{row["title"]} {text}') or row["price"].strip().lower() not in ("", "free", "$0"):
                continue  # not free, so not for this page
            got["listings"].append({"id": sid, "pid": row.get("id") or (sid if sample else ""), "title": scrub(row["title"]), "summary": scrub(text),
                                    "image": image, "town": row["town"], "date": date,
                                    "cat": market_category(row["title"], text, given)})
            continue
        if section == "job":
            if date + timedelta(days=JOB_DAYS) < now:
                continue
            kind, keep = "", []
            for line in row["summary"].splitlines():
                k, _, v = line.partition(": ")
                if k == "Type" and v.strip():
                    kind = v.strip()
                else:
                    keep.append(line)
            got["jobs"].append({"id": sid, "pid": row.get("id") or (sid if sample else ""), "title": scrub(row["title"]),
                                "summary": scrub("\n".join(keep).strip()), "employer": row["business"].strip() or row["name"],
                                "type": kind, "pay": scrub(row["price"]), "town": row["town"], "link": row["link"],
                                "image": image, "date": date, "until": date + timedelta(days=JOB_DAYS)})
            continue
        if section == "home":
            if date + timedelta(days=HOME_DAYS) < now:
                continue
            facts, keep = {}, []
            for line in row["summary"].splitlines():
                k, _, v = line.partition(": ")
                if k in ("Listing", "Beds", "Baths") and v.strip():
                    facts[k] = v.strip()
                else:
                    keep.append(line)
            kind = facts.get("Listing") if facts.get("Listing") in HOME_KINDS else ""
            got["homes"].append({"id": sid, "pid": row.get("id") or (sid if sample else ""), "title": scrub(row["title"]),
                                 "summary": scrub("\n".join(keep).strip()), "kind": kind, "price": scrub(row["price"]),
                                 "amount": home_price(row["price"]), "beds": facts.get("Beds", ""), "baths": facts.get("Baths", ""),
                                 "town": row["town"], "link": row["link"], "image": image, "by": row["business"].strip() or row["name"],
                                 "date": date})
            continue
        if section == "spotlight":
            name = row["business"] or row["title"]
            got["spotlights"].append({"name": name, "what": row["summary"], "neighborhood": row["town"],
                               "link": row["link"] or "", "image": image, "by": row["name"], "slug": slug(name),
                                      "date": date, "community": True})
            continue
        if section == "fund":
            day = parse_day(row["event_date"])
            if (day and day + timedelta(days=1) < now) or (not day and date + timedelta(days=FUND_DAYS) < now):
                continue
            got["fundraisers"].append({"id": sid, "title": row["title"], "summary": row["summary"], "image": image,
                                       "link": row["link"], "goal": row["price"], "cause": row["business"].strip(),
                                       "by": row["name"], "town": row["town"], "day": day, "date": date})
            continue
        if section == "birthday":  # shows on the person's birthday only, once an editor approves it
            today = eastern().date()
            tag = row["event_date"].strip().lower()
            if sample and tag in ("today", "yesterday"):  # previews only
                day = on = today - timedelta(days=tag == "yesterday")
            else:
                day = parse_day(row["event_date"])
                on = birthday_day(day, eastern(date).date()) if day else None
            if not on or not timedelta(0) <= today - on < timedelta(days=BIRTHDAY_DAYS):
                continue
            who = BIRTHDAY_TITLE.match(row["title"].strip())
            got["birthdays"].append({"id": sid, "name": clean(who.group(1) if who else row["title"], 80),
                                     "message": clean(row["summary"], 300), "by": clean(row["name"], 80), "date": date,
                                     "today": on == today})
            continue
        if section == "link":  # an editor pasted an article link; it becomes a regular news story
            got["links"].append({"link": row["link"], "title": row["title"], "summary": row["summary"],
                                 "section": row["business"], "date": date})
            continue
        if section == "headline":  # an editor pinned this story to the top of the front page
            got["headlines"].append({"title": row["title"], "link": row["link"], "date": date})
            continue
        if section == "bracket":
            if all(b["title"] != row["title"] for b in got["brackets"]):  # newest version of each bracket wins
                got["brackets"].append({"id": sid, "title": row["title"], "text": row["summary"], "link": row["link"],
                                        "date": date})
            continue
        facts = {}
        if section == "athlete":
            keep = []
            for line in row["summary"].splitlines():
                k, _, v = line.partition(": ")
                if k in ATHLETE_FACTS and v.strip():
                    facts[k] = v.strip()
                else:
                    keep.append(line)
            row["summary"], section = " ".join(keep), "sports"
        byline = row["name"] or "A neighbor"
        link = full_link(row["link"])
        video = youtube_id(link)
        if video:  # play it right on the story page instead of sending readers off to YouTube
            image = image or f"https://img.youtube.com/vi/{video}/hqdefault.jpg"
            link = ""
        got["stories"].append({"athlete": facts if facts or row["section"].lower() == "athlete spotlight" else None,"id": sid, "title": clean(row["title"], 200), "summary": clean(row["summary"], 400),
                        "body": paragraphs(row["summary"]), "link": link, "video": video, "source": byline,
                        "role": row["role"], "image": image, "date": date, "section": section, "community": True,
                        "town": row["town"]})
    got["flyers"].sort(key=lambda f: f["day"] or f["date"] + timedelta(days=LISTING_DAYS))
    got["listings"].sort(key=lambda x: x["date"], reverse=True)
    got["birthdays"].sort(key=lambda b: (not b["today"], b["date"]))  # today's first, then yesterday's
    note("Community: " + ", ".join(f"{len(v)} {k}" for k, v in got.items()))
    return got


def full_link(link):
    """Neighbors often type "youtube.com/..." or "www.site.com"; give those an https:// so they open."""
    link = (link or "").strip()
    if link and not link.startswith("http") and re.match(r"^[\w-]+(\.[\w-]+)+(/|$)", link):
        link = "https://" + link
    return link if re.match(r"(?i)^https?://", link) else ""


def youtube_id(link):
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:.*&)?v=|shorts/|embed/|live/)|youtu\.be/)([\w-]{11})", link or "")
    return m.group(1) if m else ""


def add_photos(stories, limit=80):
    """Fill in missing photos from each article's own share image (og:image)."""
    todo = [s for s in stories if not s.get("image") and s.get("photos", True)][:limit]
    fails = {}
    for s in todo:
        try:
            if "news.google.com" in s["link"]:
                time.sleep(0.5)  # be gentle with Google
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
            if image.startswith("https://") and is_image(image):
                s["image"] = image
        except Exception as exc:
            if bing_photo(s):
                STATS["bing"] = STATS.get("bing", 0) + 1
                continue
            key = f"{urllib.parse.urlparse(s['link']).netloc}: {type(exc).__name__} {str(exc)[:60]}"
            fails[key] = fails.get(key, 0) + 1
    have = sum(1 for s in stories if s.get("image"))
    note(f"Photos: {have} of {len(stories)} stories ({len(todo)} looked up, "
         f"{STATS.get('google_decoded', 0)} Google links decoded, {STATS.get('bing', 0)} photos from Bing)")
    if fails:  # one line, so the notice limit (10 per run) leaves room for the rest
        note("Photo lookups failed: " + "; ".join(f"{k.split(':')[0]} x{n}" for k, n in sorted(fails.items(), key=lambda kv: -kv[1])[:10]))


# ---------- rendering ----------

e = lambda s: html.escape(str(s), quote=True)
FONTS = ("https://fonts.googleapis.com/css2?family=Yellowtail&family=Libre+Caslon+Text:wght@400;700"
         "&family=Libre+Franklin:wght@400;500;600;700;800&family=Josefin+Sans:wght@600;700&display=swap")
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
  background:transparent}
/* The collage sits on a fixed layer, which phones (iOS especially) handle better than background-attachment:fixed. */
html{background:var(--surface)}
html::before{content:"";position:fixed;inset:0;z-index:-1;pointer-events:none;
  background:linear-gradient(rgba(243,230,207,.74),rgba(243,230,207,.74)),url(/assets/collage.jpg) center/cover no-repeat}
body::before{content:"";position:fixed;inset:0 0 auto;height:env(safe-area-inset-top,0px);z-index:10;background:var(--tertiary)}
a{color:inherit}
[hidden]{display:none!important}
:focus-visible{outline:none;box-shadow:var(--ring)}
.sheet{max-width:1200px;margin:32px auto;background:rgba(251,246,236,.86);border:2px solid var(--border-strong)}
@media (max-width:1264px){.sheet{margin:24px}}
@media (max-width:600px){.sheet{margin:10px;background:rgba(251,246,236,.9)}}
.wrap{padding-left:max(clamp(16px,4vw,48px),env(safe-area-inset-left,0px));padding-right:max(clamp(16px,4vw,48px),env(safe-area-inset-right,0px))}
.caps,.overline{font:700 11px/1.4 var(--caps);letter-spacing:.12em;text-transform:uppercase}
.muted{color:var(--text-2)}

/* nameplate */
.plate{display:grid;grid-template-columns:1fr minmax(0,500px) 1fr;align-items:center;gap:24px;padding-block:32px 24px}
.plate .logo{display:block;line-height:0}
.plate .logo svg{width:100%;height:auto}
.ear{display:grid;gap:4px;align-content:center}
.ear.right{justify-items:end;text-align:right;gap:12px}
.motto{line-height:1.7}.motto.mobile{display:none}
@media (max-width:900px){.plate{grid-template-columns:minmax(0,1fr);justify-items:center;padding-block:24px 16px;gap:12px}.ear{display:none}.plate .logo{width:min(100%,420px)}
  .motto.mobile{display:block;text-align:center;color:var(--text-2)}}
.strip{display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:0 18px;padding-block:6px;
  border-top:2px solid var(--border-strong);border-bottom:2px solid var(--border-strong)}
.navgroup{display:flex;flex-wrap:wrap;justify-content:center;align-items:center;gap:4px 18px}
.navlabel{display:none}
@media (max-width:1240px){.strip{justify-content:center;gap:0 18px}.navgroup{justify-content:center}}
@media (max-width:820px){.navgroup{gap:2px 12px}.strip a,.navbtn{padding-block:6px}}
.navlabel{color:var(--text-3)}
.strip a,.navbtn{text-decoration:none;white-space:nowrap;padding-block:8px}
.navbtn{font:inherit;letter-spacing:inherit;text-transform:inherit;color:inherit;background:none;border:0;cursor:pointer;padding-inline:0}
.strip a:hover,.navbtn:hover{color:var(--secondary)}
.strip .navcta{background:var(--secondary);color:var(--tertiary);padding:8px 14px}
.strip .navcta:hover{background:var(--secondary-dk);color:var(--tertiary)}
@media (max-width:900px){.strip{justify-content:center}.navgroup{justify-content:center}.navlabel{display:none}}
/* the "why this paper" pop-out: first visit, and the About link */
.about{position:fixed;right:24px;bottom:24px;z-index:30;width:min(420px,calc(100vw - 32px));max-height:calc(100vh - 48px);overflow:auto;box-sizing:border-box;
  background:var(--tertiary);border:2px solid var(--border-strong);border-top:6px solid var(--secondary);padding:28px 28px 24px;display:grid;gap:10px}
.about .script{font-size:30px;color:var(--secondary);line-height:1}
.about .hed{font-size:28px;line-height:1.15;margin:0}
.about p{margin:0;font-size:15px;line-height:1.6}
.about ul{margin:0;padding-left:18px;font-size:14px;line-height:1.55;display:grid;gap:6px}
.about .close{position:absolute;top:8px;right:10px;font:400 28px/1 var(--sans);background:none;border:0;cursor:pointer;color:var(--text-2);padding:4px 8px}
@media (max-width:600px){.about{right:16px;left:16px;bottom:16px;width:auto}}

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
h1.hed,h2.hed,h3.hed{font-family:var(--serif);font-weight:700;margin:0;text-wrap:balance}
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
.readfull{display:flex;flex-wrap:wrap;align-items:center;gap:12px 16px;margin:20px 0 4px;font-size:15px;color:var(--text-2)}
.lead .dek.body p{margin:0 0 1em}.lead .dek.body p:last-child{margin-bottom:0}
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
.postimg{display:block;padding-block:4px 36px}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}
@media (max-width:960px){.grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:600px){.grid{grid-template-columns:minmax(0,1fr)}}
.card{display:grid;gap:12px;align-content:start;min-width:0;background:var(--tertiary);border:2px solid var(--border-subtle);padding:24px}
.card:hover{border-color:var(--border-strong)}
.card .photo{margin:-24px -24px 4px;max-width:none;width:calc(100% + 48px)}
.card .hed{font-size:24px;line-height:1.2;letter-spacing:-.01em}
.card .dek{font-size:14px;line-height:1.6;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.empty{grid-column:1/-1;margin:0;color:var(--text-2)}
.home .photo{position:relative}.home .price{position:absolute;left:12px;bottom:12px;background:var(--secondary);color:var(--tertiary);font:700 20px/1 var(--sans);padding:8px 12px}
#h-sort{font:inherit;padding:10px 12px;border:2px solid var(--border-strong);background:var(--tertiary);color:var(--text)}
.dogs.grid{grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:20px}
.dogs .dogcard .photo{aspect-ratio:1}.dogs .dogcard .hed{font-size:21px}
.dogcard .photo img{object-position:50% 25%}.give .listing.dogcard .photo{aspect-ratio:1}
.dogcard .goal-v{font-size:17px}.give .dogcard .goal-v{font-size:14px}
.give-note svg.paw{fill:var(--secondary);stroke:none}
/* the newsletter pop-up: opens once for returning readers, and from the footer link. Same look as the welcome email. */
.nlpop{padding:0;border:0;background:transparent;width:min(460px,calc(100vw - 32px));max-width:none;max-height:calc(100dvh - 32px);overflow:auto}
.nlpop::backdrop{background:rgba(46,26,16,.6)}
.nl2{position:relative;background:var(--tertiary);border:2px solid var(--border-strong);border-top:6px solid var(--secondary);text-align:center}
.nl2 .close{position:absolute;top:6px;right:8px;z-index:2;font:400 30px/1 var(--sans);background:none;border:0;cursor:pointer;color:var(--text-2);padding:4px 8px}
.nl-top{padding:30px 24px 0}
.nl-logo{display:block;width:min(250px,72%);margin:0 auto}.nl-logo svg{display:block;width:100%;height:auto}
.nl-top .script{display:block;font-size:26px;line-height:1.1;color:var(--secondary);margin-top:10px}
.nl-body{padding:16px clamp(18px,6vw,30px) 16px;display:grid;gap:14px}
.nl-hed{font:700 clamp(23px,6vw,28px)/1.15 var(--serif);margin:0;color:var(--text)}
.nl-hed:focus,.nl-hed:focus-visible{outline:none;box-shadow:none}
.nl-sub{font-size:16px;line-height:1.5;margin:0;color:var(--text-2)}
.nl-list{list-style:none;margin:0;padding:0;display:grid;gap:10px;text-align:left;justify-self:center}
.nl-list li{display:flex;gap:10px;align-items:center;font:700 13px/1.3 var(--caps);letter-spacing:.06em;text-transform:uppercase;color:var(--text)}
.nl-ico{flex:none;width:30px;height:30px;border-radius:50%;background:var(--secondary);color:var(--tertiary);display:grid;place-items:center;font-size:15px;line-height:1}
.nl-form{min-height:47px}.nl-form iframe{max-width:100%}
.nl-fine{font-size:13px;line-height:1.5;margin:0;color:var(--text-2)}
.nl-no{justify-self:center;padding:6px 8px;color:var(--text-2)}
.dogday{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.1fr);gap:32px;align-items:center;background:var(--tertiary);border:2px solid var(--border-strong);padding:24px}
.dogday .photo{aspect-ratio:1}.dogday .hed{font-size:clamp(30px,4vw,48px);line-height:1.05;margin:6px 0 12px}.dogday .dek{font-size:18px;line-height:1.6}
@media (max-width:700px){.dogday{grid-template-columns:minmax(0,1fr);gap:16px;padding:16px}}
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

/* carousels ("shelves"): swipe on phones, arrow buttons on desktop; the rail scrolls, never the page */
.railbox{padding-block:0 48px;min-width:0;scroll-margin-top:72px}
.rail-head{display:flex;align-items:end;gap:16px;border-bottom:2px solid var(--border-strong);margin-bottom:20px}
.rail-head .label{border:0;padding-bottom:10px;flex:1}
.rail-head .link{margin-bottom:10px;background:none;border-top:0;border-left:0;border-right:0;cursor:pointer;padding:0 0 2px}
.rail-nav{display:flex;gap:4px;margin-bottom:6px}
.arrow{width:36px;height:36px;display:grid;place-items:center;background:var(--tertiary);border:2px solid var(--border-strong);cursor:pointer;color:var(--primary)}
.arrow svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2.5}
.arrow:hover{background:var(--primary);color:var(--tertiary)}
.arrow:disabled{opacity:.35;cursor:default;background:var(--tertiary);color:var(--primary)}
@media (hover:none){.rail-nav{display:none}}
.rail{display:grid;grid-auto-flow:column;grid-auto-columns:calc((100% - 48px)/3);gap:24px;overflow-x:auto;overscroll-behavior-x:contain;
  scroll-snap-type:x mandatory;scrollbar-width:none;padding-bottom:4px}
.rail::-webkit-scrollbar{display:none}
.rail>*{scroll-snap-align:start;min-width:0}
@media (max-width:960px){.rail{grid-auto-columns:calc((100% - 24px)/2.15)}}
@media (max-width:600px){.rail{grid-auto-columns:82%;gap:16px}}
.flyers .rail{grid-auto-columns:calc((100% - 72px)/4)}
@media (max-width:960px){.flyers .rail{grid-auto-columns:calc((100% - 24px)/2.3)}}
@media (max-width:600px){.flyers .rail{grid-auto-columns:64%}}
.flyer{display:grid;gap:8px;align-content:start;text-decoration:none}
.flyer-img{display:block;aspect-ratio:4/5;background:var(--surface);border:2px solid var(--border-strong);overflow:hidden}
.flyer-img img{width:100%;height:100%;object-fit:cover;display:block}
.flyer .hed{font-size:18px;line-height:1.25}
.flyer .dek{font-size:14px;line-height:1.5;margin:0;display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.flyer .hed a{text-decoration:none}.flyer .hed a:hover{color:var(--secondary)}
.flyer .more-link{font-size:11px;color:var(--secondary);text-decoration:none}.flyer .more-link:hover{text-decoration:underline}
.flyer.add .flyer-img{display:grid;place-items:center;border-style:dashed;border-color:var(--border-medium)}
.flyer.add .plus{font:700 64px/1 var(--serif);color:var(--secondary)}
.flyer.add:hover .flyer-img{border-color:var(--secondary)}
.listing{display:grid;gap:10px;align-content:start;background:var(--tertiary);border:2px solid var(--border-subtle);padding:0 0 20px}
.listing>:not(.photo){padding-inline:20px}
.listing .photo{aspect-ratio:4/3}
.listing .hed{font-size:20px;line-height:1.25}
.listing .dek{font-size:14px;line-height:1.55;margin:0}
.price{position:absolute;left:0;bottom:0;background:var(--secondary);color:var(--tertiary);font:700 16px/1 var(--sans);padding:8px 12px}
.ad{display:grid;gap:10px;align-content:start;text-decoration:none;background:var(--tertiary);border:2px solid var(--border-subtle);padding:0 0 20px}
.ad>:not(.photo){padding-inline:20px}
.ad .hed{font:700 20px/1.25 var(--serif)}
.ad .sponsor{color:var(--text-3);padding-top:16px}
.ad.house{background:var(--surface);border:2px dashed var(--secondary);padding:20px 0;align-content:center;text-align:center;justify-items:center}
.ad.house .script{font-size:34px;color:var(--secondary)}
.ad.house .sponsor{padding-top:0}
.ad:hover{border-color:var(--border-strong)}
.market-grid .listing{border-color:var(--border-subtle)}
.market{display:grid;grid-template-columns:240px minmax(0,1fr);gap:40px;padding-block:0 64px;align-items:start}
@media (max-width:800px){.market{grid-template-columns:minmax(0,1fr);gap:20px}}
.catside{display:grid;gap:14px;position:sticky;top:16px}
@media (max-width:800px){.catside{position:static}.catgroup ul{columns:2;column-gap:16px}}
.catside .btn{justify-self:stretch;text-align:center}
.catgroup h3{margin:6px 0 6px;font-size:11px;letter-spacing:.14em;color:var(--text-2);border-bottom:2px solid var(--border-strong);padding-bottom:6px}
.catgroup ul{list-style:none;margin:0;padding:0}
.catside a[data-cat]{display:flex;justify-content:space-between;gap:8px;padding:5px 0;text-decoration:none;font-size:15px;break-inside:avoid}
.catside a[data-cat]:hover{color:var(--secondary)}
.catside a[aria-current="true"]{color:var(--secondary);font-weight:700}
.catside .n{color:var(--text-3);font-size:13px}
.allcat{font-weight:600}
.mtools{display:flex;gap:12px;align-items:center;margin-bottom:16px}
.mtools input{flex:1}
.viewtog{display:flex}
.mlist{display:grid;gap:0;border-top:2px solid var(--border-strong)}
.mlist .listing{grid-template-columns:120px minmax(0,1fr);grid-template-rows:auto auto 1fr auto;column-gap:16px;row-gap:4px;
  padding:14px 0;background:none;border:0;border-bottom:1px solid var(--border-subtle)}
.mlist .listing>:not(.photo){padding-inline:0;grid-column:2}
.mlist .listing .photo{grid-row:1/span 4;aspect-ratio:1;align-self:start}
.mlist .listing .price{font-size:13px;padding:5px 7px}
.mlist .listing .hed{font-size:18px}
.mlist .listing .dek{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
@media (max-width:600px){.mlist .listing{grid-template-columns:88px minmax(0,1fr)}}
.mlist.grid{grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:20px;border-top:0}
.mlist.grid .listing{display:grid;grid-template-columns:minmax(0,1fr);grid-template-rows:none;background:var(--tertiary);border:2px solid var(--border-subtle);padding:0 0 16px}
.mlist.grid .listing>:not(.photo){grid-column:1;padding-inline:16px}
.mlist.grid .listing .photo{grid-row:auto;aspect-ratio:4/3}
.madwrap{max-width:360px;margin:24px 0}
.freehero{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:24px 48px;align-items:center;padding-block:48px 40px}
.freehero .fcopy{display:grid;gap:4px;justify-items:start}
.freehero .retro{margin:0}
.freehero .flead{font:700 clamp(22px,2.6vw,30px)/1.25 var(--serif);margin:12px 0 0}
.freehero .intro{font-size:17px;line-height:1.65;color:var(--text-2);margin:8px 0 12px;max-width:56ch}
.ftiles{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}
.ftile{position:relative;display:grid;justify-items:center;gap:6px;padding:22px 12px 16px;background:var(--tertiary);border:2px solid var(--primary);box-shadow:5px 5px 0 var(--secondary)}
.ftile svg{width:64%;max-width:110px;stroke:var(--primary);fill:none;stroke-width:3;stroke-linecap:round;stroke-linejoin:round}
.ftag{position:absolute;top:-2px;right:-2px;background:var(--secondary);color:var(--tertiary);font:700 13px/1 var(--sans);letter-spacing:.08em;text-transform:uppercase;padding:6px 9px}
.flabel{font:600 13px/1 var(--sans);letter-spacing:.12em;text-transform:uppercase}
.fsteps{grid-column:1/-1;list-style:none;margin:16px 0 0;padding:20px 0 0;border-top:2px solid var(--primary);display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:20px}
.fsteps li{display:grid;grid-template-columns:52px minmax(0,1fr);gap:14px;align-items:start;font-size:15px;line-height:1.5;color:var(--text-2)}
.fsteps b{display:block;color:var(--text);font-size:16px}
.fsteps svg{width:52px;height:52px;stroke:var(--secondary);fill:none;stroke-width:4;stroke-linecap:round;stroke-linejoin:round}
@media (max-width:800px){.freehero{grid-template-columns:minmax(0,1fr);padding-block:32px}.fsteps{grid-template-columns:minmax(0,1fr)}
 .ftiles{grid-template-columns:repeat(4,minmax(0,1fr));gap:10px}.ftile{padding:14px 6px 10px}.flabel{font-size:10px}.ftag{font-size:10px;padding:4px 6px}}
.job{display:grid;gap:8px;align-content:start;background:var(--tertiary);border:2px solid var(--border-subtle);padding:20px}
.job .hed{font-size:20px;line-height:1.25;margin:0}.job .hed a{color:inherit;text-decoration:none}.job .hed a:hover{text-decoration:underline}
.job .employer{margin:0;font-weight:700;display:flex;flex-wrap:wrap;gap:4px 12px;align-items:center}
.job .pay{background:var(--secondary);color:var(--tertiary);font:700 13px/1 var(--sans);padding:5px 8px}
.job .dek{font-size:14px;line-height:1.55;margin:0}
.jobs{padding-block:0 64px}.jobs .qtabs{margin:12px 0 20px}
.joblist{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,320px),1fr));gap:20px}
.railbox.jobs .rail .job{width:300px}
.jobpage{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:40px;align-items:start;padding-block:0 32px}
.jobpage .box{display:grid;gap:14px}
@media (max-width:800px){.jobpage{grid-template-columns:minmax(0,1fr);gap:20px}}
.replybox{max-width:560px;width:calc(100% - 32px);border:2px solid var(--primary);background:var(--tertiary);color:var(--primary);padding:24px;border-radius:0}
.replybox::backdrop{background:rgba(46,26,16,.55)}
.replybox .close{position:absolute;top:8px;right:12px;background:none;border:0;font-size:28px;line-height:1;cursor:pointer;color:inherit}
.replybox form{position:relative;align-items:start}
.safenote{display:flex;gap:10px;align-items:flex-start}
.safenote svg{width:22px;height:22px;flex:none;fill:none;stroke:var(--secondary);stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.adrow{display:grid;gap:4px;justify-items:center;padding-block:16px}
.adlabel{font-size:10px;letter-spacing:.14em;color:var(--text-3)}
.adbanner{display:block;width:100%;max-width:728px;line-height:0;text-decoration:none}
.adbanner img{width:100%;height:auto;display:block;border:1px solid var(--border-subtle)}
.adbanner.house{display:flex;flex-wrap:wrap;align-items:center;justify-content:center;gap:4px 16px;min-height:90px;line-height:1.3;
  padding:12px 20px;background:var(--surface);border:2px dashed var(--secondary);text-align:center}
.adbanner.house .script{font-size:30px;color:var(--secondary);line-height:1}
.adbanner.house .adtext{font:600 15px/1.4 var(--sans)}
.adbanner.house .adspot{font-size:11px;color:var(--text-2);flex-basis:100%}
.adbanner.house:hover{border-style:solid}
.adbanner.promo{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:8px 20px;min-height:90px;box-sizing:border-box;
  line-height:1.2;padding:12px 24px;color:var(--primary);border:8px solid transparent;
  background:linear-gradient(var(--tertiary),var(--tertiary)) padding-box,repeating-linear-gradient(-55deg,var(--secondary) 0 9px,var(--tertiary) 9px 18px) border-box}
.adbanner.promo .ptext{display:grid;gap:6px;text-align:left}
.adbanner.promo .script{font-size:22px;color:var(--secondary);line-height:1}
.adbanner.promo .retro{font-size:24px;line-height:1.05}
.adbanner.promo .adtext{font:500 14px/1.4 var(--sans);color:var(--text-2)}
.adbanner.promo.hot{background:linear-gradient(135deg,#e5652b 0%,var(--secondary) 55%,var(--secondary-dk) 100%);border:0;border-radius:18px;box-shadow:0 10px 30px -14px rgba(120,52,16,.45)}
.adbanner.promo.hot .script{color:var(--tertiary)}
.adbanner.promo.hot .retro{color:var(--tertiary);text-shadow:2px 2px 0 var(--primary)}
.adbanner.promo.hot .adtext{color:var(--tertiary)}
.adbanner.promo.hot .btn{background:var(--tertiary);color:var(--primary);border-color:var(--tertiary);border-radius:999px}
.adbanner.promo.hot:hover .btn{background:var(--primary);color:var(--tertiary);border-color:var(--primary)}
.ad.house.promo{border:8px solid transparent;background:linear-gradient(var(--tertiary),var(--tertiary)) padding-box,repeating-linear-gradient(-55deg,var(--secondary) 0 9px,var(--tertiary) 9px 18px) border-box}
.ad.house.promo .dek{font-size:14px;line-height:1.5;margin:0;color:var(--text-2)}
.evad.promo .flyer-img .script{font-size:34px}
.sponsortag{display:flex;align-items:center;gap:8px;margin-bottom:10px;text-decoration:none;font-size:11px;color:var(--text-2)}
.sponsortag img{height:28px;width:auto}
.sponsortag.house{border-bottom:1px dashed var(--secondary);letter-spacing:.1em}
.sponsortag.house:hover{color:var(--secondary)}
@media (max-width:600px){.sponsortag.house{display:none}}
.evad .flyer-img .script{font-size:40px;line-height:1.1;color:var(--secondary);text-align:center}
.evad .sponsor{color:var(--text-3)}
footer .wrap.sponsors{justify-content:center;gap:6px 14px;padding-block:14px;border-top:1px solid rgba(251,246,236,.18);font-size:14px}
footer .sponsors .caps{color:var(--secondary)}
footer .sponsors a{color:inherit}
.spots{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:20px}
.spot{background:var(--tertiary);border:2px solid var(--border-subtle);border-top:4px solid var(--secondary);padding:20px;display:grid;gap:6px;align-content:start}
.spot .code{font:700 13px/1 var(--caps);letter-spacing:.12em;color:var(--secondary)}
.spot h3{font:700 20px/1.25 var(--serif);margin:0}
.spot p{margin:0;font-size:14px;line-height:1.55}
.spot .price{position:static;display:inline-block;justify-self:start;font-size:14px;padding:6px 10px;margin-top:6px}
.fund .cause,.watch-head .cause{display:inline-block;justify-self:start;margin-inline:20px;background:var(--surface);border:1px solid var(--secondary);color:var(--secondary-dk);font:700 11px/1 var(--caps);letter-spacing:.1em;text-transform:uppercase;padding:5px 7px}
.fund .muted{font-size:13px;color:var(--text-2)}
.fund .share{display:flex;flex-wrap:wrap;gap:8px}
.causebox .script{display:block;font-size:28px;line-height:1.3;color:var(--secondary)}
.goal{position:absolute;right:14px;bottom:-26px;z-index:1;width:104px;height:104px;border-radius:50%;display:grid;place-content:center;justify-items:center;
  background:var(--secondary);color:var(--tertiary);border:3px solid var(--tertiary);outline:2px solid var(--secondary);transform:rotate(-8deg);text-align:center}
.goal-k{font:700 10px/1 var(--caps);letter-spacing:.16em;text-transform:uppercase;opacity:.9}
.goal-v{font:700 24px/1.05 var(--serif);margin:3px 0 2px;white-space:nowrap}.goal-v.long{font-size:19px}
.fund .photo{overflow:visible}.fund .photo>img,.fund .photo>.art{overflow:hidden}
.play{position:absolute;left:50%;top:50%;width:64px;height:64px;margin:-32px 0 0 -32px;border-radius:50%;background:rgba(46,26,16,.78);border:3px solid var(--tertiary)}
.play::after{content:"";position:absolute;left:24px;top:17px;border-style:solid;border-width:12px 0 12px 19px;border-color:transparent transparent transparent var(--tertiary)}
.photo.video{aspect-ratio:16/9;background:#000}.photo.video iframe{position:absolute;inset:0;width:100%;height:100%;border:0}
.fund .tagline{font:700 10px/1 var(--caps);letter-spacing:.12em;text-transform:uppercase;color:var(--text-3)}
.fund .report{font-size:12px;color:var(--text-3);text-decoration:underline;justify-self:start}
.fund .report:hover{color:var(--secondary)}
.givebox{padding-block:0 48px}
.give{background:var(--surface);border:2px solid var(--border-strong);padding:18px clamp(14px,2.4vw,24px) 14px}
.give-top{display:flex;flex-wrap:wrap;align-items:end;justify-content:space-between;gap:10px 24px;margin-bottom:12px}
.give-top .script{display:block;margin-bottom:6px;font-size:24px;transform-origin:0 100%}
.give-top .retro{font-size:clamp(22px,2.6vw,32px);line-height:1}
.give-top p{margin:6px 0 0;max-width:60ch;font-size:14px;color:var(--text-2)}
.give .rail-head{margin-bottom:12px}.give .rail-head .label{padding-bottom:6px}
.give .rail{grid-auto-columns:calc((100% - 72px)/4)}
@media (max-width:960px){.give .rail{grid-auto-columns:calc((100% - 24px)/2.3)}}
@media (max-width:600px){.give .rail{grid-auto-columns:74%}}
.give .listing{gap:6px;padding-bottom:14px}.give .listing>:not(.photo){padding-inline:14px}
.give .listing .photo{aspect-ratio:16/10}.give .listing .hed{font-size:17px}
.give .listing .dek{font-size:13px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.give .fund .cause{margin-inline:14px;font-size:10px;padding:4px 6px}.give .fund .muted{font-size:12px}
.give .goal{width:80px;height:80px;bottom:-20px;right:10px}.give .goal-v{font-size:18px}.give .goal-v.long{font-size:15px}.give .goal-k{font-size:8px}
.give .give-note{margin-top:12px;padding-top:10px;font-size:12px}
.give-top .share{display:flex;flex-wrap:wrap;gap:8px}
.give .rail-head{border-bottom-color:var(--border-medium);margin-bottom:16px}
.give .railbox{padding:0}
.give .listing{border-color:var(--border-medium)}
.give-note{display:flex;gap:12px;align-items:flex-start;margin-top:20px;padding-top:16px;border-top:1px dashed var(--border-medium);font-size:13px;line-height:1.55;color:var(--text-2)}
.give-note b{color:var(--text)}
.give-note svg{flex:none;width:20px;height:20px;margin-top:1px;fill:none;stroke:var(--secondary);stroke-width:2}
.givefacts{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:16px 32px;margin:0;padding:24px clamp(16px,3vw,32px);background:var(--surface);border:2px solid var(--border-strong);list-style:none}
.givefacts li{font-size:14px;line-height:1.55;color:var(--text-2)}
.givefacts b{display:block;font:700 12px/1.3 var(--caps);letter-spacing:.1em;text-transform:uppercase;color:var(--text);margin-bottom:4px}
.tiers{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:20px;align-items:stretch}
.tier{position:relative;background:var(--tertiary);border:2px solid var(--border-strong);padding:24px 20px;display:grid;gap:8px;align-content:start}
.tier.pick{border:3px solid var(--secondary);background:#fffdf8}
.tier .picked{position:absolute;top:-13px;left:16px;background:var(--secondary);color:var(--tertiary);font-size:11px;padding:5px 8px}
.tier h3{font:700 22px/1.2 var(--serif);margin:0}
.tier .cost{margin:0;font-size:15px;color:var(--text-2)}.tier .cost b{font:700 36px/1 var(--serif);color:var(--secondary)}
.tier ul{margin:4px 0 8px;padding-left:18px;font-size:14px;line-height:1.55;display:grid;gap:4px}
.tier .btn{justify-self:start}
.deals{margin:24px 0 0;padding-left:18px;display:grid;gap:4px;font-size:15px}
.reqs{columns:2 320px;column-gap:40px}
.reqs li{break-inside:avoid;margin-bottom:10px;font-size:15px;line-height:1.6}
footer .wrap.credit{display:flex;flex-wrap:wrap;align-items:baseline;justify-content:center;gap:4px 10px;text-align:center;
  padding-top:18px;padding-bottom:22px;border-top:1px solid rgba(251,246,236,.18);font:600 14px/1.4 var(--sans)}
footer .credit .script{font-size:26px;color:var(--secondary);line-height:1}
footer .credit small{flex-basis:100%;font:400 12px/1.6 var(--sans);opacity:.7}
footer .credit .legal{flex-basis:100%;display:flex;flex-wrap:wrap;justify-content:center;gap:4px 18px;font:600 13px/1.6 var(--sans)}
footer .credit .legal a{color:inherit;text-decoration:underline;text-underline-offset:3px}
.note.report{font-size:14px;opacity:.85;margin-bottom:32px}
footer .credit .legal.towns{font-weight:400;opacity:.85}
.note.towns{font-size:15px;margin-top:8px}
.grid.events .slide.flyer{width:auto}
.vh{position:absolute;width:1px;height:1px;margin:-1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}
.legaltext{max-width:760px;padding-block:8px 40px}.legaltext .hed{font-size:24px;margin:28px 0 6px}.legaltext p{font-size:17px;line-height:1.65;margin:0}

/* sports: athlete cards and tournament brackets */
.athlete .sport{color:var(--secondary)}
.bracketbox{display:grid;gap:12px;margin-bottom:40px}
.bracketbox .rail-head{margin:0}
.bracketbox h3.hed{font-size:24px;line-height:1.2;margin:0}
.bracket{display:grid;grid-template-columns:repeat(var(--rounds),minmax(190px,1fr));gap:0;overflow-x:auto;overscroll-behavior-x:contain;
  background:var(--tertiary);border:2px solid var(--border-strong);border-top:4px solid var(--secondary);padding:16px 0}
.round{display:grid;grid-template-rows:auto 1fr;padding:0 16px;border-left:2px dashed var(--border-medium)}
.round:first-child{border-left:0}
.round h4{margin:0 0 12px;font-size:12px;letter-spacing:.12em;color:var(--text-2)}
.games{display:flex;flex-direction:column;justify-content:space-around;gap:16px}
.game{border:2px solid var(--border-strong);background:#fffdf8}
.team{display:flex;justify-content:space-between;gap:12px;padding:8px 10px;font:400 15px/1.3 var(--sans)}
.team+.team{border-top:1px solid var(--border-subtle)}
.team b{font-variant-numeric:tabular-nums;min-width:2ch;text-align:right}
.team.won{background:var(--secondary);color:var(--tertiary);font-weight:700}
.trophy{display:grid;justify-items:center;gap:8px;text-align:center}
.trophy svg{width:72px;height:72px;fill:none;stroke:var(--secondary);stroke-width:4;stroke-linecap:round;stroke-linejoin:round}
.trophy .hed{font:700 22px/1.2 var(--serif)}

/* birthday shout-outs: front page box and birthdays.html */
.bday{display:grid;gap:24px;padding:32px clamp(16px,3vw,32px);justify-items:center;text-align:center}
.bday-head{display:grid;justify-items:center;gap:6px}
.bday-head .script{margin-bottom:4px}
.bday-head .muted{margin:8px 0 0}
.bday-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,240px),1fr));gap:16px;width:100%}
.bday-card{background:linear-gradient(160deg,#fffaf2 0%,#fde9d6 100%);border:0;border-radius:18px;padding:28px 22px 26px;display:grid;gap:10px;justify-items:center;align-content:start;
  box-shadow:0 10px 30px -14px rgba(120,52,16,.35);transition:transform .2s ease,box-shadow .2s ease}
.bday-card:hover{transform:translateY(-3px);box-shadow:0 16px 36px -14px rgba(120,52,16,.45)}
.bday-cake{width:56px;height:56px;border-radius:50%;background:var(--secondary);color:var(--tertiary);display:grid;place-items:center;line-height:0}
.bday-cake svg{width:30px;height:30px}
.bday-card .kicker{color:var(--secondary);letter-spacing:.16em}
.bday-name{font:700 clamp(24px,3vw,30px)/1.15 var(--serif);margin:0;overflow-wrap:anywhere}
.bday-msg{font:italic 400 16px/1.55 var(--serif);margin:0;color:var(--text);overflow-wrap:anywhere}
.bday-from{margin:0;color:var(--text-2)}
.bdays .bday-card{height:100%;box-sizing:border-box;text-align:center}
.bdays .rail{padding:6px 4px 22px;margin:-6px -4px -18px}
.bdays .rail:focus,.bdays .rail:focus-visible{outline:none;box-shadow:none}
.bday-card.add{background:var(--secondary);color:var(--tertiary);text-decoration:none;align-content:center}
.bday-card.add .kicker,.bday-card.add .bday-from{color:var(--tertiary)}
.bday-card.add .plus{width:56px;height:56px;border-radius:50%;background:var(--tertiary);color:var(--secondary);display:grid;place-items:center;font:700 34px/1 var(--sans)}
.bday-acts{justify-content:center;width:100%}.bday-acts .btn{flex:0 1 280px}
.bday-date{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:8px}
.bday-extra{display:grid;grid-template-columns:minmax(0,1fr);gap:20px;padding:16px;background:var(--surface)}

.verify{display:grid;gap:12px;background:var(--tertiary);border:2px solid var(--border-subtle);border-top:4px solid var(--secondary);padding:clamp(16px,3vw,32px)}
.verify .script{margin-bottom:16px}.verify p{margin:0}
.verify .row{display:flex;gap:12px;flex-wrap:wrap}.verify [data-v-code]{flex:0 1 200px;font:700 22px/1 var(--sans);letter-spacing:.3em;text-align:center}

/* forms and the admin desk */
.formbox{padding-block:0 64px;scroll-margin-top:72px}
.formbox .label{margin-bottom:24px}
form.sunny{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:20px 24px;background:var(--tertiary);border:2px solid var(--border-subtle);border-top:4px solid var(--secondary);padding:clamp(16px,3vw,32px)}
@media (max-width:700px){form.sunny{grid-template-columns:minmax(0,1fr)}}
.fld{display:grid;gap:6px;min-width:0}.fld.wide,.wide{grid-column:1/-1}
.fld>.caps{color:var(--text)}
.hint{font:400 13px/1.5 var(--sans);color:var(--text-2);margin:0}
input:not([type=checkbox]):not([type=file]),select,textarea{font:400 16px/1.4 var(--sans);color:var(--text);background:#fffdf8;border:2px solid var(--border-medium);border-radius:0;padding:10px 12px;min-height:44px;width:100%;box-sizing:border-box}
textarea{resize:vertical;min-height:120px}
input:focus,select:focus,textarea:focus{outline:none;border-color:var(--primary);box-shadow:var(--ring)}
input[type=file]{font:400 14px/1.4 var(--sans)}
.check{display:flex;gap:10px;align-items:flex-start;font-size:14px;line-height:1.5}
.check input{width:20px;height:20px;margin-top:2px;accent-color:var(--secondary)}
.hp{position:absolute;left:-9999px;width:1px;height:1px;overflow:hidden}
.submitrow{display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.formmsg{font:600 14px/1.4 var(--sans);color:var(--secondary-dk);margin:0}
.formnote{grid-column:1/-1;margin:0;padding:12px 16px;background:var(--surface);font-size:14px}
.btn:disabled{opacity:.35;cursor:not-allowed}
.signin{background:var(--surface);padding:16px;gap:8px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}.row input{flex:1 1 220px;width:auto}
.thanks{background:var(--tertiary);border:2px solid var(--border-subtle);border-top:4px solid var(--secondary);padding:32px;text-align:center;display:grid;justify-items:center;gap:8px}
.thanks .script{font-size:40px;color:var(--secondary)}
.admin{padding-block:0 64px;display:grid;gap:24px}
.admin .box{max-width:560px;justify-self:center;width:100%}
.logform{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr) auto;gap:8px}
@media (max-width:600px){.logform{grid-template-columns:minmax(0,1fr)}}
.deskbar{display:grid;gap:8px;margin-bottom:16px}
.qtabs{display:flex;flex-wrap:wrap;gap:0 8px}
.queue{display:grid;gap:16px}
  background:var(--secondary);color:var(--tertiary);border:2px solid var(--secondary);box-shadow:inset 0 0 0 6px var(--secondary),inset 0 0 0 8px var(--primary)}
.qitem.pick{grid-template-columns:120px minmax(0,1fr);padding:12px;align-items:center}.qitem.pick .hed{font-size:18px;margin:4px 0 8px}
.qitem.pick.on{border-color:var(--secondary);background:var(--surface)}.qitem.pick .qphoto img{aspect-ratio:3/2;object-fit:cover;width:100%;display:block}
.qitem{display:grid;grid-template-columns:200px minmax(0,1fr);gap:20px;background:var(--tertiary);border:2px solid var(--border-subtle);padding:20px}
.qitem:not(:has(.qphoto)){grid-template-columns:minmax(0,1fr)}
@media (max-width:700px){.qitem{grid-template-columns:minmax(0,1fr)}}
.qphoto img{width:100%;aspect-ratio:4/3;object-fit:cover;display:block}
.qbody{display:grid;gap:8px;align-content:start;min-width:0}
.qbody .hed{font-size:22px;line-height:1.2}
.qtext{margin:0;white-space:pre-line;font-size:15px}
.facts{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:2px 12px;margin:0;font-size:13px}
.facts dt{font-weight:700}.facts dd{margin:0;overflow-wrap:anywhere}
.btn.ghost{border-color:transparent;background:none}.btn.ghost:hover{color:var(--secondary-dk);background:none}

/* community pages */
.pagehead{padding-block:48px 32px;text-align:center;display:grid;justify-items:center;gap:4px}
.pagehead .retro{margin:0}
.pagehead .intro{max-width:60ch;margin:16px 0 0;font-size:18px;line-height:1.65;color:var(--text-2)}
.card .art.sm,.path .art.sm,.role .art.sm{width:56px;height:56px;display:block}
.art.sm svg{width:100%;height:100%;fill:none;stroke:var(--primary);stroke-width:4;stroke-linecap:round;stroke-linejoin:round}
.path .hed,.role .hed{font-size:24px;line-height:1.2}
.path .dek,.role .dek{-webkit-line-clamp:unset;display:block}
.grid.four{grid-template-columns:repeat(4,minmax(0,1fr))}
@media (max-width:960px){.grid.four{grid-template-columns:repeat(2,minmax(0,1fr))}}
@media (max-width:600px){.grid.four{grid-template-columns:minmax(0,1fr)}}
.meta .tag{color:var(--secondary-dk);font-weight:700}
.bizhero .photo{aspect-ratio:16/9;max-height:460px}
.lead h1.hed{font-size:clamp(34px,4.6vw,56px);line-height:1.05;letter-spacing:-.02em;margin:0}

/* candy stripes: the one decorative brand element, used as trim only */
.stripes{height:14px;background:repeating-linear-gradient(-55deg,var(--secondary) 0 9px,var(--tertiary) 9px 18px)}
footer{background:var(--primary);color:var(--tertiary)}
footer .sky{display:block;width:100%;height:auto;color:var(--tertiary);opacity:.12}
footer .wrap{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:center;gap:16px 48px;padding-block:24px 32px}
footer .logo{width:220px;line-height:0}footer .logo svg{width:100%;height:auto}
footer p{margin:0;max-width:56ch;font-size:14px;line-height:1.6;opacity:.85}



/* magazine front: ticker, lead + stack, sidebar, labelled rules with "Explore all" */
.ticker{background:var(--primary);color:var(--tertiary)}
.ticker-in{display:flex;align-items:stretch;gap:0;overflow:hidden}
.tick-label{flex:none;display:flex;align-items:center;gap:8px;padding:12px 18px 10px 0;margin-right:16px;border-right:2px solid var(--secondary);
  font:700 12px/1 var(--caps);letter-spacing:.12em;text-transform:uppercase;color:var(--tertiary);white-space:nowrap}
.tick-label::before{content:"";width:10px;height:10px;background:var(--secondary)}
.tick-viewport{flex:1;min-width:0;overflow:hidden;mask-image:linear-gradient(90deg,transparent,#000 24px,#000 calc(100% - 24px),transparent)}
.tick-track{display:flex;width:max-content;animation:tick 70s linear infinite}
.tick-dup{display:flex}
.ticker:hover .tick-track,.ticker:focus-within .tick-track{animation-play-state:paused}
.tick-track a{display:flex;align-items:center;gap:10px;padding:10px 28px 10px 0;color:var(--tertiary);text-decoration:none;font:600 14px/1.3 var(--sans);white-space:nowrap}
.tick-track a:hover{color:#f3b38f}
.tick-tag{background:var(--secondary);color:var(--tertiary);font:700 10px/1 var(--caps);letter-spacing:.1em;text-transform:uppercase;padding:5px 7px 3px}
@keyframes tick{to{transform:translateX(-50%)}}
@media (prefers-reduced-motion:reduce){.tick-track{animation:none}.tick-dup{display:none}.tick-viewport{overflow-x:auto;mask-image:none}}
@media (max-width:600px){.tick-label{font-size:0;gap:0;padding-right:12px;margin-right:12px}.tick-label::before{width:12px;height:12px}}
.mag-head{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;border-bottom:2px solid var(--primary);margin-bottom:20px}
.mag-label{white-space:nowrap;margin:0;background:var(--primary);color:var(--tertiary);font:700 12px/1 var(--caps);letter-spacing:.12em;text-transform:uppercase;padding:9px 12px 7px}
.explore{white-space:nowrap;font:700 11px/1 var(--caps);letter-spacing:.12em;text-transform:uppercase;color:var(--text-2);text-decoration:none;padding:8px 0}
.explore:hover{color:var(--secondary)}
.mag{background:var(--tertiary);display:grid;grid-template-columns:minmax(0,2.4fr) minmax(0,1fr);gap:40px;padding-block:32px 56px}
.mag-grid{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:24px;align-items:start}
.mag .lead{border-bottom:3px solid var(--secondary)}
.mag .lead .hed{font-size:clamp(30px,3vw,40px)}
.mag .lead .dek{font-size:17px;line-height:1.6}
.mag-stack{display:grid;gap:20px}
.midcard{display:grid;gap:8px;align-content:start;padding-bottom:20px;border-bottom:1px solid var(--border-subtle)}
.midcard:last-child{border-bottom:0;padding-bottom:0}
.midcard .photo{margin-bottom:4px}
.midcard .hed{font-size:20px;line-height:1.22;letter-spacing:-.01em;margin:0}
.mag-side{min-width:0}
.mag-side .brief{grid-template-columns:84px minmax(0,1fr)}
.mag-side .brief .photo{grid-column:1;grid-row:1}
.mag-side .brief > div{grid-column:2;grid-row:1}
@media (max-width:1100px){.mag{grid-template-columns:minmax(0,1fr);gap:32px}.mag-side .brief{padding-inline:8px}}
@media (min-width:601px) and (max-width:1100px){.mag-side{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));column-gap:24px}.mag-side .mag-head{grid-column:1/-1}}
@media (max-width:760px){.mag-grid{grid-template-columns:minmax(0,1fr)}.mag-stack{grid-template-columns:repeat(2,minmax(0,1fr))}.midcard{border-bottom:0;padding-bottom:0}}
@media (max-width:600px){.mag{padding-block:20px 40px}.mag-stack{grid-template-columns:minmax(0,1fr)}.midcard{border-bottom:1px solid var(--border-subtle);padding-bottom:20px}}

/* ===== NewsFlash structure, Sunny orange accent (2026) ===== */
:root{--primary:#141414;--text:#1b1b1b;--text-2:#5a5a5a;--text-3:#8c8c8c;--tertiary:#fff;--surface:#f3f3f3;--surface-raised:#e9e9e9;
  --border-subtle:#e6e6e6;--border-medium:#d4d4d4;--border-strong:#141414}
html{background:#fff}html::before{display:none}
.sheet,.sheet:is(*){max-width:none;margin:0;border:0;background:#fff}
@media (max-width:1264px){.sheet{margin:0}}@media (max-width:600px){.sheet{margin:0;background:#fff}}
.wrap{max-width:1200px;margin-inline:auto;box-sizing:border-box}
.stripes{height:4px;background:var(--secondary)}
.photo img{filter:none}
.hed{font-family:var(--sans);font-weight:800;letter-spacing:-.01em}
.kicker{color:var(--secondary)}
.topbar{background:var(--primary);color:#fff}
.topbar-in{display:flex;justify-content:space-between;align-items:center;gap:16px;padding-block:8px;font-size:10.5px}
.topbar-mid{opacity:.7}
.topshare{display:flex;align-items:center;gap:6px;color:#fff;text-decoration:none}.topshare svg{width:12px;height:12px;fill:currentColor}
.topshare:hover{color:var(--secondary)}
.plate{display:grid;grid-template-columns:minmax(220px,340px) minmax(0,728px);justify-content:space-between;align-items:center;gap:32px;padding-block:22px}
.plate-ad .adrow{padding:0}.plate-ad .adbanner{min-height:90px;max-width:728px}
.plate-ad .adbanner.promo{padding:14px 20px}
.plate-ad .adbanner.promo .retro{font-size:22px}
.navbar{border-block:1px solid var(--border-subtle);box-shadow:0 2px 0 rgba(0,0,0,.03)}
.navbar .strip{display:flex;justify-content:space-between;align-items:stretch;flex-wrap:nowrap;gap:12px;padding-block:0}
.navbar .navgroup{display:flex;align-items:stretch;gap:0;overflow-x:auto;scrollbar-width:none}
.navbar .navgroup a,.navbar .navbtn{display:flex;align-items:center;padding:15px 13px 13px;color:var(--text);font-size:11.5px}
.navbar .navgroup a:hover,.navbar .navbtn:hover{color:var(--secondary)}
.navbar .navgroup a.on{background:var(--secondary);color:#fff}
.navbar .navcta{align-self:center;background:var(--primary);color:#fff;padding:9px 14px 8px;white-space:nowrap;text-decoration:none}
.navbar .navcta:hover{background:var(--secondary);color:#fff}
.ticker{display:flex;align-items:stretch;gap:0;margin-block:20px 0;background:none;color:var(--text)}
.ticker>*{border-block:1px solid var(--border-subtle)}
.tick-label{flex:none;display:flex;align-items:center;margin:0;padding:0 16px;background:var(--secondary);color:#fff;border:0;
  font:700 12px/1 var(--caps);letter-spacing:.12em;text-transform:uppercase}
.tick-label::before{display:none}
.tick-list{flex:1;min-width:0;list-style:none;margin:0;padding:0 16px;position:relative;border-left:0}
.tick-list li{display:none}.tick-list li.on{display:block;animation:tickin .35s ease}
.tick-list a{display:flex;align-items:center;gap:10px;padding:11px 0 10px;color:var(--text);text-decoration:none;font:600 14px/1.3 var(--sans);
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tick-list a:hover{color:var(--secondary)}
.tick-tag{flex:none;background:none;color:var(--text-3);font:700 10px/1 var(--caps);letter-spacing:.1em;text-transform:uppercase;padding:0}
.tick-nav{display:flex;border-left:0}
.tick-nav button{width:36px;border:1px solid var(--border-subtle);border-block:0;background:#fff;color:var(--text);font-size:20px;line-height:1;cursor:pointer}
.tick-nav button+button{border-left:0}
.tick-nav button:hover{background:var(--secondary);color:#fff;border-color:var(--secondary)}
@keyframes tickin{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
@media (prefers-reduced-motion:reduce){.tick-list li.on{animation:none}}
.hero{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:4px;padding-block:20px 8px}
.hero.solo{grid-template-columns:1fr}
.hero-side{display:grid;grid-template-rows:1fr 1fr;gap:4px}
.ov{position:relative;overflow:hidden;background:#222;min-height:0}
.ov.big{aspect-ratio:16/10}
.ov .photo{position:absolute;inset:0;aspect-ratio:auto;height:100%}
.ov .photo img{transition:transform .6s ease}.ov:hover .photo img{transform:scale(1.04)}
.ov-text{position:absolute;inset:auto 0 0;display:grid;justify-items:start;gap:8px;padding:70px 22px 18px;color:#fff;pointer-events:none;
  background:linear-gradient(rgba(0,0,0,0),rgba(0,0,0,.82))}
.ov-text a{pointer-events:auto}
.ov .kicker{background:var(--secondary);color:#fff;padding:5px 8px 3px}
.ov .hed{margin:0;color:#fff;font-size:clamp(16px,1.6vw,20px);line-height:1.2;text-wrap:balance}
.ov.big .hed{font-size:clamp(22px,3vw,36px);line-height:1.1}
.ov-meta{font:500 12px/1.3 var(--sans);opacity:.85}
.nf{display:grid;grid-template-columns:minmax(0,1fr) 320px;gap:40px;padding-block:28px 12px}
.nf-col,.nf-side{display:grid;gap:12px;align-content:start}
.nf-side{gap:20px}
.mag-head{align-items:center;border-bottom:1px solid var(--border-subtle);margin-bottom:6px;padding-bottom:10px}
.mag-label{background:none;color:var(--text);border-left:4px solid var(--secondary);padding:2px 0 0 10px;
  font:800 19px/1.1 var(--sans);letter-spacing:-.01em;text-transform:none}
.explore{color:var(--text-2)}.explore:hover{color:var(--secondary)}
.mag-head .sections{position:static;background:none;border:0;display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;min-width:0}
.mag-head .sections .tab{border:0;background:none;padding:4px 8px;font:600 12px/1.2 var(--sans);text-transform:none;letter-spacing:0;color:var(--text-2);white-space:nowrap;box-shadow:none}
.mag-head .sections .tab .n{display:none}
.mag-head .sections .tab:hover,.mag-head .sections .tab[aria-pressed="true"]{color:var(--secondary);box-shadow:none}
.trend{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:24px;margin-bottom:28px}
.tcard{display:grid;gap:8px;align-content:start}
.tcard .hed{margin:4px 0 0;font-size:24px;line-height:1.15}
.tcard .dek{margin:0;color:var(--text-2);font-size:15px;line-height:1.6}
.trend-list,.side-list{display:grid;gap:16px;align-content:start}
.thumb{display:grid;grid-template-columns:104px minmax(0,1fr);gap:14px;align-items:start}
.thumb .photo{aspect-ratio:4/3}
.thumb>div{display:grid;gap:4px}
.thumb .kicker{font-size:10px}
.thumb .hed{margin:0;font-size:15px;line-height:1.3}
.tdate{font:500 12px/1.2 var(--sans);color:var(--text-3)}
.around{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}
.around .midcard{display:grid;gap:8px;align-content:start}
.around .hed{margin:0;font-size:17px;line-height:1.25}
.nf-side .side-list .thumb{grid-template-columns:84px minmax(0,1fr)}
.watch{background:#141414}
.w-slide{position:absolute;inset:0}
.w-media{position:absolute;inset:0;background:radial-gradient(circle at 75% 25%,rgba(217,83,30,.55),transparent 55%),linear-gradient(135deg,#2a2a2a,#0d0d0d)}
.w-media img,.w-media iframe{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;border:0;display:block}
.ov.watch:hover .w-media img{transform:scale(1.04)}.w-media img{transition:transform .6s ease}
.w-play{position:absolute;inset:0;width:100%;border:0;padding:0 0 18%;background:none;cursor:pointer;display:grid;place-items:center}
.w-play span{width:54px;height:54px;border-radius:50%;background:var(--secondary);display:grid;place-items:center;box-shadow:0 4px 18px rgba(0,0,0,.4);transition:transform .2s}
.w-play span::after{content:"";margin-left:4px;border-style:solid;border-width:10px 0 10px 17px;border-color:transparent transparent transparent #fff}
.w-play:hover span,.w-play:focus-visible span{transform:scale(1.1)}
.watch .ov-text{z-index:1;padding-top:90px;background:linear-gradient(rgba(0,0,0,0),rgba(0,0,0,.6) 40%,rgba(0,0,0,.92))}
.watch .hed{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.w-play{padding:14px!important;place-items:start!important}
.watch.playing .ov-text,.watch.playing .w-dots,.watch.playing .w-bar{display:none}
.w-dots{position:absolute;top:12px;right:12px;z-index:2;display:flex;gap:6px}
.w-dots button{width:9px;height:9px;padding:0;border:1.5px solid #fff;border-radius:50%;background:transparent;cursor:pointer}
.w-dots button[aria-current]{background:var(--secondary);border-color:var(--secondary)}
.w-bar{position:absolute;left:0;bottom:0;z-index:2;height:3px;width:0;background:var(--secondary)}
.w-bar.run{animation:wbar 10s linear forwards}
.watch:hover .w-bar,.watch:focus-within .w-bar{animation-play-state:paused}
@keyframes wbar{to{width:100%}}
@media (prefers-reduced-motion:reduce){.w-bar{display:none}}
.w-share{display:inline-flex;align-items:center;gap:5px;margin-left:12px;padding:3px 8px 2px;border:1px solid rgba(255,255,255,.6);color:#fff;
  font:700 10px/1.4 var(--caps);letter-spacing:.1em;text-transform:uppercase;text-decoration:none;pointer-events:auto}
.w-share svg{width:11px;height:11px;fill:currentColor}.w-share:hover{background:var(--secondary);border-color:var(--secondary)}
.watch-page{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(0,1fr);gap:32px;align-items:start;padding-block:28px}
.watch.solo{aspect-ratio:16/9}
.watch-body{display:grid;gap:10px;align-content:start}
.watch-body h1.hed{margin:0;font-size:clamp(26px,3vw,36px);line-height:1.12}
.watch-body .host{margin:0;color:var(--text-2)}
.watch-body .dek{color:var(--text-2);font-size:16px;line-height:1.65}.watch-body .dek p{margin:0 0 10px}
.watch-body .share{display:flex;flex-wrap:wrap;align-items:center;gap:10px 20px;margin-top:4px}.watch-body .share .textbtn{padding:0}
.watch-body .note{margin:6px 0 0;font-size:14px;color:var(--text-2)}
.watch-more{padding-block:12px 32px}
.watch-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:24px}
.watch-tile{display:grid;gap:8px;text-decoration:none;color:var(--text)}
.watch-tile .w-media{position:relative;aspect-ratio:16/9;overflow:hidden}
.watch-tile .hed{font-size:17px;line-height:1.25}.watch-tile:hover .hed{color:var(--secondary)}
@media (max-width:860px){.watch-page,.watch-grid{grid-template-columns:1fr}}
.first{display:grid;justify-items:center;align-content:center;text-align:center;gap:8px;padding:26px 20px;box-sizing:border-box;
  border:2px dashed var(--secondary);background:#fff7f2;color:var(--text);text-decoration:none}
.first:hover{background:#ffefe5}
.first-logo img{display:block;width:min(200px,80%);height:auto;margin-inline:auto}
.first .kicker{margin-top:4px}
.first .hed{font-family:var(--serif);font-weight:700;font-size:22px;line-height:1.15}
.first .dek{margin:0;color:var(--text-2);font-size:14px;line-height:1.5;max-width:34ch}
.first .btn{margin-top:6px}
.first-wrap{max-width:520px;margin:8px auto 0}
.first-wrap .first{padding:34px 28px}
.first-wrap .first-logo img{width:min(260px,80%)}
.watch.big{aspect-ratio:16/9}
.watch.big .hed{font-size:clamp(20px,2.6vw,32px)}
.watch-hub{padding-block:8px 20px}
.watch-tile .w-play{position:absolute;inset:0;padding:12px;place-items:start}
.watch-tile .w-play span{width:40px;height:40px}.watch-tile .w-play span::after{border-width:8px 0 8px 13px;margin-left:3px}
.givebox{padding-block:24px 40px}
.watch-layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:40px;align-items:start;padding-block:28px 12px}
.watch-layout.solo{grid-template-columns:minmax(0,860px);justify-content:center}
.watch-main{display:grid;gap:18px;min-width:0}
.watch-head{display:grid;gap:8px;padding-bottom:18px;border-bottom:1px solid var(--border-subtle)}
.watch-head .kicker{text-decoration:none}
.watch-head h1.hed{margin:0;font-size:clamp(26px,2.8vw,36px);line-height:1.1;text-wrap:balance}
.watch-meta{margin:0;color:var(--text-2)}
.watch-head .share{display:flex;flex-wrap:wrap;align-items:center;gap:10px 16px;margin-top:6px}
.watch-head .share .btn{padding:10px 16px 9px}.watch-head .share .textbtn{padding:0}
.wu-card{position:relative;background:var(--surface);border-left:4px solid var(--secondary);padding:22px 26px 18px}
.wu-head{margin:0 0 12px;font:800 18px/1.2 var(--sans)}
.wu-body{max-height:340px;overflow:hidden;position:relative;font-size:16px;line-height:1.7;color:var(--text)}
.wu-card:not(.open) .wu-body::after{content:"";position:absolute;inset:auto 0 0;height:90px;background:linear-gradient(rgba(243,243,243,0),var(--surface))}
.wu-card.open .wu-body{max-height:none}
.wu-body p{margin:0 0 14px;max-width:68ch}
.wu-label{margin:22px 0 10px;font:700 12px/1.3 var(--caps);letter-spacing:.14em;text-transform:uppercase;color:var(--secondary)}
.wu-label:first-child{margin-top:0}
.wu-list{list-style:none;margin:0 0 16px;padding:0;display:grid;gap:10px;max-width:68ch}
.wu-list li{position:relative;padding-left:22px}
.wu-list li::before{content:"";position:absolute;left:2px;top:.62em;width:8px;height:8px;background:var(--secondary)}
.wu-list li.has-ts{display:flex;gap:12px;padding-left:0}.wu-list li.has-ts::before{display:none}
.wu-ts{flex:none;min-width:52px;text-align:center;box-sizing:border-box;font:700 12px/1.9 var(--sans);color:#fff;background:var(--primary);padding:0 8px;text-decoration:none;height:fit-content}
a.wu-ts:hover{background:var(--secondary)}
.wu-body a:not(.wu-ts){color:var(--secondary-dk);text-decoration:underline;text-underline-offset:3px}
.wu-tags{display:flex;flex-wrap:wrap;gap:6px}.wu-tags span{font:600 12px/1 var(--sans);color:var(--text-2);background:#fff;border:1px solid var(--border-medium);padding:6px 9px}
.wu-more{margin-top:10px;border:0;background:none;padding:0;cursor:pointer;font:700 12px/1.4 var(--caps);letter-spacing:.12em;text-transform:uppercase;color:var(--text);
  text-decoration:underline;text-decoration-color:var(--secondary);text-decoration-thickness:2px;text-underline-offset:5px}
.wu-more:hover{color:var(--secondary)}
.watch-pitch{margin:0;font-size:14px;color:var(--text-2)}
.watch-aside{display:grid;gap:4px;align-content:start;position:sticky;top:16px}
.watch-list{display:grid;gap:14px;margin-bottom:10px}
.watch-row{display:grid;grid-template-columns:140px minmax(0,1fr);gap:12px;align-items:start;text-decoration:none;color:var(--text)}
.watch-row .w-media{position:relative;aspect-ratio:16/9;overflow:hidden}
.watch-row .hed{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden;font-size:15px;line-height:1.3;margin-bottom:4px}
.watch-row .host{font-size:10px;color:var(--text-3)}
.watch-row:hover .hed{color:var(--secondary)}
@media (max-width:980px){.watch-layout{grid-template-columns:minmax(0,1fr)}.watch-aside{position:static}}
@media (max-width:600px){.wu-card{padding:18px 16px 14px}.watch-row{grid-template-columns:120px minmax(0,1fr)}}
.story-layout .photo{aspect-ratio:16/9}
.story-layout .photo img{object-fit:cover}
.story-layout .wu-body .readfull{display:grid;gap:12px;justify-items:start;margin-top:6px;padding-top:16px;border-top:1px solid var(--border-medium);color:var(--text-2);font-size:15px}
.story-facts{display:flex;flex-wrap:wrap;gap:0;margin:0;border:1px solid var(--border-subtle)}
.story-facts div{flex:1 1 140px;padding:12px 16px;border-right:1px solid var(--border-subtle)}.story-facts div:last-child{border-right:0}
.story-facts dt{font:700 10px/1.4 var(--caps);letter-spacing:.14em;text-transform:uppercase;color:var(--secondary)}
.story-facts dd{margin:2px 0 0;font:700 17px/1.3 var(--sans)}
.story-note{margin:0;font-size:14px;color:var(--text-2)}
.story-layout .watch-meta .tag{color:var(--secondary)}
.fund .dek{display:-webkit-box;-webkit-line-clamp:3;-webkit-box-orient:vertical;overflow:hidden}
.fund .hed a,.fund .photo{color:inherit;text-decoration:none}.fund .hed a:hover{text-decoration:underline}
.fund .more-link{display:inline-block;margin:2px 0 6px;color:var(--primary);font-size:12px;text-decoration:none}
.watch-head .cause{margin:8px 0 4px;justify-self:start}.watch-main .give-note{margin:18px 0 0}
.event-top{display:grid;grid-template-columns:minmax(0,300px) minmax(0,1fr);gap:28px;align-items:start}
.event-top .watch-head{border-bottom:0;padding-bottom:0}
.event-flyer{border:1px solid var(--border-subtle);background:var(--surface)}
.event-flyer img{display:block;width:100%;height:auto}
.event-facts{margin-top:6px}
.event-row .w-media{aspect-ratio:4/5;width:84px}.event-row{grid-template-columns:84px minmax(0,1fr)}
.event-row .w-media img{width:100%;height:100%;object-fit:cover}
@media (max-width:700px){.event-top{grid-template-columns:1fr}.event-flyer{max-width:420px}}
footer{background:var(--primary)!important}
.btn.solid{background:var(--secondary);border-color:var(--secondary)}
@media (max-width:980px){.nf{grid-template-columns:1fr}.nf-side{grid-template-columns:repeat(auto-fit,minmax(280px,1fr));align-items:start}}
@media (max-width:760px){
  .plate{grid-template-columns:1fr;justify-items:center;gap:14px;padding-block:16px}.plate .logo{width:min(300px,80%)}
  .topbar-mid{display:none}
  .hero{grid-template-columns:1fr}.hero-side{grid-template-rows:none;grid-template-columns:1fr 1fr}
  .ov.big{aspect-ratio:4/3}.hero-side .ov{aspect-ratio:1}.hero-side .ov-text{padding:40px 12px 12px}.hero-side .ov .hed{font-size:15px}
  .hero-side .ov-meta{display:none}.w-play{padding:12px;place-items:start}.w-play span{width:40px;height:40px}.w-play span::after{border-width:8px 0 8px 13px;margin-left:3px}
  .trend{grid-template-columns:1fr}.around{grid-template-columns:1fr}
  .mag-head{flex-wrap:wrap}
  .navbar .navcta{display:none}}
body{margin:0}
.nf>*,.nf-col>*,.nf-side>*,.hero>*{min-width:0}
.navbar .strip{border:0}
.navbar .navgroup{flex-wrap:nowrap;justify-content:flex-start}
.navbar .navgroup a,.navbar .navbtn{padding-inline:9px}
.mag-head .sections{flex:0 1 auto}
.plate-ad .adbanner.promo .ptext{gap:4px}.plate-ad .adbanner.promo .script{display:block;line-height:1.25;font-size:20px}
@media (max-width:760px){.navbar .wrap.strip{padding-inline:0}.navbar .navgroup{padding-inline:8px}
  .plate-ad{width:100%}.plate-ad .adbanner.promo{min-height:0;padding:12px 14px;flex-wrap:wrap;justify-content:center;gap:10px}.plate-ad .adbanner.promo .ptext{justify-items:center;text-align:center}.plate-ad .adbanner.promo .adtext{display:none}
  .plate-ad .adbanner.promo .retro{font-size:19px}.plate-ad .adbanner.promo .script{font-size:16px}.plate-ad .btn{white-space:nowrap}}
@media (max-width:480px){.ticker .tick-tag{display:none}.tick-label{padding:0 10px;font-size:11px}}

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
    "sports": '<path d="M30 14h40v22c0 12-9 22-20 22S30 48 30 36zM30 22H16c0 14 6 20 14 20M70 22h14c0 14-6 20-14 20M50 58v12M36 88h28M40 88l3-18h14l3 18"/>',
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


def post_media(cfg, s):
    """The framed post image, for services that post the feed to Facebook or Instagram."""
    if not s.get("post_image"):
        return ""
    url = e(cfg["site_url"].rstrip("/") + "/" + s["post_image"])
    return f'<enclosure url="{url}" type="image/jpeg" length="0"/>'


def render_feed(cfg, stories):
    site = cfg["site_url"].rstrip("/")
    items = "".join(f"""<item><title>{e(s['title'])}</title><link>{e(page_url(cfg, s))}</link>
<guid isPermaLink="false">{s['id']}</guid><category>{e(SECTION_NAMES[s['section']])}</category>
<pubDate>{email.utils.format_datetime(s['date'])}</pubDate>
<description>{e(s['summary'] + ' (via ' + s['source'] + ')')}</description>{post_media(cfg, s)}</item>""" for s in stories[:50])
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>{e(cfg['site_name'])}</title><link>{e(site)}/</link>
<description>{e(cfg['tagline'])}</description>{items}</channel></rss>"""


FB_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M14 8h3V4h-3c-2.8 0-4 1.7-4 4.3V10H7v4h3v8h4v-8h3l1-4h-4V8.6c0-.4.3-.6 1-.6z"/></svg>'


def logo(name="logo-horizontal.svg"):
    """Inline one of the outlined logo files from brand/ (made by tools/make_logo.py)."""
    return (BRAND / name).read_text().replace('xmlns="http://www.w3.org/2000/svg" ', "")


def jsonld(data):
    """Structured data for search engines; "</" is escaped so text can't close the script tag."""
    return ('<script type="application/ld+json">' + json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
            + "</script>")


# Other ways people type the name, so Google shows "Sunny Mansfield" as the site name in results.
ALT_NAMES = ["SunnyMansfield", "SunnyMansfield.com", "Sunny Mansfield Ohio"]


def mailing_address(cfg, sep=", "):
    """The publisher's mailing address from config.json, on one line (or joined by sep)."""
    return sep.join(cfg.get("mailing_address") or ["Ofgeneration, LLC", "Mansfield, OH"])


def mailing_postal(cfg):
    lines = cfg.get("mailing_address") or []
    m = re.match(r"(.+),\s*([A-Z]{2})\s+(\d{5})", lines[-1]) if len(lines) >= 3 else None
    if not m:
        return {"@type": "PostalAddress", "addressLocality": "Mansfield", "addressRegion": "OH", "addressCountry": "US"}
    return {"@type": "PostalAddress", "streetAddress": ", ".join(lines[1:-1]), "addressLocality": m.group(1),
            "addressRegion": m.group(2), "postalCode": m.group(3), "addressCountry": "US"}


def publisher(cfg):
    site = cfg["site_url"].rstrip("/")
    org = {"@type": "NewsMediaOrganization", "@id": f"{site}/#org", "name": cfg["site_name"], "alternateName": ALT_NAMES,
           "url": f"{site}/", "logo": {"@type": "ImageObject", "url": f"{site}/brand/icon-512.png", "width": 512, "height": 512},
           "image": f"{site}/og.png",
           "address": mailing_postal(cfg),
           "description": "Curated positive local news from Mansfield and Richland County, Ohio, written by the people who live here.",
           "areaServed": [{"@type": "City", "name": "Mansfield, Ohio"}, {"@type": "AdministrativeArea", "name": "Richland County, Ohio"}],
           "publishingPrinciples": f"{site}/rules.html#rules", "correctionsPolicy": f"{site}/about.html#corrections",
           "contactPoint": {"@type": "ContactPoint", "contactType": "editorial", "email": cfg["submit_email"], "url": f"{site}/contact.html"},
           "email": cfg["submit_email"], "parentOrganization": {"@type": "Organization", "name": "Ofgeneration LLC"}}
    same = [u for u in (cfg.get("facebook_page"), cfg.get("instagram")) if u]
    if same:
        org["sameAs"] = same
    return org


# Swap any story photo that fails to load for the sunny drawing. It sits in <head> so it's listening before images load.
# A shelter dog's big photo falls back to its thumbnail; a dog whose photo won't load at all is taken off the page.
PHOTO_FALLBACK = ("""document.addEventListener('error',ev=>{const i=ev.target;if(i.tagName!=='IMG'||!i.closest('.photo'))return;
 if(i.dataset.thumb&&!i.dataset.tried){i.dataset.tried=1;i.src=i.dataset.thumb;return}
 const dog=i.closest('.dogcard,.dogday-wrap');if(dog){dog.remove();return}
 const a=document.createElement('span');a.className='art';a.innerHTML='<svg viewBox="0 0 100 100" aria-hidden="true">"""
                  + ART["good"] + """</svg>';i.replaceWith(a)},true);""")


def google_head(cfg):
    """Search Console's ownership tag, the Google tag for Google Ads and Analytics, and the AdSense script, when config.json has them.
    google_tag can hold several IDs separated by commas, like "G-XXXX, AW-1234"."""
    out = ""
    if cfg.get("google_site_verification"):
        out += f'\n<meta name="google-site-verification" content="{e(cfg["google_site_verification"])}">'
    ids = [x.strip() for x in (cfg.get("google_tag") or "").split(",") if x.strip()]
    if ids:
        configs = "".join(f"gtag('config',{json.dumps(i)});" for i in ids)
        out += (f'\n<script async src="https://www.googletagmanager.com/gtag/js?id={e(ids[0])}"></script>'
                f"<script>window.dataLayer=window.dataLayer||[];function gtag(){{dataLayer.push(arguments)}}"
                f"gtag('js',new Date());{configs}</script>")
    pub = adsense_pub(cfg)
    if pub:
        out += (f'\n<script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-{e(pub)}" '
                f'crossorigin="anonymous"></script>')
    return out


def adsense_pub(cfg):
    """The AdSense publisher ID from config.json ("ca-pub-123..." or "pub-123..."), as "pub-123...", or "" when unset or malformed."""
    raw = (cfg.get("adsense_client") or "").strip()
    m = re.fullmatch(r"(?:ca-)?(pub-\d{10,20})", raw)
    return m.group(1) if m else ""


def head(cfg, title, desc, url, image=None, kind="website", data=None, index=True):
    """kind is the og:type; data is JSON-LD for the page; index=False keeps a page out of search results."""
    image = image or cfg["site_url"].rstrip("/") + "/og.png"
    robots = "max-image-preview:large,max-snippet:-1" if index else "noindex,follow"
    return f"""<title>{e(title)}</title>
<meta name="claude:viewport-fit-sides" content="cover">
<meta name="description" content="{e(desc)}">
<meta name="robots" content="{robots}"><link rel="canonical" href="{e(url)}">
<meta name="theme-color" content="#D9531E">
<meta property="og:type" content="{kind}"><meta property="og:site_name" content="{e(cfg['site_name'])}"><meta property="og:locale" content="en_US">
<meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(url)}"><meta property="og:image" content="{e(image)}">
<meta name="twitter:card" content="summary_large_image"><meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(desc)}"><meta name="twitter:image" content="{e(image)}">
{jsonld(data) if data else ""}
<link rel="icon" type="image/svg+xml" href="/brand/mark.svg"><link rel="icon" type="image/png" sizes="192x192" href="/brand/icon-192.png">
<link rel="apple-touch-icon" href="/brand/apple-touch-icon.png">{google_head(cfg)}
<link rel="alternate" type="application/rss+xml" title="{e(cfg['site_name'])}" href="{e(cfg['site_url'].rstrip('/'))}/feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="{FONTS}">
<style>{CSS}</style>
<script>{PHOTO_FALLBACK}</script>"""


def img_src(src):
    return src if src.startswith("https://") else "/" + src  # sample photos live under /assets


def photo(s):
    src = s.get("image") or ""
    src = src if src.startswith("https://") else "/" + src  # sample photos live under /assets
    inner = (f'<img src="{e(src)}" alt="{e(s["title"])}" loading="lazy" referrerpolicy="no-referrer">' if s.get("image")
             else f'<span class="art"><svg viewBox="0 0 100 100" aria-hidden="true">{ART[s["section"]]}</svg></span>')
    play = '<span class="play"></span>' if s.get("video") else ""
    return f'<a class="photo" href="{e(s["link"])}"{target(s)} tabindex="-1" aria-hidden="true">{inner}{play}</a>'


def kicker(s):
    return f'<span class="caps kicker">{e(SECTION_NAMES[s["section"]])}</span>'


def meta(s):
    by = f'By {e(s["source"])} · <span class="tag">Community</span>' if s.get("community") else e(s["source"])
    return f'<div class="meta"><span>{by}</span><span>{ago(s["date"])}</span></div>'


def share(cfg, s, read=False):
    url = page_url(cfg, s)
    out = (f'<a class="btn fb" href="{e(fb_share(url))}" target="_blank" rel="noopener">{FB_ICON}Share</a>'
           f'<button class="textbtn" type="button" data-copy="{e(url)}">Copy link</button>')
    if read and s.get("community") and s.get("more"):
        out += f'<a class="textbtn" href="{e(s["more"])}" target="_blank" rel="noopener nofollow ugc">Read more</a>'
    elif read and not s.get("community"):
        out += f'<a class="textbtn" href="{e(s["link"])}" target="_blank" rel="noopener">Read at {e(s["source"])}</a>'
    return f'<div class="share">{out}</div>'


def target(s):
    return "" if s.get("community") and "/s/" in s["link"] else ' target="_blank" rel="noopener"'


def hed(s, tag="h3"):
    return f'<{tag} class="hed"><a href="{e(s["link"])}"{target(s)}>{e(s["title"])}</a></{tag}>'


def lead(cfg, s, media=None, tag="h2", full=False):
    dek = f'<p class="dek">{e(s["summary"])}</p>' if s["summary"] else ""
    if full and s.get("body"):  # story page: show the whole submitted article, not the 400-character teaser
        dek = '<div class="dek body">' + "".join(f"<p>{e(p)}</p>" for p in s["body"]) + "</div>"
    elif full and not s.get("community") and s.get("link"):
        # Outside news: the feed only gives us a short excerpt, and we can't republish the whole article,
        # so send readers to the original with a clear button instead of a dead-end "…".
        dek += (f'<p class="readfull"><span>This is a short preview from {e(s["source"])}.</span>'
                f'<a class="btn" href="{e(s["link"])}" target="_blank" rel="noopener">Read the full story at {e(s["source"])} &rarr;</a></p>')
    return f'<article class="lead">{media or photo(s)}{kicker(s)}{hed(s, tag)}{dek}{meta(s)}{share(cfg, s, read=True)}</article>'


def brief(s):
    return f'<article class="brief"><div>{kicker(s)}{hed(s)}<div style="margin-top:8px">{meta(s)}</div></div>{photo(s)}</article>'


def card(cfg, s, front=False):
    dek = f'<p class="dek">{e(s["summary"])}</p>' if s["summary"] else ""
    flag = ' data-front="1"' if front else ""
    return (f'<article class="card" data-sec="{s["section"]}"{flag}>{photo(s)}{kicker(s)}{hed(s)}{dek}{meta(s)}'
            f'{share(cfg, s)}</article>')


def masthead(cfg, site, stories, quote=None, current=""):
    today = datetime.now(timezone.utc).strftime("%A, %B %-d, %Y")
    on = lambda key: ' class="on" aria-current="page"' if key == current else ""
    ad = banner(cfg, "A1").replace('class="wrap adrow"', 'class="adrow"', 1)
    return f"""<header>
<div class="topbar"><div class="wrap topbar-in"><span class="caps">{today}</span>
<span class="caps topbar-mid">{len(stories)} good stories this week · Free · Updated all day</span>
<a class="caps topshare" href="{e(fb_share(site + '/'))}" target="_blank" rel="noopener">{FB_ICON}Share the paper</a></div></div>
<div class="wrap plate">
<a class="logo" href="{e(site)}/" aria-label="{e(cfg['site_name'])} home">{logo("logo-retro.svg")}</a>
<div class="plate-ad">{ad}</div>
</div>
<nav class="navbar" aria-label="Main"><div class="wrap strip caps">
<div class="navgroup"><a href="{e(site)}/"{on("front")}>Front page</a><a href="{e(site)}/events.html">Events</a><a href="{e(site)}/watch.html"{on("watch")}>Watch Mansfield</a>
<a href="{e(site)}/sports.html">Sports</a><a href="{e(site)}/pets.html"{on("pets")}>Tyger Paws</a><a href="{e(site)}/fundraisers.html">Donate</a>
<a href="{e(site)}/free.html">Free Stuff</a><a href="{e(site)}/jobs.html">Jobs</a><a href="{e(site)}/homes.html">Homes</a><a href="{e(site)}/businesses.html">Businesses</a>
<button class="navbtn" type="button" data-about>About</button>
<a href="{e(site)}/rules.html">Join the team</a>{f'<a href="{e(site)}/advertise.html">Advertise</a>' if ads_open(cfg) else ""}</div>
<a class="navcta" href="{e(site)}/submit.html">Submit news</a></div></nav>
</header>
<aside class="about" id="about" role="dialog" aria-labelledby="about-h" hidden>
<button class="close" type="button" data-about-close aria-label="Close">&times;</button>
<span class="script">Welcome, neighbor!</span><h2 class="hed" id="about-h">Why Sunny Mansfield?</h2>
<p><b>Created for the people. Written by the people.</b></p>
<p>Sunny Mansfield is a curated local media outlet that focuses on highlighting the positive side of Mansfield and Richland County: neighbors helping neighbors, new shops opening, students and athletes winning, lives worth celebrating, and the events that bring us together.</p>
<ul><li><b>Curated, not crowded.</b> We hand-pick the good news from local outlets and neighbors, and every story links back to its original source.</li>
<li><b>Uplifting, always.</b> We skip crime, crashes and arguing. There's plenty of that elsewhere.</li>
<li><b>Written with neighbors.</b> Students, business owners, churches and mentors all add stories, and an editor reviews every post.</li>
<li><b>Made to share.</b> Every story has a Share button. One share can make someone's day.</li></ul>
<div class="share"><button class="btn solid" type="button" data-about-close>Start reading</button><a class="textbtn" href="{e(site)}/submit.html">Share good news</a></div>
</aside>{newsletter_pop(cfg)}"""


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
<p>Postcards and snapshots from around town. Got an old photo in a shoebox? <a class="link" href="{e(cfg['site_url'].rstrip('/'))}/submit.html">Send it in</a></p>
</div></div></section>"""


def biz_url(cfg, b):
    return f"{cfg['site_url'].rstrip('/')}/b/{b['slug']}.html"


def civic(cfg, businesses):
    site = cfg["site_url"].rstrip("/")
    more = (f'<a class="slide flyer add" href="{e(site)}/submit.html#spotlight"><span class="flyer-img"><span class="plus">+</span></span>'
            f'<span class="hed">Spotlight your business</span><span class="dek">Free for Richland County businesses</span></a>')
    cards = "".join(biz_card(cfg, b).replace('<article class="card"', '<article class="card slide"', 1) for b in businesses[:12])
    return rail("Shop local", cards + more, f'<a class="link" href="{e(site)}/businesses.html">See all</a>', rid="shops", kind="sec")


def newsletter_pop(cfg):
    """The weekly email sign-up as a pop-up, in the same look as the welcome email: the full-color logo,
    then the pitch and beehiiv's form (people confirm by email before they're added).
    beehiiv's form only loads when the pop-up opens, so pages stay fast."""
    nl = cfg.get("newsletter") or {}
    if not nl.get("page"):
        return ""
    form = (f'<div class="nl-form" data-form="{e(nl["beehiiv_form"])}"></div>' if nl.get("beehiiv_form") else
            f'<a class="btn solid" href="{e(nl["page"])}" target="_blank" rel="noopener">Subscribe</a>')
    day = f'every {e(nl["day"])}' if nl.get("day") else "a week"
    return (f'<dialog class="nlpop" id="nlpop" aria-labelledby="nl-h"><div class="nl2">'
            f'<button class="close" type="button" data-nl-close aria-label="Close">&times;</button>'
            f'<div class="nl-top"><span class="nl-logo" role="img" aria-label="{e(cfg["site_name"])}">{logo("logo-retro.svg")}</span>'
            f'<span class="script">Good news is on the way</span></div>'
            f'<div class="nl-body"><h2 class="nl-hed" id="nl-h" tabindex="-1" autofocus>Good news, straight to your inbox.</h2>'
            f'<p class="nl-sub">One short, free email {day} with the best of Mansfield and Richland County.</p>'
            f'<ul class="nl-list"><li><span class="nl-ico" aria-hidden="true">&#9728;</span>The week\'s best good news</li>'
            f'<li><span class="nl-ico" aria-hidden="true">&#9733;</span>Things to do this weekend</li></ul>'
            f'{form}<p class="nl-fine">We\'ll email you once to confirm. Unsubscribe anytime.</p>'
            f'<button class="textbtn nl-no" type="button" data-nl-close>No thanks</button></div></div></dialog>')


NL_ORANGE, NL_INK, NL_CREAM, NL_MUTED = "#D9531E", "#2E1A10", "#FBF6EC", "#6B5A4E"


def make_newsletter_issue(cfg, out, stories, flyers=(), funds=(), listings=(), jobs=(), birthdays=(), dogs=()):
    """This week's newsletter, built from the site's own sections, in two forms:
    newsletter/issue.html, a branded email-style page to read or paste into beehiiv, and
    newsletter/issue.json, the same issue as beehiiv editor blocks so it can be loaded into a draft in one step.
    Empty sections are left out."""
    site = cfg["site_url"].rstrip("/")
    canon = (cfg.get("canonical_url") or site).rstrip("/")
    absu = lambda u: u if not u or u.startswith("http") else canon + ("" if u.startswith("/") else "/") + u
    now = datetime.now(timezone.utc)
    week = now - timedelta(days=7)
    today = eastern().date()
    utm = lambda u: u + ("&" if "?" in u else "?") + "utm_source=newsletter" if u.startswith(canon) else u
    short = lambda t, n=170: t if len(t) <= n else t[:n - 10].rsplit(" ", 1)[0] + "…"
    sections = []  # (heading, [items]); item = dict(title, url, text, image, kicker, button)

    news = [x for x in stories if x["section"] != "life" and x["date"] >= week]
    news.sort(key=lambda x: (not x.get("image"), -x["date"].timestamp()))
    if news:
        sections.append(("This week's good news", [
            {"title": x["title"], "url": utm(canon + f"/s/{x['id']}.html"), "text": short(" ".join((x.get("summary") or "").split())),
             "image": absu(x.get("image") or "") if i == 0 else "", "kicker": x.get("source") or ""} for i, x in enumerate(news[:5])]))
    dayof = lambda f: f["day"].date() if isinstance(f["day"], datetime) else f["day"]
    called_off = lambda f: re.match(r"\W*(cancel|postpone|call(ed)? off)", (f.get("title") or "") + " " + (f.get("summary") or ""), re.I) or \
        re.match(r"\W*(cancel|postpone)", f.get("summary") or "", re.I)
    soon = sorted((f for f in flyers if f.get("day") and today <= dayof(f) <= today + timedelta(days=9) and not called_off(f)), key=dayof)
    if soon:
        sections.append(("Coming up around town", [
            {"title": f["title"], "url": utm(canon + event_url(f)), "kicker": f["day"].strftime("%a, %b %-d") + (" · " + f["town"] if f.get("town") else ""),
             "text": short(" ".join((f.get("summary") or "").split()), 140)} for f in soon[:5]]))
    dog = dog_of_the_day(dogs)
    if dog:
        about = " · ".join(v for v in (dog.get("breed"), dog.get("age"), dog.get("sex")) if v)
        sections.append(("Tyger Paws: dog of the week", [
            {"title": f"Meet {dog['name']}", "url": dog["url"], "image": (dog.get("big") or dog.get("image") or "").replace("h_1000", "h_600"),
             "text": (about + ". " if about else "") + f"{dog['name']} is waiting at the {(cfg.get('shelter') or {}).get('name', 'Richland County Dog Shelter')} for a family.",
             "button": f"Meet {dog['name']}"}]))
    if funds:
        sections.append(("Neighbors helping neighbors", [
            {"title": f["title"], "url": utm(canon + fund_url(f)), "kicker": f.get("town") or "Richland County",
             "text": short(" ".join((f.get("summary") or "").split()), 140)} for f in list(funds)[:3]]))
    fresh = [x for x in listings if x["date"] >= week][:4]
    if fresh:
        sections.append(("Free stuff", [
            {"title": x["title"], "url": utm(canon + f"/free.html#l-{x['id']}"), "kicker": x.get("town") or "Richland County",
             "text": short(x["summary"], 120)} for x in fresh]))
    hiring = [j for j in jobs if j["date"] >= week][:4]
    if hiring:
        sections.append(("Now hiring", [
            {"title": j["title"], "url": utm(canon + f"/jobs/{j['id']}.html"),
             "kicker": " · ".join(v for v in (j["employer"], j.get("pay"), j.get("town")) if v), "text": ""} for j in hiring]))
    cakes = [b for b in birthdays][:8]
    if cakes:
        sections.append(("Birthday shout-outs", [
            {"title": "Happy birthday, " + ", ".join(b["name"] for b in cakes[:-1]) + (" and " if len(cakes) > 1 else "") + cakes[-1]["name"] + "!",
             "url": utm(canon + "/birthdays.html"), "text": "Want to surprise someone? Send a free birthday shout-out.", "button": "Send a shout-out"}]))

    header = canon + "/brand/email-weekly.png"
    week_of = (today - timedelta(days=today.weekday())).strftime("%B %-d, %Y")
    intro = "Hi neighbor! Here's the good news from around Mansfield and Richland County this week."
    addr = mailing_address(cfg)

    # 1. The email-style page (inline styles and tables, so it also pastes cleanly)
    F = "font-family:Georgia,'Times New Roman',serif;"
    S = "font-family:Helvetica,Arial,sans-serif;"
    btn = lambda label, url: (f'<p style="margin:14px 0 4px"><a href="{e(url)}" style="{S}display:inline-block;background:{NL_ORANGE};color:#fff;'
                              f'text-decoration:none;font-weight:700;font-size:15px;padding:12px 22px">{e(label)}</a></p>')
    rows = []
    for head, items in sections:
        rows.append(f'<h2 style="{S}font-size:13px;letter-spacing:2px;text-transform:uppercase;color:{NL_ORANGE};margin:34px 0 4px;'
                    f'padding-top:18px;border-top:2px solid {NL_INK}">{e(head)}</h2>')
        for it in items:
            img = (f'<a href="{e(it["url"])}"><img src="{e(it["image"])}" alt="{e(it["title"])}" width="560" '
                   f'style="display:block;width:100%;max-width:560px;height:auto;margin:12px 0 4px;border:0"></a>') if it.get("image") else ""
            kick = f'<p style="{S}font-size:12px;letter-spacing:1px;text-transform:uppercase;color:{NL_MUTED};margin:14px 0 2px">{e(it["kicker"])}</p>' if it.get("kicker") else ""
            text = f'<p style="{S}font-size:15px;line-height:1.55;color:{NL_INK};margin:4px 0 0">{e(it["text"])}</p>' if it.get("text") else ""
            title = f'<h3 style="{F}font-size:20px;line-height:1.25;margin:{"6px" if kick else "14px"} 0 0"><a href="{e(it["url"])}" style="color:{NL_INK};text-decoration:none">{e(it["title"])}</a></h3>'
            rows.append(img + kick + title + text + (btn(it["button"], it["url"]) if it.get("button") else ""))
    body = "\n".join(rows) or f'<p style="{S}">No new posts this week yet.</p>'
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex"><title>{e(cfg["site_name"])} weekly · Week of {e(week_of)}</title></head>
<body style="margin:0;background:#efe7da"><table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;width:100%;background:{NL_CREAM};border:2px solid {NL_INK}">
<tr><td><a href="{e(utm(canon + '/'))}"><img src="{e(header)}" alt="{e(cfg['site_name'])}: good news, every week" width="600" style="display:block;width:100%;height:auto;border:0"></a></td></tr>
<tr><td style="padding:8px 24px 28px">
<p style="{S}font-size:12px;letter-spacing:2px;text-transform:uppercase;color:{NL_MUTED};margin:18px 0 6px">Week of {e(week_of)}</p>
<p style="{S}font-size:16px;line-height:1.55;color:{NL_INK};margin:0">{e(intro)}</p>
{body}
<div style="margin-top:34px;padding:22px;background:{NL_ORANGE};color:#fff;text-align:center">
<p style="{F}font-size:22px;margin:0 0 6px;color:#fff">Got good news?</p>
<p style="{S}font-size:15px;margin:0 0 14px;color:#fff">Tell us about a neighbor, a new shop or a big win. It's free.</p>
<a href="{e(utm(canon + '/submit.html'))}" style="{S}display:inline-block;background:#fff;color:{NL_ORANGE};font-weight:700;text-decoration:none;padding:12px 22px">Share good news</a></div>
<p style="{S}font-size:12px;line-height:1.6;color:{NL_MUTED};text-align:center;margin:26px 0 0">{e(cfg["site_name"])} · {e(addr)}</p>
</td></tr></table></td></tr></table></body></html>"""

    # 2. The same issue as beehiiv editor blocks
    txt = lambda t, **m: {"type": "text", "text": t, **({"marks": [{"type": k, "attrs": v} if v else {"type": k} for k, v in m.items()]} if m else {})}
    para = lambda *c: {"type": "paragraph", "content": list(c)} if c else {"type": "paragraph"}
    image = lambda src, url, alt: {"type": "imageBlock", "attrs": {"src": src, "url": url, "alt": alt, "width": "100%", "align": "center",
                                                                   "isUploaded": False, "allowExternal": True, "source": "url"},
                                   "content": [{"type": "figcaption"}]}
    button = lambda label, url: {"type": "button", "attrs": {"href": url, "target": "_blank", "alignment": "center", "size": "large", "isCustom": True,
                                                             "customBackgroundColor": NL_ORANGE, "customTextColor": "#FFFFFF"},
                                 "content": [txt(label)]}
    doc = [image(header, utm(canon + "/"), cfg["site_name"] + ": good news, every week"),
           para(txt(f"Week of {week_of}", bold=None)), para(txt(intro))]
    for head, items in sections:
        doc.append({"type": "horizontalRule"})
        doc.append({"type": "heading", "attrs": {"level": 2}, "content": [txt(head)]})
        for it in items:
            if it.get("image"):
                doc.append(image(it["image"], it["url"], it["title"]))
            if it.get("kicker"):
                doc.append(para(txt(it["kicker"], italic=None)))
            doc.append({"type": "heading", "attrs": {"level": 3}, "content": [txt(it["title"], link={"href": it["url"], "target": "_blank"})]})
            if it.get("text"):
                doc.append(para(txt(it["text"])))
            if it.get("button"):
                doc.append(button(it["button"], it["url"]))
    doc += [{"type": "horizontalRule"}, {"type": "heading", "attrs": {"level": 2, "textAlign": "center"}, "content": [txt("Got good news?")]},
            {"type": "paragraph", "attrs": {"textAlign": "center"}, "content": [txt("Tell us about a neighbor, a new shop or a big win. It's free.")]},
            button("Share good news", utm(canon + "/submit.html"))]
    bits = [{"This week's good news": "The week's good news", "Coming up around town": "things to do",
             "Neighbors helping neighbors": "neighbors helping neighbors", "Now hiring": "local jobs"}.get(h, "") for h, _ in sections]
    if dog:
        bits.insert(2, f"{dog['name']} from Tyger Paws")
    bits = [b for b in bits if b]
    preview = (", ".join(bits[:3]) + " and more.") if bits else intro
    meta = {"subject": f"Good news from Mansfield: week of {week_of.rsplit(',', 1)[0]}", "preview": preview[:1].upper() + preview[1:], "week_of": week_of,
            "sections": [h for h, _ in sections], "doc": {"type": "doc", "content": doc}}
    folder = out / "newsletter"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "issue.html").write_text(html)
    (folder / "issue.json").write_text(json.dumps(meta, ensure_ascii=False, default=str))
    return meta


def submit_band(cfg, site):
    return f"""<section class="wrap" id="submit"><div class="candy"><div class="candy-in submit">
<div><span class="script">Hey neighbor!</span><h2 class="retro sm">Got good news?</h2>
<p>Ribbon cuttings, 100th birthdays, block parties, garage sales and kids doing great things. Neighbors, students, shops and mentors all post here.</p></div>
<div class="share" id="subscribe"><a class="btn solid" href="{e(site)}/submit.html">Submit good news</a><a class="btn" href="{e(site)}/birthdays.html#form">Birthday shout-out</a><a class="btn" href="{e(site)}/feed.xml" target="_blank" rel="noopener">Subscribe by RSS</a></div>
</div></div></section>"""


def report_link(cfg, s=None):
    """A mailto that names the post, so a takedown request reaches an editor with what they need."""
    subject = f"Report or remove: {s['title']}" if s else "Report or remove a post"
    body = f"Post: {s['link']}\n\nWhat should we fix or take down, and why?\n" if s else "Link to the post:\n\nWhat should we fix or take down, and why?\n"
    return f"mailto:{cfg['submit_email']}?subject={urllib.parse.quote(subject[:150])}&body={urllib.parse.quote(body)}"


def follow_links(cfg):
    """A "Follow us" line in the footer for the social pages config.json lists; nothing when none are set."""
    links = [(n, cfg.get(k)) for n, k in (("Facebook", "facebook_page"), ("Instagram", "instagram")) if cfg.get(k)]
    if not links:
        return ""
    return ('<nav class="legal follow">Follow us on ' +
            "".join(f'<a href="{e(u)}" rel="me noopener" target="_blank">{e(n)}</a>' for n, u in links) + "</nav>\n")


def newsletter_link(cfg):
    page = (cfg.get("newsletter") or {}).get("page")
    return f'<a href="{e(page)}" target="_blank" rel="noopener" data-nl>Email newsletter</a>' if page else ""


def footer(cfg):
    site = cfg["site_url"].rstrip("/")
    return f"""<footer>{skyline().replace('class="skyline"', 'class="sky"')}<div class="wrap">
<span class="logo">{logo("logo-retro-reverse.svg")}</span>
<p>We share headlines, short summaries and source photos, and every story links to the original reporting.
Please read and support Richland Source, the Mansfield News Journal and our other local outlets.</p>
<span class="caps">Updated {datetime.now(timezone.utc).strftime('%b %-d, %Y')}</span></div>
{sponsor_strip(cfg)}
<div class="wrap credit"><span class="script">Made in Mansfield</span> <span>by Ofgeneration LLC</span>
{follow_links(cfg)}<nav class="legal towns">Good news in {" ".join(f'<a href="{e(town_url(cfg, t))}">{e(t)}</a>' for t, _ in TOWNS)}</nav>
<nav class="legal"><a href="{e(site)}/about.html">About</a><a href="{e(site)}/contact.html">Contact</a><a href="{e(site)}/privacy.html">Privacy</a><a href="{e(site)}/terms.html">Terms</a><a href="{e(site)}/rules.html#rules">Sunny rules</a><a href="{e(site)}/birthdays.html">Birthday shout-outs</a>{newsletter_link(cfg)}<a href="{e(report_link(cfg))}">Report or remove a post</a></nav>
<small>&copy; {datetime.now(timezone.utc).year} Ofgeneration LLC. All rights reserved. {e(cfg['site_name'])} is a publication of Ofgeneration LLC.
Stories from other outlets belong to their publishers.</small>
<small class="addr">{e(mailing_address(cfg, " · "))}</small></div></footer>"""


SCRIPT = ("""<script>
(()=>{const PAGE=9;const tabs=[...document.querySelectorAll('.sections .tab')];
const cards=[...document.querySelectorAll('.grid .card')];const empty=document.querySelector('.grid .empty');
const more=document.getElementById('more');const label=document.querySelector('#latest .label');
let current='all',limit=PAGE;
function store(k,v){try{if(v===undefined)return localStorage.getItem(k);localStorage.setItem(k,v)}catch(_){return null}}
const shelves=document.getElementById('shelves');const latest=document.getElementById('latest');
function render(){let n=0,shown=0;if(shelves&&latest){const all=current==='all';shelves.hidden=!all;latest.hidden=all}
 cards.forEach(c=>{const match=current==='all'?!c.dataset.front:c.dataset.sec===current;n+=match;
  const on=match&&shown<limit;shown+=on;c.hidden=!on});
 if(empty)empty.hidden=n>0;if(more){more.hidden=shown>=n;more.textContent=`Show ${Math.min(PAGE,n-shown)} more stories`}}
function show(sec,scroll){sec=(sec||'').toLowerCase();const t=tabs.find(x=>x.dataset.sec===sec)||tabs[0];if(!t)return;
 current=t.dataset.sec;limit=PAGE;tabs.forEach(x=>x.setAttribute('aria-pressed',x===t));
 if(label)label.firstChild.textContent=current==='all'?'More good news':t.firstChild.textContent.trim();render();
 if(scroll)document.getElementById('latest').scrollIntoView({behavior:'smooth',block:'start'})}
tabs.forEach(t=>t.addEventListener('click',()=>{show(t.dataset.sec,true);history.replaceState(null,'','#'+t.dataset.sec)}));
if(more)more.addEventListener('click',()=>{limit+=PAGE;render()});
document.querySelectorAll('[data-show]').forEach(b=>b.addEventListener('click',()=>{show(b.dataset.show,true);history.replaceState(null,'','#'+b.dataset.show)}));
document.querySelectorAll('.railbox').forEach(box=>{const r=box.querySelector('.rail');const bs=box.querySelectorAll('.arrow');
 const upd=()=>{bs[0].disabled=r.scrollLeft<4;bs[1].disabled=r.scrollLeft+r.clientWidth>=r.scrollWidth-4};
 bs.forEach(b=>b.addEventListener('click',()=>r.scrollBy({left:+b.dataset.dir*r.clientWidth*.9,behavior:'smooth'})));
 r.addEventListener('scroll',upd,{passive:true});addEventListener('resize',upd);upd();
 // Gentle auto-scroll: one card every few seconds while the shelf is on screen, paused while someone is using it.
 if(matchMedia('(prefers-reduced-motion: reduce)').matches||r.scrollWidth<=r.clientWidth+4)return;
 let hold=false,seen=false,rest=0;const stop=()=>{rest=Date.now()+12000};
 ['pointerenter','focusin','touchstart','wheel'].forEach(ev=>box.addEventListener(ev,()=>{hold=true;stop()},{passive:true}));
 ['pointerleave','focusout'].forEach(ev=>box.addEventListener(ev,()=>{hold=false},{passive:true}));bs.forEach(b=>b.addEventListener('click',stop));
 new IntersectionObserver(es=>{seen=es[0].isIntersecting},{threshold:.4}).observe(r);
 setInterval(()=>{if(hold||!seen||Date.now()<rest||document.hidden)return;const c=r.firstElementChild;if(!c)return;
  const step=c.getBoundingClientRect().width+parseFloat(getComputedStyle(r).columnGap||0);
  r.scrollTo({left:r.scrollLeft+r.clientWidth>=r.scrollWidth-4?0:r.scrollLeft+step,behavior:'smooth'})},4500)});
const h=decodeURIComponent(location.hash.slice(1));show(tabs.some(x=>x.dataset.sec===h)?h:'all',false);
const about=document.getElementById('about');
if(about){const seen=store('aboutSeen');if(!seen&&!/admin\.html/.test(location.pathname))setTimeout(()=>about.hidden=false,4000);
 document.addEventListener('click',ev=>{if(ev.target.closest('[data-about]')){about.hidden=false;about.querySelector('.btn').focus()}
  if(ev.target.closest('[data-about-close]')){about.hidden=true;store('aboutSeen','1')}});
 document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&!about.hidden){about.hidden=true;store('aboutSeen','1')}})}
const nlpop=document.getElementById('nlpop');
if(nlpop&&nlpop.showModal){const box=nlpop.querySelector('.nl-form');const DAY=864e5;
 const load=()=>{if(!box||box.dataset.on)return;box.dataset.on=1;const sc=document.createElement('script');sc.async=true;
  sc.src='https://subscribe-forms.beehiiv.com/v3/loader.js';sc.dataset.beehiivForm=box.dataset.form;box.appendChild(sc)};
 const open=()=>{if(nlpop.open)return;load();nlpop.showModal();document.getElementById('nl-h').focus()};
 const snooze=days=>store('nlSnooze',String(Date.now()+days*DAY));
 if(/[?&]subscribed=1/.test(location.search)){snooze(3650);setTimeout(()=>say("You're subscribed! Watch your inbox for good news."),300)}
 document.addEventListener('click',ev=>{const t=ev.target.closest('[data-nl]');if(t){ev.preventDefault();open();return}
  if(ev.target.closest('[data-nl-close]')||ev.target===nlpop){nlpop.close();snooze(14)}});
 nlpop.addEventListener('cancel',()=>snooze(14));
 // Typing in beehiiv's form moves focus into its frame: count that as signed up (or trying), and don't ask again for a while.
 addEventListener('blur',()=>{if(nlpop.open&&document.activeElement&&document.activeElement.tagName==='IFRAME')snooze(60)});
 // Ask once on its own, after 20 seconds or halfway down the page. New visitors see the About pop-out first,
 // so for them the clock starts when they close it (15 seconds later, or halfway down the page).
 const due=!(+store('nlSnooze')>Date.now())&&!/admin\.html/.test(location.pathname);
 if(due){let done=false,t0=store('aboutSeen')?Date.now()-5000:0;
  const go=()=>{if(done||!t0||document.querySelector('dialog[open]')||(about&&!about.hidden))return;done=true;open()};
  const met=()=>{if(t0)return;t0=Date.now();setTimeout(go,15000)};
  document.addEventListener('click',ev=>{if(ev.target.closest('[data-about-close]'))met()});
  document.addEventListener('keydown',ev=>{if(ev.key==='Escape'&&about&&!about.hidden)met()},true);
  if(t0)setTimeout(go,20000);
  addEventListener('scroll',()=>{if(t0&&Date.now()-t0>2000&&scrollY>(document.documentElement.scrollHeight-innerHeight)*.5)go()},{passive:true})}}
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
</script>""")


ARROWS = ('<div class="rail-nav"><button class="arrow" type="button" data-dir="-1" aria-label="Back">'
          '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M15 5l-7 7 7 7"/></svg></button>'
          '<button class="arrow" type="button" data-dir="1" aria-label="Next">'
          '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 5l7 7-7 7"/></svg></button></div>')


FIRST_POST = {  # what an empty spot asks for: label, where the form is, button, examples
    "good": ("Good News", "submit.html", "Share good news", "A birthday, a big win, a neighbor who went above and beyond."),
    "sports": ("Sports", "submit.html#athlete", "Spotlight an athlete", "A signing day, a personal best, a team that never quit."),
    "hoods": ("Neighborhoods", "submit.html", "Share your street", "A block party, a cleanup, a street that came together."),
    "biz": ("Local Business", "submit.html#spotlight", "Spotlight a business", "A grand opening, an anniversary, a new hire you're proud of."),
    "sales": ("Garage Sales", "submit.html", "Post your sale", "Garage, yard, porch and rummage sales around Richland County."),
    "seniors": ("Seniors", "submit.html", "Share a story", "A 100th birthday, a retiree who still volunteers, a veteran's story."),
    "free": ("Free Stuff", "submit.html#free", "Give something away", "A dresser you don't need, outgrown kids' clothes, extra garden tomatoes. Free only."),
    "homes": ("Homes", "submit.html#home", "List a home free", "A house or apartment for rent or sale in Richland County. Price up front, replies stay private."),
    "jobs": ("Now hiring", "submit.html#job", "Post a job free", "Hiring at your shop, church, school or nonprofit? Neighbors are looking."),
    "funds": ("Fundraisers", "submit.html#fundraiser", "Share a fundraiser", "Medical bills, team trips, fire recovery and special causes."),
    "events": ("Local events", "submit.html#flyer", "Post your flyer", "Concerts, fish fries, festivals, games and church suppers."),
    "birthday": ("Birthdays", "birthdays.html#form", "Send a shout-out", "Know someone celebrating? We'll wish them a happy birthday here and on Facebook, free."),
    "pets": ("Tyger Paws", "submit.html", "Send your adoption story", "Adopted a dog from the Richland County shelter? Send us your adoption story and a photo."),
    "watch": ("Watch Mansfield", "submit.html", "Send us your video", "A podcast, a local show, a highlight reel. Mansfield made, Mansfield watched."),
}


def first_post(cfg, key, block=False):
    """An empty spot: the Sunny Mansfield logo and a 'Be the first to post!' button to the right form."""
    site = cfg["site_url"].rstrip("/")
    what, href, cta, examples = FIRST_POST[key]
    inner = (f'<span class="first-logo"><img src="/brand/logo-retro.svg" alt="Sunny Mansfield" width="220" height="80" loading="lazy"></span>'
             f'<span class="caps kicker">{e(what)}</span><span class="hed">Be the first to post!</span>'
             f'<span class="dek">{e(examples)}</span><span class="btn solid sm">{e(cta)}</span>')
    if block:
        return f'<div class="first-wrap"><a class="first" href="{e(site)}/{href}">{inner}</a></div>'
    return f'<a class="slide first" href="{e(site)}/{href}">{inner}</a>'


def rail(title, items, more="", rid="", kind=""):
    """A swipeable row of cards with arrow buttons, like a magazine shelf."""
    head = f'<div class="rail-head"><h2 class="label">{title}</h2>{more}{ARROWS}</div>'
    ident = f' id="{rid}"' if rid else ""
    return (f'<section class="wrap railbox {kind}"{ident}>{head}'
            f'<div class="rail" tabindex="0">{items}</div></section>')


def event_url(f):
    return f"/e/{f['id']}.html"  # site-relative, so rebase() and sub-folder sites keep working


def flyer_card(f):
    """A flyer on a shelf: the picture, date and town, title and a three-line teaser. It all opens the event's page."""
    when = f["day"].strftime("%a, %b %-d") if f.get("day") else "Coming up"
    img = (f'<img src="{e(img_src(f["image"]))}" alt="Flyer: {e(f["title"])}" loading="lazy" referrerpolicy="no-referrer">'
           if f.get("image") else f'<span class="art"><svg viewBox="0 0 100 100" aria-hidden="true">{ART["good"]}</svg></span>')
    dek = f'<p class="dek">{e(" ".join(f["summary"].split()))}</p>' if f.get("summary") else ""
    page = event_url(f)
    return (f'<article class="slide flyer"><a class="flyer-img" href="{e(page)}" tabindex="-1" aria-hidden="true">{img}</a>'
            f'<span class="caps kicker">{e(when)}{" · " + e(f["town"]) if f.get("town") else ""}</span>'
            f'<h3 class="hed"><a href="{e(page)}">{e(f["title"])}</a></h3>{dek}'
            f'<a class="caps more-link" href="{e(page)}">Event details <span aria-hidden="true">&rsaquo;</span></a></article>')


def listing_card(x, cfg):
    img = (f'<img src="{e(img_src(x["image"]))}" alt="" loading="lazy" referrerpolicy="no-referrer">' if x.get("image")
           else f'<span class="art"><svg viewBox="0 0 100 100" aria-hidden="true">{ART["sales"]}</svg></span>')
    price = '<span class="price">Free</span>'
    how = reply_button(cfg, x, "Message the giver")
    cat = x.get("cat") or "other"
    search = e(f'{x["title"]} {x["summary"]} {x.get("town") or ""}'.lower())
    return (f'<article class="slide listing" id="l-{x["id"]}" data-cat="{cat}" data-q="{search}"><span class="photo">{img}{price}</span>'
            f'<span class="caps kicker">{e(MARKET_CAT_NAMES.get(cat, ""))} · {e(x.get("town") or "Richland County")} · {ago(x["date"])}</span>'
            f'<h3 class="hed">{e(x["title"])}</h3><p class="dek">{e(x["summary"])}</p><div class="share">{how}</div></article>')


def reply_button(cfg, x, label):
    """Opens the private reply box. The poster's email never reaches the page; the reply function looks it up."""
    if not x.get("pid") or not backend_on(cfg):
        return f'<span class="muted">Replies open soon</span>'
    return (f'<button class="btn solid sm" type="button" data-reply="{e(x["pid"])}" data-title="{e(x["title"])}">'
            f'{label}</button>')


def job_url(cfg, j):
    return f'{cfg["site_url"].rstrip("/")}/jobs/{j["id"]}.html'


def job_card(cfg, j, slide=False):
    facts = " · ".join(e(v) for v in (j.get("type"), j.get("town") or "Richland County", ago(j["date"])) if v)
    pay = f'<span class="pay">{e(j["pay"])}</span>' if j.get("pay") else ""
    dek = j["summary"] if len(j["summary"]) < 180 else j["summary"][:170].rsplit(" ", 1)[0] + "…"
    search = e(f'{j["title"]} {j["employer"]} {j["summary"]} {j.get("town") or ""}'.lower())
    return (f'<article class="{"slide " if slide else ""}job" id="j-{j["id"]}" data-type="{e(slug(j.get("type") or "other"))}" data-q="{search}">'
            f'<span class="caps kicker">{facts}</span><h3 class="hed"><a href="{e(job_url(cfg, j))}">{e(j["title"])}</a></h3>'
            f'<p class="employer">{e(j["employer"])}{pay}</p><p class="dek">{e(dek)}</p>'
            f'<div class="share">{reply_button(cfg, j, "Reply privately")}<a class="btn sm" href="{e(job_url(cfg, j))}">Details</a></div></article>')


REPLY_NOTE = ("Your message, name and email go straight to the person who posted. We never show their email to you or yours "
              "to anyone else, and we keep only a scrambled record for 30 days to stop spam.")


def reply_box(cfg):
    """The private reply form for Free Stuff and jobs. It calls the "reply" Supabase function, which emails the poster."""
    if not backend_on(cfg):
        return ""
    conf = json.dumps({"url": cfg["supabase_url"], "key": cfg["supabase_anon_key"]})
    site = cfg["site_url"].rstrip("/")
    return f"""<dialog class="replybox" id="reply" aria-labelledby="reply-h"><form class="sunny" id="reply-form" novalidate>
<button class="close" type="button" data-reply-close aria-label="Close">&times;</button>
<span class="caps kicker">Private reply</span><h2 class="hed wide" id="reply-h">Reply</h2>
<label class="fld"><span class="caps">Your name *</span><input name="name" maxlength="100" required autocomplete="name"></label>
<label class="fld"><span class="caps">Your email *</span><input name="email" type="email" maxlength="200" required autocomplete="email"><span class="hint">They answer you here.</span></label>
<label class="fld wide"><span class="caps">Message *</span><textarea name="message" rows="5" minlength="10" maxlength="3000" required></textarea></label>
<label class="hp" aria-hidden="true">Leave this empty<input name="hp" tabindex="-1" autocomplete="off"></label>
<p class="hint wide">{e(REPLY_NOTE)} Never send money, gift cards, your Social Security number or bank details to someone you haven't met. <a class="link" href="{e(site)}/privacy.html" target="_blank">Privacy</a></p>
<div class="wide submitrow"><button class="btn solid" type="submit">Send</button><span class="formmsg" role="status" aria-live="polite"></span></div>
</form></dialog>
<script src="{SB_JS}"></script><script>
(()=>{{const C={conf};const sb=supabase.createClient(C.url,C.key);const d=document.getElementById('reply');const f=document.getElementById('reply-form');
const msg=f.querySelector('.formmsg');let post=null;
document.addEventListener('click',ev=>{{const b=ev.target.closest('[data-reply]');if(b){{post=b.dataset.reply;
 document.getElementById('reply-h').textContent='Reply about: '+b.dataset.title;msg.textContent='';f.hidden=false;d.showModal();f.name.focus();return}}
 if(ev.target.closest('[data-reply-close]')||ev.target===d)d.close()}});
f.addEventListener('submit',async ev=>{{ev.preventDefault();if(!f.reportValidity())return;const btn=f.querySelector('[type=submit]');
 if(f.hp.value){{d.close();return}}btn.disabled=true;msg.textContent='Sending…';
 try{{const {{data,error}}=await sb.functions.invoke('reply',{{body:{{post_id:post,name:f.name.value.trim(),email:f.email.value.trim(),message:f.message.value.trim()}}}});
  if(error){{let t='';try{{t=(await error.context.json()).error}}catch(_){{}}throw new Error(t||error.message)}}
  f.reset();msg.textContent='Sent! Watch your email for their answer.'}}
 catch(err){{msg.textContent='Could not send: '+(err.message||err)}}finally{{btn.disabled=false}}}});}})();
</script>"""


# Every place an ad can go. The Advertise page and the media kit read this list.
# config.json "ads" entries name a spot: {"spot": "A1", "name": ..., "image": ..., "link": ..., "until": "2026-12-31"}
AD_SPOTS = [
    {"spot": "A1", "name": "Top banner", "where": "Every page, right under the menu", "kind": "banner",
     "size": "1456 x 180 px (shows at 728 x 90), plus 640 x 200 px for phones", "price": "$150 to $300 a month"},
    {"spot": "A2", "name": "Front page carousel card", "where": "Inside the story carousels on the front page", "kind": "card",
     "size": "1200 x 800 px photo, a name and one short line", "price": "$50 to $100 a month"},
    {"spot": "A3", "name": "Section sponsor", "where": "“Presented by” on one section shelf, like Sports or Seniors", "kind": "logo",
     "size": "Logo, 600 x 200 px PNG with a see-through background", "price": "$75 to $150 a month"},
    {"spot": "A4", "name": "Story page banner", "where": "Under the story on every story page", "kind": "banner",
     "size": "1456 x 180 px, plus 640 x 200 px for phones", "price": "$75 to $150 a month"},
    {"spot": "A5", "name": "Sports page sponsor", "where": "Top of the Sports page, above athlete spotlights and brackets", "kind": "banner",
     "size": "1456 x 180 px, plus 640 x 200 px for phones", "price": "$75 to $150 a month"},
    {"spot": "A6", "name": "Events sponsor", "where": "A flyer-sized card in the Local events carousel", "kind": "flyer",
     "size": "1080 x 1350 px (4:5), like a flyer", "price": "$50 to $100 a month"},
    {"spot": "A7", "name": "Free Stuff sponsor", "where": "On the Free Stuff page and its front page carousel", "kind": "card",
     "size": "1200 x 800 px photo, a name and one short line", "price": "$25 to $50 a month"},
    {"spot": "S1", "name": "Sponsored social post", "where": "One post on our Facebook and Instagram, marked as a paid partnership", "kind": "social",
     "size": "1080 x 1350 px image or we design it from your photos", "price": "$25 to $75 a post"},
]
AD_SPOT = {s["spot"]: s for s in AD_SPOTS}
# While ads are paused, each spot keeps its size and shows one of our own promos instead.
# (script, headline, line, link, button). Banners use their spot; cards take turns through PROMO_CARDS.
PROMO = {
    "A1": ("Happy birthday!", "Birthday shout-outs", "Free! We wish them a happy birthday on the site and Facebook.", "birthdays.html#form", "Send a shout-out"),
    "A4": ("Pass it on!", "Know a neighbor doing great things?", "Tell us in a few lines and add a photo.", "submit.html", "Submit good news"),
    "A5": ("Game on!", "Spotlight an athlete", "A big game, a personal best, a kid who never quits.", "submit.html#athlete", "Spotlight an athlete"),
}
PROMO_CARDS = [
    ("Free to a good home", "Give away free stuff", "Neighbors message you privately.", "submit.html#free", "Give something away"),
    ("Now hiring?", "Post a job free", "Applicants reply without seeing your email.", "submit.html#job", "Post a job"),
    ("Shop local!", "Get a free business spotlight", "Your own page with your photo and story.", "submit.html#spotlight", "Get a spotlight"),
    ("What's happening?", "Post your event flyer", "It rides the front page until the big day.", "submit.html#flyer", "Post a flyer"),
]


def promo_banner(cfg, spot):
    site = cfg["site_url"].rstrip("/")
    script, title, line, href, cta = PROMO[spot]
    hot = " hot" if spot == "A1" else ""  # the top banner (birthday shout-outs) is solid orange
    return (f'<aside class="wrap adrow" aria-label="From Sunny Mansfield"><a class="adbanner promo{hot}" href="{e(site)}/{href}">'
            f'<span class="ptext"><span class="script">{e(script)}</span><span class="retro">{e(title)}</span>'
            f'<span class="adtext">{e(line)}</span></span><span class="btn solid sm">{e(cta)}</span></a></aside>')


def promo_card(cfg, i, flyer=False):
    site = cfg["site_url"].rstrip("/")
    script, title, line, href, cta = PROMO_CARDS[i % len(PROMO_CARDS)]
    if flyer:
        return (f'<a class="slide flyer add evad promo" href="{e(site)}/{href}"><span class="flyer-img"><span class="script">{e(script)}</span></span>'
                f'<span class="hed">{e(title)}</span><span class="dek">{e(line)}</span></a>')
    return (f'<a class="slide ad house promo" href="{e(site)}/{href}"><span class="script">{e(script)}</span>'
            f'<span class="hed">{e(title)}</span><span class="dek">{e(line)}</span><span class="btn solid sm">{e(cta)}</span></a>')

# Packages for the Advertise page and the media kit. Founding sponsor prices, per month unless noted.
AD_TIERS = [
    {"name": "Neighbor", "price": "$49", "per": "a month", "best": "Shops, salons, food trucks and side businesses",
     "gets": ["A front page carousel card (A2) or a Free Stuff card (A7)", "Your name in the sponsor thank-you on every page",
              "One shout-out on our Facebook page each month"]},
    {"name": "Main Street", "price": "$129", "per": "a month", "best": "Restaurants, gyms, dealers and service businesses", "pick": True,
     "gets": ["Everything in Neighbor", "\u201cPresented by\u201d sponsor of one section (A3)", "One sponsored post a month on Facebook and Instagram (S1)",
              "A featured business page with photos and hours"]},
    {"name": "Hometown Hero", "price": "$279", "per": "a month", "best": "Banks, hospitals, insurance and bigger local brands",
     "gets": ["Everything in Main Street", "Top banner on every page (A1), shared with up to two other sponsors",
              "Story page banner (A4)", "Two sponsored posts a month"]},
    {"name": "Presenting Partner", "price": "$499", "per": "a month", "best": "One business that wants to own the paper",
     "gets": ["Everything in Hometown Hero", "The top banner all to yourself", "\u201cFive good things, presented by\u201d on the weekly roundup post",
              "Four sponsored posts a month", "First choice of the Sports sponsorship"]},
]
AD_SPECIALS = [
    ("Sports season sponsor", "$399 a season", "Top of the Sports page (A5) and \u201cBrackets presented by\u201d for one sports season."),
    ("Events sponsor", "$69 a month", "A flyer-sized card in the Local events carousel (A6)."),
    ("Sponsored social post", "$49 a post", "One post on Facebook and Instagram, marked as a paid partnership. We can design it for you (S1)."),
    ("Boost a flyer or listing", "$15 a week", "Pin an event flyer, garage sale or Free Stuff listing to the front of its carousel."),
]
AD_DEALS = ["Pay for 3 months, save 10%. Pay for 12 months, save 20%.", "Nonprofits, churches and schools save 25%.",
            "Founding sponsors (the first 10) keep their price for a full year.", "Business spotlights and community posts are always free."]


def ads_open(cfg):
    """False hides every "Advertise here" placeholder and the Advertise page. Paid ads in config still show."""
    return cfg.get("ads_open", True)


def ads_for(cfg, spot, section=None):
    """Paid ads running now in one spot. Ads without a spot are carousel cards (A2)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return [a for a in cfg.get("ads") or [] if (a.get("spot") or "A2") == spot and a.get("link")
            and (not a.get("until") or a["until"] >= today) and (not section or a.get("section") == section)]


def banner(cfg, spot, i=0):
    """A wide ad, or a branded "Advertise here" placeholder until one is sold."""
    site = cfg["site_url"].rstrip("/")
    ads = ads_for(cfg, spot)
    if ads:
        a = ads[i % len(ads)]
        small = f'<source media="(max-width:600px)" srcset="{e(a["image_mobile"])}">' if a.get("image_mobile") else ""
        return (f'<aside class="wrap adrow" aria-label="Advertisement"><span class="caps adlabel">Advertisement</span>'
                f'<a class="adbanner" href="{e(a["link"])}" target="_blank" rel="noopener sponsored">'
                f'<picture>{small}<img src="{e(a["image"])}" alt="{e(a.get("alt") or a["name"])}" loading="lazy"></picture></a></aside>')
    if not ads_open(cfg):
        return promo_banner(cfg, spot) if spot in PROMO else ""
    s = AD_SPOT[spot]
    script, text = s.get("house", ("Advertise here!", "Put your business in front of neighbors who love Mansfield."))
    return (f'<aside class="wrap adrow" aria-label="Advertising space"><a class="adbanner house" href="{e(site)}/advertise.html#spots">'
            f'<span class="script">{script}</span><span class="adtext">{text}</span>'
            f'<span class="caps adspot">Spot {spot} · {e(s["name"])}</span></a></aside>')


def sponsor_tag(cfg, section, site):
    ads = ads_for(cfg, "A3", section)
    if ads:
        a = ads[0]
        mark = f'<img src="{e(a["image"])}" alt="{e(a["name"])}">' if a.get("image") else e(a["name"])
        return (f'<a class="sponsortag" href="{e(a["link"])}" target="_blank" rel="noopener sponsored">'
                f'<span class="caps">Presented by</span>{mark}</a>')
    if not ads_open(cfg):
        return ""
    return f'<a class="sponsortag house caps" href="{e(site)}/advertise.html#spots">Sponsor this section</a>'


def sponsor_strip(cfg):
    site = cfg["site_url"].rstrip("/")
    seen, names = set(), []
    for a in cfg.get("ads") or []:
        if a["name"] not in seen and ads_for(cfg, a.get("spot") or "A2"):
            seen.add(a["name"])
            names.append(f'<a href="{e(a["link"])}" target="_blank" rel="noopener sponsored">{e(a["name"])}</a>')
    if names:
        return f'<div class="wrap sponsors"><span class="caps">Thank you to our sponsors</span>{" · ".join(names)}</div>'
    if not ads_open(cfg):
        return ""
    return (f'<div class="wrap sponsors"><span class="caps">Founding sponsors wanted</span>'
            f'<a href="{e(site)}/advertise.html">Your business could be the first name here</a></div>')


SHIELD = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3l8 3v6c0 4.5-3.4 8.2-8 9-4.6-.8-8-4.5-8-9V6z"/><path d="M12 8v5M12 16v.5"/></svg>'
FUND_SHORT = ("<b>Posted by neighbors, not by us.</b> Sunny Mansfield and Ofgeneration LLC don't run, verify or endorse these fundraisers, "
              "and we never handle the money. Give only if you trust the organizer, and never send gift cards or wire money.")
FUND_FACTS = [
    ("Community submissions", "Fundraisers are posted by neighbors. Sunny Mansfield and Ofgeneration LLC don't run, endorse or "
     "verify them, and aren't connected to the people or causes asking."),
    ("We never touch the money", "Donations go straight to the fundraising site, like GoFundMe. We don't collect, hold or pass along "
     "any money, and we get nothing from your gift."),
    ("No promises", "An editor gives each post a quick look before it appears, but we can't confirm who is asking, how much is "
     "needed or how the money will be spent. Giving is your choice and your risk."),
    ("Not tax advice", "Your gift may not be tax-deductible. Ask the organizer or the fundraising site."),
    ("Give safely", "Use trusted sites, give to people and groups you know, and never send gift cards, wire transfers, crypto or "
     "cash-app payments to someone you don't know."),
    ("Something wrong?", "Report it and we'll take it down while we look into it."),
]


def money(v):
    """Turn a goal like 5000 or "5000" into "$5,000"; leave anything wordier alone."""
    digits = v.replace(",", "").replace("$", "").strip()
    return f"${int(digits):,}" if digits.isdigit() else v


def fund_url(f):
    return f"/give/{f['id']}.html"  # site-relative, so rebase() and sub-folder sites keep working


def fund_card(cfg, f):
    """A fundraiser on a shelf: picture and goal, title, a three-line teaser, Donate and Share. The rest is on its page."""
    page = fund_url(f)
    img = (f'<img src="{e(img_src(f["image"]))}" alt="" loading="lazy" referrerpolicy="no-referrer">' if f.get("image")
           else f'<span class="art"><svg viewBox="0 0 100 100" aria-hidden="true">{ART["seniors"]}</svg></span>')
    goal = (f'<span class="goal"><span class="goal-k">Goal</span><span class="goal-v{" long" if len(money(f["goal"])) > 6 else ""}">{e(money(f["goal"]))}</span>'
            f'<span class="goal-k">needed</span></span>') if f.get("goal") else ""
    when = f' · Until {f["day"].strftime("%b %-d")}' if f.get("day") else ""
    cause = f'<span class="cause">{e(f["cause"])}</span>' if f.get("cause") else ""
    give = (f'<a class="btn solid sm" href="{e(f["link"])}" target="_blank" rel="noopener nofollow ugc">Donate</a>' if f.get("link") else "")
    site = cfg["site_url"].rstrip("/")
    share = f'<a class="btn fb sm" href="{e(fb_share(site + page))}" target="_blank" rel="noopener">{FB_ICON}Share</a>'
    by = f'<span class="muted">Organized by {e(f["by"])}</span>' if f.get("by") else ""
    dek = f'<p class="dek">{e(" ".join((f.get("summary") or "").split()))}</p>' if f.get("summary") else ""
    return (f'<article class="slide listing fund" id="f-{f["id"]}"><a class="photo" href="{e(page)}" tabindex="-1" aria-hidden="true">{img}{goal}</a>'
            f'<span class="caps kicker">{e(f.get("town") or "Richland County")}{when}</span>{cause}'
            f'<h3 class="hed"><a href="{e(page)}">{e(f["title"])}</a></h3>{dek}'
            f'<a class="caps more-link" href="{e(page)}">Read the full story <span aria-hidden="true">&rsaquo;</span></a>{by}'
            f'<span class="tagline">Community post · Not verified by us</span>'
            f'<div class="share">{give}{share}</div></article>')


def ad_card(cfg, ad=None, spot="A2", i=0):
    site = cfg["site_url"].rstrip("/")
    if ad:
        text = f'<span class="dek">{e(ad["text"])}</span>' if ad.get("text") else ""
        return (f'<a class="slide ad" href="{e(ad["link"])}" target="_blank" rel="noopener sponsored">'
                f'<span class="photo"><img src="{e(ad["image"])}" alt="{e(ad["name"])}" loading="lazy"></span>'
                f'<span class="caps sponsor">Sponsor</span><span class="hed">{e(ad["name"])}</span>'
                f'{text}</a>')
    if not ads_open(cfg):
        return promo_card(cfg, i)
    return (f'<a class="slide ad house" href="{e(site)}/advertise.html#spots"><span class="caps sponsor">Spot {spot}</span>'
            f'<span class="script">Advertise here!</span><span class="hed">Reach neighbors who love Mansfield</span>'
            f'<span class="btn solid sm">See ad spots</span></a>')


def slide(cfg, s):
    return card(cfg, s).replace('<article class="card"', '<article class="card slide"', 1)


def quote_of_the_day(path=HERE / "quotes.csv"):
    """The current quote from quotes.csv. Columns can be named quote, author, date and hashtags (any order),
    or just quote then author with no header row."""
    from zoneinfo import ZoneInfo
    if not path.exists():
        return None
    rows = [r for r in csv.reader(io.StringIO(path.read_text(encoding="utf-8-sig"))) if any(c.strip() for c in r)]
    if not rows:
        return None
    names = [c.strip().lower() for c in rows[0]]
    col = lambda *keys: next((i for i, n in enumerate(names) if n in keys), None)
    qi, ai, di = col("quote", "text", "quotes"), col("author", "by", "who", "name", "said by"), col("date")
    ti = col("suggested hashtags", "hashtags", "tags")
    if qi is None:
        qi, ai, di, ti = 0, 1 if len(rows[0]) > 1 else None, None, None
    else:
        rows = rows[1:]
    get = lambda r, i: r[i].strip() if i is not None and i < len(r) else ""
    quotes = [{"text": get(r, qi), "author": get(r, ai), "tags": get(r, ti),
               "date": parse_day(get(r, di)) if di is not None and get(r, di) else None}
              for r in rows if get(r, qi)]
    if not quotes:
        return None
    # Two quotes a day, at 8 a.m. and 8 p.m. Eastern, walking down the list in date order (file order if undated).
    quotes.sort(key=lambda q: q["date"] or datetime.max.replace(tzinfo=timezone.utc))
    start = (quotes[0]["date"] or datetime(2026, 10, 22, tzinfo=timezone.utc)).date()
    now = datetime.now(ZoneInfo("America/New_York"))
    turn = now.replace(hour=20 if now.hour >= 20 or now.hour < 8 else 8, minute=0, second=0, microsecond=0)
    if now.hour < 8:
        turn -= timedelta(days=1)
    recent = []
    for back in range(2):  # this turn and the one before, so the Facebook feed always has something new to find
        at = turn - timedelta(hours=12 * back)
        n = (at.date() - start).days * 2 + (at.hour == 20)
        recent.append({**quotes[n % len(quotes)], "at": at})
    return {**recent[0], "recent": recent}


def pinned(stories, headlines):
    """The story an editor picked as the headline, if it's still on the site."""
    for h in sorted(headlines, key=lambda h: h["date"], reverse=True)[:1]:
        for s in stories:
            if (h["link"] and h["link"] == s["link"]) or s["title"].strip().lower() == h["title"].strip().lower():
                return s
    return None


def shelf_order(rails, funds, events):
    """Good News, then local causes, then Sports, then local events, then the rest of the sections."""
    out = []
    for k, html_ in rails:
        out.append(html_)
        if k == "good":
            out.append(funds)
            funds = ""
        if k == "sports":
            out.append(events)
            events = ""
    return "".join(out) + funds + events  # if a section is empty today, its neighbor still shows, just lower down


def tv_embed(url):
    """Turn a podcast or video link into (kind, source URL, thumbnail, player height in px).
    kind is "youtube" (16:9 video), "frame" (an audio player from the host), "audio" (a plain audio file) or ""."""
    url = (url or "").strip()
    m = re.search(r'<iframe[^>]+src="([^"]+)"', url)  # a pasted embed code from any host
    if m:
        h = re.search(r'height="(\d+)', url)
        src = html.unescape(m.group(1))
        kind, _, thumb, _ = tv_embed(src)
        if kind in ("youtube", "video"):
            return kind, tv_embed(src)[1], thumb, 0
        if kind:
            return kind, tv_embed(src)[1], thumb, int(h.group(1)) if h else tv_embed(src)[3]
        w = re.search(r'width="(\d+)', url)
        video = h and w and int(h.group(1)) >= 200 and int(w.group(1)) > int(h.group(1))  # wide and tall: a video player
        return ("video" if video else "frame"), src, "", int(h.group(1)) if h else 200
    m = re.search(r"(?:youtube(?:-nocookie)?\.com/(?:watch\?(?:.*&)?v=|embed/|shorts/|live/)|youtu\.be/)([\w-]{11})", url)
    if m:
        vid = m.group(1)
        return "youtube", f"https://www.youtube-nocookie.com/embed/{vid}?autoplay=1&rel=0", f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg", 0
    m = re.search(r"vimeo\.com/(?:video/)?(\d+)", url)
    if m:
        return "video", f"https://player.vimeo.com/video/{m.group(1)}", "", 0
    if re.search(r"facebook\.com/.+/videos/|fb\.watch/", url):
        return "video", "https://www.facebook.com/plugins/video.php?show_text=false&href=" + urllib.parse.quote(url, safe=""), "", 0
    m = re.search(r"open\.spotify\.com/(?:embed/)?(episode|show)/(\w+)", url)
    if m:
        return "frame", f"https://open.spotify.com/embed/{m.group(1)}/{m.group(2)}", "", 232
    m = re.search(r"(?:anchor\.fm|creators\.spotify\.com/pod/show|podcasters\.spotify\.com/pod/show)/([\w-]+)/episodes/([\w-]+)", url)
    if m:
        return "frame", f"https://anchor.fm/{m.group(1)}/embed/episodes/{m.group(2)}", "", 161
    m = re.search(r"podcasts\.apple\.com(/[^?#]+)(\?[^#]*)?", url)
    if m:
        ep = "i=" in (m.group(2) or "")
        return "frame", f"https://embed.podcasts.apple.com{m.group(1)}{m.group(2) or ''}", "", 175 if ep else 450
    if re.search(r"(?:^|//)(?:www\.|m\.)?soundcloud\.com/", url):
        return "frame", "https://w.soundcloud.com/player/?visual=true&url=" + urllib.parse.quote(url.split("?")[0], safe=""), "", 300
    m = re.search(r"buzzsprout\.com/(\d+)/(?:episodes/)?(\d+)", url)
    if m:
        return "frame", f"https://www.buzzsprout.com/{m.group(1)}/{m.group(2)}?client_source=small_player&iframe=true", "", 200
    m = re.search(r"podbean\.com/(?:ew|e|player-v2/\?i=)/?(pb-[\w-]+|[\w-]+-pb)", url)
    if m:
        pid = m.group(1)
        pid = pid if pid.endswith("-pb") else pid[3:] + "-pb"
        return "frame", f"https://www.podbean.com/player-v2/?i={pid}&share=1&download=1&skin=1&btn-skin=7", "", 150
    if re.search(r"\.(mp3|m4a|aac|ogg|oga|opus|wav)(\?|$)", url, re.I):
        return "audio", url, "", 0
    return "", "", "", 0


YT_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id="
YT_PLAYLIST_FEED = "https://www.youtube.com/feeds/videos.xml?playlist_id="


def youtube_info(url):
    """Title and channel name for a YouTube link, from YouTube's public oEmbed endpoint ({} if it can't be reached)."""
    try:
        _, body = fetch("https://www.youtube.com/oembed?format=json&url=" + urllib.parse.quote(url, safe=""), limit=20_000)
        return json.loads(body)
    except Exception as exc:
        note(f"Watch Mansfield: no title for {url} ({exc})")
        return {}


def channel_videos(channel_id, count):
    """The newest videos on a YouTube channel, from its public RSS feed (newest first)."""
    if not channel_id or count <= 0:
        return []
    # The channel's "UULF" playlist is its Videos tab (long-form only, no Shorts or live streams). If that feed can't
    # be read, fall back to the full channel feed and skip anything that links to /shorts/.
    body = ""
    for feed in (YT_PLAYLIST_FEED + "UULF" + channel_id[2:], YT_FEED + channel_id):
        try:
            _, body = fetch(feed.replace(" ", ""), limit=600_000)
            if "<entry>" in body:
                break
        except Exception as exc:
            note(f"Watch Mansfield: feed unavailable ({exc})")
    out = []
    entries = [x for x in re.findall(r"<entry>(.*?)</entry>", body, re.S) if "/shorts/" not in x]
    for entry in entries[:count]:
        vid = re.search(r"<yt:videoId>([\w-]{11})</yt:videoId>", entry)
        title = re.search(r"<title>(.*?)</title>", entry, re.S)
        author = re.search(r"<author>\s*<name>(.*?)</name>", entry, re.S)
        about = re.search(r"<media:description>(.*?)</media:description>", entry, re.S)
        when = re.search(r"<published>(.*?)</published>", entry)
        if vid:
            out.append({"url": f"https://www.youtube.com/watch?v={vid.group(1)}",
                        "title": html.unescape(title.group(1)).strip() if title else "",
                        "host": html.unescape(author.group(1)).strip() if author else "",
                        "writeup": html.unescape(about.group(1)).strip() if about else "",
                        "date": when.group(1).strip() if when else ""})
    return out


def load_watch(watch, sample=False):
    """watch.json -> the video list for Watch Mansfield: hand-picked links first, then the newest channel videos.
    A plain YouTube link is enough; the headline and channel name are filled in from YouTube."""
    picks = watch.get("videos") or watch.get("tv") or []  # "tv" is the old station.json name
    picks = [{"url": x} if isinstance(x, str) else dict(x) for x in ([picks] if isinstance(picks, (str, dict)) else picks)]
    picks = [x for x in picks if (x.get("url") or "").strip()]
    if not sample:
        for x in picks:
            if tv_embed(x["url"])[0] == "youtube" and not (x.get("title") and x.get("host")):
                info = youtube_info(x["url"])
                x.setdefault("title", info.get("title", ""))
                x["title"] = x["title"] or info.get("title", "")
                x["host"] = x.get("host") or info.get("author_name", "")
        seen = {youtube_id(x["url"]) for x in picks}
        picks += [v for v in channel_videos(watch.get("channel_id", ""), int(watch.get("from_channel") or 0))
                  if youtube_id(v["url"]) not in seen]
    if picks:
        note(f"Watch Mansfield: {len(picks)} videos")
    return picks


def watch_entries(videos):
    """Videos for the Watch Mansfield slot; entries without a playable video link are skipped.
    YouTube previews use the video's own thumbnail (the HD one, falling back to the standard one)."""
    out = []
    for x in videos:
        kind, src, thumb, _ = tv_embed(x.get("url", ""))
        if kind in ("youtube", "video"):
            url = (x.get("url") or "").strip()
            thumb = x.get("image") or (thumb.replace("/hqdefault.jpg", "/maxresdefault.jpg") if thumb else "")
            vid = youtube_id(url) or slug(x.get("title") or url)[:48].strip("-") or f"video-{len(out) + 1}"
            out.append({**x, "src": src, "thumb": thumb, "href": x.get("link") or (url if url.startswith("http") else ""),
                        "id": vid, "page": f"watch/{vid}.html"})
    return out


def make_watch_images(videos, out):
    """watch/<id>.jpg: each video's preview inside the Watch Mansfield frame, sized for Facebook link previews."""
    try:
        from posts import load_photo, watch_image
    except ImportError as exc:
        note(f"Watch images: skipped ({exc})")
        return set()
    made = set()
    (out / "watch").mkdir(parents=True, exist_ok=True)
    for v in videos:
        photo = None
        for src in ([v["thumb"], v["thumb"].replace("maxresdefault", "hqdefault")] if v["thumb"] else []):
            photo = load_photo(src, UA)
            if photo and photo.width > 200:  # YouTube answers a missing maxres picture with a tiny placeholder
                break
            photo = None
        try:
            watch_image(v.get("title") or "Watch Mansfield", v.get("host") or "", photo).save(
                out / "watch" / f"{v['id']}.jpg", quality=86, optimize=True, progressive=False)
            made.add(v["id"])
        except Exception as exc:
            note(f"Watch image for {v['id']}: {exc}")
    if made:
        note(f"Watch images: {len(made)} made")
    return made


def render_watch_index(cfg, videos, stories):
    """watch.html: every Watch Mansfield video in one big carousel (10 seconds each), then all of them as tiles."""
    site = cfg["site_url"].rstrip("/")
    tiles = "".join(f'<a class="watch-tile" href="{e(watch_url(cfg, v))}"><span class="w-media">'
                    + (f'<img src="{e(img_src(v["thumb"]))}" alt="" loading="lazy" onerror="{THUMB_FALLBACK}">' if v["thumb"] else "")
                    + f'<span class="w-play" aria-hidden="true"><span></span></span></span><span class="caps kicker">{e(v.get("host") or "Watch Mansfield")}</span>'
                    f'<span class="hed">{e(v.get("title") or "")}</span></a>' for v in videos)
    body = (f'<section class="wrap watch-hub">{watch_card(cfg, videos).replace("class=\"ov watch\"", "class=\"ov watch big\"", 1)}</section>'
            f'<section class="wrap watch-more">{section_head(f"All videos ({len(videos)})")}<div class="watch-grid">{tiles}</div></section>{WATCH_JS}'
            if videos else f'<section class="wrap latest">{first_post(cfg, "watch", block=True)}</section>')
    inner = f"""{page_title("Mansfield made", "Watch Mansfield", "Podcasts, shows and videos from people in Mansfield and Richland County. Tap any video to watch, and share the ones you love.")}
{body}
<section class="wrap latest"><p class="note" style="text-align:center">Have a Mansfield show, podcast or video? <a class="link" href="{e(site)}/submit.html">Send it to us</a> and we may feature it here.</p></section>"""
    data = {"@context": "https://schema.org", "@type": "CollectionPage", "name": "Watch Mansfield", "url": f"{site}/watch.html",
            "hasPart": [{"@type": "VideoObject", "name": v.get("title") or "", "url": watch_url(cfg, v)} for v in videos]}
    return shell(cfg, f"Watch Mansfield: local videos and podcasts · {cfg['site_name']}",
                 "Podcasts, shows and videos made by people in Mansfield and Richland County, Ohio. Local interviews, sports talk, "
                 "music and stories from around town, all in one place.", f"{site}/watch.html", inner, stories, data=data)


def watch_url(cfg, v):
    return f"{cfg['site_url'].rstrip('/')}/{v['page']}"


def watch_desc(v):
    text = " ".join((v.get("writeup") or "").split())
    if not text:
        return f"Watch \u201c{v.get('title') or 'this video'}\u201d{' from ' + v['host'] if v.get('host') else ''} on Watch Mansfield, local videos from Mansfield and Richland County, Ohio."
    return text if len(text) <= 220 else text[:217].rsplit(" ", 1)[0] + "\u2026"


BULLET = re.compile(r"^(?:[✅✔☑•▪▸►➡→–—*\-]|\U0001F449|\U0001F539|\U0001F538|\d{1,2}[.)])\s*")
TIMESTAMP = re.compile(r"^\(?((?:\d{1,2}:)?\d{1,2}:\d{2})\)?\s*[-–—:]?\s*")
LEAD_SYMBOLS = re.compile(r"^[^\w\"'(]+")
URL = re.compile(r"(https?://[^\s<]+[^\s<.,;:!?)\]'\"])")


def linkify(text):
    """Escape a line and turn bare links into real ones."""
    out, last = [], 0
    for m in URL.finditer(text):
        out.append(e(text[last:m.start()]))
        href = m.group(1)
        label = re.sub(r"^https?://(www\.)?", "", href)
        out.append(f'<a href="{e(href)}" target="_blank" rel="noopener nofollow ugc">{e(label[:48] + ("…" if len(label) > 48 else ""))}</a>')
        last = m.end()
    return "".join(out) + e(text[last:])


def seconds(stamp):
    n = 0
    for part in stamp.split(":"):
        n = n * 60 + int(part)
    return n


YEAR_ITEM = re.compile(r"(?<![\w$,])((?:19|20)\d{2}):\s+")
ABBREV = re.compile(r"^(?:(?:[A-Z]\.)+|Mr\.|Mrs\.|Ms\.|Dr\.|St\.|Jr\.|Sr\.|Rev\.|No\.|Jan\.|Feb\.|Aug\.|Sept\.|Oct\.|Nov\.|Dec\.|Ave\.|Rd\.)$")


def sentences(text):
    """Split a paragraph into sentences without breaking on 'U.S.' or 'Dr.'."""
    out, cur = [], []
    for w in text.split():
        cur.append(w)
        if re.search(r"[.!?][\"”’)]?$", w) and not ABBREV.match(w):
            out.append(" ".join(cur)); cur = []
    if cur:
        out.append(" ".join(cur))
    return out


def tidy_wall(text, size=320):
    """Break a long pasted paragraph into short ones, and turn an inline timeline
    ("milestones include: 1975: ... 1987: ...") into its own list."""
    lines = []
    for raw in (text or "").replace("\r", "").split("\n"):
        line = raw.strip()
        if len(line) < 400 or BULLET.match(line):
            lines.append(raw)
            continue
        tail = []
        hits = list(YEAR_ITEM.finditer(line))
        if len(hits) >= 2:
            starts = [m.start() for m in hits] + [len(line)]
            head, items = line[:starts[0]], [line[a:b].strip() for a, b in zip(starts, starts[1:])]
            last = sentences(items[-1])
            if len(last) > 1:
                items[-1], tail = last[0], [" ".join(last[1:])]
            line = head
            items = ["• " + x for x in items]
        else:
            items = []
        sents = sentences(line)
        intro = sents.pop() if sents and sents[-1].endswith(":") and items else None
        paras, para = [], []
        for x in sents:
            para.append(x)
            if len(" ".join(para)) >= size:
                paras.append(" ".join(para)); para = []
        if para:
            if paras and len(" ".join(para)) < size // 3:  # no one-line orphan at the end
                paras[-1] += " " + " ".join(para)
            else:
                paras.append(" ".join(para))
        for x in paras:
            lines += [x, ""]
        if intro:
            lines.append(intro)
        lines += items + [""] if items else []
        for t in tail:
            lines += tidy_wall(t, size).split("\n") + [""]
    return "\n".join(lines)


def format_writeup(text, video_url=""):
    """Turn a YouTube description into a tidy episode write-up: section labels (lines like "IN THIS EPISODE:"),
    real bullet lists (lines starting with a check mark, dash or number, with the lead phrase in bold), clickable
    timestamps, links and a row of hashtags."""
    vid = youtube_id(video_url)
    blocks, items, tags = [], [], []
    loose = []  # short plain lines right after a section label, shown as a list (like "KEY MOMENTS:")

    def flush():
        nonlocal items, loose
        if loose and len(loose) >= 2:
            items = [f"<li>{linkify(x)}</li>" for x in loose] + items
        elif loose:
            blocks.extend(f"<p>{linkify(x)}</p>" for x in loose)
        if items:
            blocks.append('<ul class="wu-list">' + "".join(items) + "</ul>")
        items, loose = [], []

    after_label = False
    for raw in tidy_wall(text).split("\n"):
        line = raw.strip()
        if not line:
            if loose and not after_label:
                flush()
            continue
        words = line.split()
        if all(w.startswith("#") for w in words):
            tags += [w for w in words if len(w) > 1]
            continue
        bare = LEAD_SYMBOLS.sub("", line).strip()
        letters = re.sub(r"[^A-Za-z]", "", bare)
        is_label = (bare and len(bare) <= 60 and len(bare.split()) <= 7 and not BULLET.match(line)
                    and (bare.endswith(":") or (letters.isupper() and len(letters) > 3)) and not URL.search(bare))
        if is_label:
            flush()
            label = bare.rstrip(":").strip()
            label = label.capitalize() if label.isupper() else label
            blocks.append(f'<h3 class="wu-label">{e(label)}</h3>')
            after_label = True
            continue
        ts = TIMESTAMP.match(line)
        if (ts and len(line) > len(ts.group(0))) or BULLET.match(line):
            if loose:
                flush()
            after_label = False
        if ts and len(line) > len(ts.group(0)):
            stamp = ts.group(1)
            chip = (f'<a class="wu-ts" href="https://www.youtube.com/watch?v={vid}&amp;t={seconds(stamp)}s" target="_blank" rel="noopener">{stamp}</a>'
                    if vid else f'<span class="wu-ts">{stamp}</span>')
            items.append(f"<li class=\"has-ts\">{chip}<span>{linkify(line[ts.end():])}</span></li>")
            continue
        bullet = BULLET.match(line)
        if bullet:
            body = line[bullet.end():].strip()
            lead = re.match(r"^(.{3,70}?)\s+[–—-]\s+(.+)$", body) or re.match(r"^([^:]{3,60}):\s+(.+)$", body)
            html_ = (f"<strong>{linkify(lead.group(1))}</strong> \u2013 {linkify(lead.group(2))}" if lead else linkify(body))
            items.append(f"<li>{html_}</li>")
            continue
        short = len(line) <= 140 and not URL.search(line)
        if short and ((after_label and not line.endswith((".", "!", "?"))) or (loose and not line.endswith("."))):
            if items:
                flush()
            loose.append(line)
            continue
        flush()
        after_label = False
        blocks.append(f"<p>{linkify(line)}</p>")
    flush()
    if tags:
        blocks.append('<p class="wu-tags">' + "".join(f"<span>{e(t)}</span>" for t in dict.fromkeys(tags)) + "</p>")
    return "".join(blocks)


def nice_date(iso):
    try:
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).strftime("%B %-d, %Y")
    except (ValueError, AttributeError):
        return ""


WRITEUP_JS = """<script>(()=>{const c=document.querySelector('.wu-card');if(!c)return;const b=c.querySelector('.wu-more');const body=c.querySelector('.wu-body');
if(body.scrollHeight<=body.clientHeight+40){c.classList.add('open');b.hidden=true;return}
b.addEventListener('click',()=>{const o=c.classList.toggle('open');b.textContent=o?'Show less':'Show the full description';b.setAttribute('aria-expanded',o)})})()</script>"""


def render_watch_page(cfg, v, videos, stories, has_image):
    """A page for one Watch Mansfield video, laid out like an episode page: the player in its Watch Mansfield frame,
    the title, show name and date, Share buttons, then an 'About this episode' card with the formatted write-up.
    'More from Watch Mansfield' sits beside it. Sharing to Facebook shows the framed picture (watch/<id>.jpg)."""
    site = cfg["site_url"].rstrip("/")
    url = watch_url(cfg, v)
    title = v.get("title") or "Watch Mansfield"
    writeup = format_writeup(v.get("writeup") or "", v.get("url", "")) or f"<p>{e(watch_desc(v))}</p>"
    when = nice_date(v.get("date", ""))
    meta_bits = [b for b in (e(v.get("host") or ""), when) if b]
    source = (f'<a class="btn" href="{e(v["href"])}" target="_blank" rel="noopener">Watch on '
              f'{"YouTube" if "youtu" in v["href"] else "the original site"}</a>') if v["href"] else ""
    others = [o for o in videos if o is not v][:6]
    more = (f'<aside class="watch-aside">{section_head("More from Watch Mansfield")}<div class="watch-list">'
            + "".join(f'<a class="watch-row" href="{e(watch_url(cfg, o))}"><span class="w-media">'
                      + (f'<img src="{e(img_src(o["thumb"]))}" alt="" loading="lazy" onerror="{THUMB_FALLBACK}">' if o["thumb"] else "")
                      + f'</span><span><span class="hed">{e(o.get("title") or "")}</span><span class="caps host">{e(o.get("host") or "")}</span></span></a>'
                      for o in others) + f'</div><a class="explore" href="{e(site)}/watch.html">All videos <span aria-hidden="true">&rsaquo;</span></a></aside>') if others else ""
    image = f"{site}/{v['page'][:-5]}.jpg" if has_image else None
    data = {"@context": "https://schema.org", "@type": "VideoObject", "name": title, "description": watch_desc(v),
            "thumbnailUrl": [image or (v["thumb"] if v["thumb"].startswith("https://") else site + "/og.png")],
            "embedUrl": v["src"].split("?")[0], "url": url, "publisher": {"@id": f"{site}/#org"}}
    if v.get("date"):
        data["uploadDate"] = v["date"]
    if v.get("url", "").startswith("http"):
        data["contentUrl"] = v["url"]
    inner = f"""<section class="wrap watch-layout{"" if others else " solo"}"><article class="watch-main">
{watch_card(cfg, [v], solo=True)}
<header class="watch-head"><a class="caps kicker" href="{e(site)}/watch.html">Watch Mansfield</a><h1 class="hed">{e(title)}</h1>
{f'<p class="caps watch-meta">{" &middot; ".join(meta_bits)}</p>' if meta_bits else ""}
<div class="share"><a class="btn fb" href="{e(fb_share(url))}" target="_blank" rel="noopener">{FB_ICON}Share on Facebook</a>{source}
<button class="textbtn" type="button" data-copy="{e(url)}">Copy link</button></div></header>
<section class="wu-card" aria-labelledby="wu-h"><h2 class="wu-head" id="wu-h">About this episode</h2>
<div class="wu-body">{writeup}</div><button class="wu-more" type="button" aria-expanded="false">Show the full description</button></section>
<p class="note watch-pitch">Have a Mansfield show, podcast or video? <a class="link" href="{e(site)}/submit.html">Send it to us</a> and we may feature it on Watch Mansfield.</p>
</article>{more}</section>{WATCH_JS}{WRITEUP_JS}"""
    return shell(cfg, f"{title} · Watch Mansfield · {cfg['site_name']}", watch_desc(v), url, inner, stories,
                 image=image, data=data)


THUMB_FALLBACK = "if(this.src.includes('maxresdefault'))this.src=this.src.replace('maxresdefault','hqdefault');else this.remove()"


def watch_card(cfg, videos, solo=False):
    """Watch Mansfield: one hero spot that rotates through local videos every 10 seconds, each titled with its headline.
    The player loads only when a reader taps Play, and the rotation stops while a video plays."""
    slides = []
    for i, v in enumerate(videos):
        title = e(v.get("title") or "Watch Mansfield")
        # maxresdefault doesn't exist for every video, so fall back to hqdefault, then to the plain dark card.
        img = f'<img src="{e(img_src(v["thumb"]))}" alt="" onerror="{THUMB_FALLBACK}">' if v["thumb"] else ""
        hed_link = title if solo else f'<a href="{e(watch_url(cfg, v))}">{title}</a>'
        fb = f'<a class="w-share" href="{e(fb_share(watch_url(cfg, v)))}" target="_blank" rel="noopener" aria-label="Share {title} on Facebook">{FB_ICON}Share</a>'
        host = f'<span class="ov-meta">{e(v.get("host") or "")}{"" if solo else fb}</span>'
        slides.append(f'<div class="w-slide"{"" if i == 0 else " hidden"}><div class="w-media">{img}'
                      f'<button class="w-play" type="button" data-tv="{e(v["src"])}" aria-label="Play {title}"><span></span></button></div>'
                      f'<div class="ov-text"><span class="caps kicker">Watch Mansfield</span><h3 class="hed">{hed_link}</h3>{host}</div></div>')
    n = len(videos)
    CUR = ' aria-current="true"'
    dots = ("".join(f'<button type="button" data-go="{i}" aria-label="Video {i + 1} of {n}"{CUR if i == 0 else ""}></button>'
                    for i in range(n)) if n > 1 else "")
    extra = f'<div class="w-dots">{dots}</div><span class="w-bar" aria-hidden="true"></span>' if n > 1 else ""
    if solo:
        return f'<div class="ov watch solo">{"".join(slides)}</div>'
    return (f'<article class="ov watch" id="watch" role="region" aria-roledescription="carousel" aria-label="Watch Mansfield videos">'
            f'{"".join(slides)}{extra}</article>')


WATCH_JS = """<script>/* Watch Mansfield: a new video every 10 seconds (the orange bar is the timer); players load only on Play */
(()=>{const w=document.querySelector('.watch');if(!w)return;const slides=[...w.querySelectorAll('.w-slide')];
const dots=[...w.querySelectorAll('[data-go]')];const bar=w.querySelector('.w-bar');let i=0,saved=null;
function stop(){if(saved){saved.box.innerHTML=saved.html;saved=null;w.classList.remove('playing')}}
function restart(){if(!bar)return;bar.classList.remove('run');void bar.offsetWidth;bar.classList.add('run')}
function go(k){stop();i=(k+slides.length)%slides.length;slides.forEach((s,n)=>s.hidden=n!==i);
 dots.forEach((d,n)=>n===i?d.setAttribute('aria-current','true'):d.removeAttribute('aria-current'));restart()}
dots.forEach(d=>d.addEventListener('click',()=>go(+d.dataset.go)));
if(bar){bar.addEventListener('animationend',()=>go(i+1));restart()}
w.addEventListener('click',ev=>{const b=ev.target.closest('[data-tv]');if(!b)return;const box=b.parentNode;
 saved={box,html:box.innerHTML};const f=document.createElement('iframe');f.src=b.dataset.tv;f.title=b.getAttribute('aria-label').replace('Play ','');
 f.allow='autoplay; clipboard-write; encrypted-media; picture-in-picture; fullscreen';f.allowFullscreen=true;
 box.replaceChildren(f);w.classList.add('playing');bar&&bar.classList.remove('run')});
})();</script>"""


def section_head(title, link="", tag="h2", hid=""):
    """A section label with an 'Explore all' style link on the right, like a magazine rule."""
    ident = f' id="{hid}"' if hid else ""
    return f'<div class="mag-head"><{tag} class="mag-label"{ident}>{title}</{tag}>{link}</div>'


def ticker(stories):
    """The 'Good news' flash bar: one headline at a time behind an orange label, with back and next arrows."""
    if not stories:
        return ""
    ON = ' class="on"'
    items = "".join(f'<li{"" if i else ON}><a href="{e(s["link"])}"{target(s)}><span class="tick-tag">{e(SECTION_NAMES[s["section"]])}</span>'
                    f'{e(s["title"])}</a></li>' for i, s in enumerate(stories))
    return (f'<div class="wrap ticker" role="region" aria-label="Good news now"><span class="tick-label">Good news</span>'
            f'<ul class="tick-list">{items}</ul><div class="tick-nav"><button type="button" data-tick="-1" aria-label="Previous headline">&lsaquo;</button>'
            f'<button type="button" data-tick="1" aria-label="Next headline">&rsaquo;</button></div></div>')


TICKER_JS = """<script>(()=>{const t=document.querySelector('.ticker');if(!t)return;const li=[...t.querySelectorAll('.tick-list li')];let i=0,timer;
const go=d=>{li[i].classList.remove('on');i=(i+d+li.length)%li.length;li[i].classList.add('on')};
t.querySelectorAll('[data-tick]').forEach(b=>b.addEventListener('click',()=>{go(+b.dataset.tick);start()}));
const calm=matchMedia('(prefers-reduced-motion: reduce)').matches;
function start(){clearInterval(timer);if(!calm&&li.length>1)timer=setInterval(()=>go(1),5000)}
t.addEventListener('mouseenter',()=>clearInterval(timer));t.addEventListener('mouseleave',start);
t.addEventListener('focusin',()=>clearInterval(timer));start()})()</script>"""


def overlay(s, big=False):
    """A photo card with the section tag and headline laid over the picture."""
    return (f'<article class="ov{" big" if big else ""}">{photo(s)}<div class="ov-text">{kicker(s)}{hed(s, "h2" if big else "h3")}'
            f'<span class="ov-meta">{e(s["source"])} · {ago(s["date"])}</span></div></article>')


def hero(top, two, watch=""):
    side = f'<div class="hero-side">{watch}{"".join(overlay(s) for s in two)}</div>' if two or watch else ""
    return f'<section class="wrap hero{"" if side else " solo"}">{overlay(top, True)}{side}</section>'


def thumb(s):
    return f'<article class="thumb">{photo(s)}<div>{kicker(s)}{hed(s)}<span class="tdate">{ago(s["date"])}</span></div></article>'


def trend_card(s):
    dek = f'<p class="dek">{e(s["summary"])}</p>' if s["summary"] else ""
    return f'<article class="tcard">{photo(s)}{kicker(s)}{hed(s)}{meta(s)}{dek}</article>'


def mid_card(s):
    return f'<article class="midcard">{photo(s)}{kicker(s)}{hed(s)}{meta(s)}</article>'


def magazine(cfg, top, also, videos=(), tabs=""):
    """Front page, NewsFlash style: a three-spot hero (lead story, Watch Mansfield videos, one more story), then
    'Trending today' and 'Around Richland County' beside a 'Recent news' sidebar."""
    watch = watch_card(cfg, videos) if videos else ""
    k = 1 if watch else 2
    two, big, lst, row, side = also[:k], also[k:k + 1], also[k + 1:k + 5], also[k + 5:k + 8], also[k + 8:k + 13]
    explore = '<a class="explore" href="#shelves">Explore all <span aria-hidden="true">&rsaquo;</span></a>'
    filters = f'<nav class="sections" aria-label="Sections">{tabs}</nav>' if tabs else explore
    trend = (f'{section_head("Trending today", filters)}<div class="trend">{trend_card(big[0])}'
             f'<div class="trend-list">{"".join(thumb(s) for s in lst)}</div></div>') if big else ""
    around = (f'{section_head("Around Richland County", explore)}<div class="around">{"".join(mid_card(s) for s in row)}</div>') if row else ""
    recent = f'{section_head("Recent news")}<div class="side-list">{"".join(thumb(s) for s in side)}</div>' if side else ""
    return (f'{hero(top, two, watch)}<section class="wrap nf"><div class="nf-col">{trend}{around}</div>'
            f'<aside class="nf-side">{recent}</aside></section>{WATCH_JS if watch else ""}{TICKER_JS}')


def render_index(cfg, stories, businesses, flyers, listings, funds=(), headlines=(), quote=None, jobs=(), birthdays=(), dogs=()):
    site = cfg["site_url"].rstrip("/")
    stories = [s for s in stories if s["section"] != "life"]  # Celebrations of Life live on their own page
    # The front page leads with the editor's pick, or else the newest story with a photo; the rest stay newest first.
    # The two hero spots beside it lay the headline over the picture, so they also take the newest photo stories.
    pick = pinned(stories, headlines) or next((s for s in stories if s.get("image")), None)
    rest = [s for s in stories if s is not pick]
    beside = [s for s in rest if s.get("image")][:2]
    news = ([pick] if pick else []) + beside + [s for s in rest if s not in beside]
    top, also = (news[0], news[1:15]) if news else (None, [])
    front_ids = {s["id"] for s in ([top] if top else []) + also}
    counts = {k: sum(1 for s in stories if s["section"] == k) for k, _ in SECTIONS}
    tabs = f'<button class="tab" type="button" data-sec="all">All <span class="n">{len(stories)}</span></button>' + "".join(
        f'<button class="tab" type="button" data-sec="{k}">{e(n)} <span class="n">{counts[k]}</span></button>'
        for k, n in SECTIONS if counts[k])
    note = ""
    front = ""
    if top:
        front = magazine(cfg, top, also, watch_entries(cfg.get("watch") or []), tabs)
    grid = "".join(card(cfg, s, front=s["id"] in front_ids) for s in stories)
    ads = ads_for(cfg, "A2")
    ad_at = lambda i: ad_card(cfg, ads[i % len(ads)] if ads else None, i=i // 3)
    sale_ads = ads_for(cfg, "A7")
    events_ad = ads_for(cfg, "A6")
    rails = []
    for i, (k, name) in enumerate(SECTIONS):
        items = [s for s in stories if s["section"] == k][:12]
        if not items:
            continue  # empty sections stay off the front page until their first post (their own pages still ask for one)
        slides = [slide(cfg, s) for s in items]
        if i % 3 == 1:
            slides.insert(min(3, len(slides)), ad_at(i))  # one sponsor spot every few shelves
        see = f'{sponsor_tag(cfg, k, site)}<button class="link" type="button" data-show="{k}">See all {counts[k]}</button>'
        rails.append((k, rail(e(name), "".join(slides), see, kind="sec")))
    ev_ad = (f'<a class="slide flyer evad" href="{e(events_ad[0]["link"])}" target="_blank" rel="noopener sponsored"><span class="flyer-img">'
             f'<img src="{e(events_ad[0]["image"])}" alt="{e(events_ad[0]["name"])}" loading="lazy"></span><span class="caps sponsor">Sponsor</span>'
             f'<span class="hed">{e(events_ad[0]["name"])}</span></a>') if events_ad else (
        f'<a class="slide flyer add evad" href="{e(site)}/advertise.html#spots"><span class="flyer-img"><span class="script">Advertise<br>here!</span></span>'
        f'<span class="hed">Sponsor local events</span><span class="dek">Spot A6</span></a>') if ads_open(cfg) else promo_card(cfg, 2, flyer=True)
    flyer_cards = [flyer_card(f) for f in flyers]
    if ev_ad:
        flyer_cards.insert(min(2, len(flyer_cards)), ev_ad)
    events = rail("Local events", "".join(flyer_cards) + (
        f'<a class="slide flyer add" href="{e(site)}/submit.html#flyer"><span class="flyer-img"><span class="plus">+</span></span>'
        f'<span class="hed">Post your flyer</span><span class="dek">Free for community events</span></a>'), rid="events", kind="flyers")
    if not flyers:
        events = ""  # no flyers yet: leave the shelf off the front page
    funds_rail = (f'<section class="wrap givebox" id="donate"><div class="rail-head"><h2 class="label">Donate</h2>'
                  f'<a class="link" href="{e(site)}/fundraisers.html">See all</a></div><div class="give"><div class="give-top">'
                  f'<div><span class="script">Neighbors helping neighbors</span><h2 class="retro sm">Donate to local causes</h2>'
                  f'<p>Medical bills, team trips, fire recovery and special causes shared by people in Richland County.</p></div>'
                  f'<div class="share"><a class="btn solid sm" href="{e(site)}/fundraisers.html">See all {len(funds)}</a>'
                  f'<a class="btn sm" href="{e(site)}/submit.html#fundraiser">Share a fundraiser</a></div></div>'
                  + rail("Fundraisers", "".join(fund_card(cfg, f) for f in funds[:12])).replace('class="wrap railbox ', 'class="railbox ')
                  + f'<p class="give-note">{SHIELD}<span>{FUND_SHORT} <a class="link" href="{e(site)}/fundraisers.html#before-you-give">Read before you give</a></span></p>'
                  f'</div></section>') if funds else ""
    market = rail("Free Stuff", "".join(listing_card(x, cfg) for x in listings[:12]) + ad_card(cfg, sale_ads[0] if sale_ads else None, "A7", i=1),
                  f'<a class="link" href="{e(site)}/free.html">See all</a>', kind="sales") if listings else ""
    market += rail("Now hiring", "".join(job_card(cfg, j, slide=True) for j in jobs[:12]),
                   f'<a class="link" href="{e(site)}/jobs.html">See all</a>', kind="jobs") if jobs else ""
    market += funds_rail  # Donate sits under Now hiring, near the bottom
    home = {"@context": "https://schema.org", "@graph": [
        publisher(cfg),
        {"@type": "WebSite", "@id": f"{site}/#site", "name": cfg["site_name"], "alternateName": ALT_NAMES, "url": f"{site}/",
         "inLanguage": "en-US", "description": "Good news, events and things to do in Mansfield and Richland County, Ohio.",
         "about": [{"@type": "City", "name": "Mansfield", "containedInPlace": {"@type": "State", "name": "Ohio"}},
                   {"@type": "AdministrativeArea", "name": "Richland County, Ohio"}],
         "publisher": {"@id": f"{site}/#org"}},
        {"@type": "ItemList", "name": "Today's good news", "itemListElement": [
            {"@type": "ListItem", "position": i, "url": page_url(cfg, s), "name": s["title"]}
            for i, s in enumerate([s for s in news if s.get("community")][:10], 1)]}]}
    return f"""{head(cfg, f"{cfg['site_name']} | Good News & Things to Do in Mansfield, Ohio",
                 "Good news from Mansfield, Ohio and Richland County: events and things to do, high school sports, "
                 "shop-local spotlights, free stuff and jobs. Updated daily.", site + '/', data=home)}
<div class="sheet"><div class="stripes" aria-hidden="true"></div>
{masthead(cfg, site, stories, current="front")}
<main><h1 class="vh">{e(cfg['site_name'])}: good news from Mansfield and Richland County, Ohio</h1>{note}{ticker(news[:10])}{front}
{birthday_rail(cfg, birthdays)}
{happy_tails_box(cfg, dogs)}
{civic(cfg, businesses)}
<div id="shelves">{shelf_order(rails, "", events)}</div>
{market}
<section class="wrap latest" id="latest" hidden><h2 class="label">More good news</h2>
<div class="grid">{grid}<p class="empty" hidden>Nothing here this week yet. Know a story? <a class="link" href="{e(site)}/submit.html">Submit it</a></p></div>
<div class="more-row"><button class="btn" type="button" id="more" hidden>Show more stories</button></div></section>
{submit_band(cfg, site)}
<div class="stripes" aria-hidden="true"></div>
</main>
{footer(cfg)}
</div>
<div class="toast" id="toast" role="status" aria-live="polite" hidden></div>
{SCRIPT}{reply_box(cfg) if listings or jobs else ""}"""


def shell(cfg, title, desc, url, inner, stories, image=None, extra="", data=None, index=True):
    site = cfg["site_url"].rstrip("/")
    body = f"""<div class="sheet"><div class="stripes" aria-hidden="true"></div>{masthead(cfg, site, stories)}<main>
{inner}<div class="stripes" aria-hidden="true"></div></main>{footer(cfg)}</div>
<div class="toast" id="toast" role="status" aria-live="polite" hidden></div>"""
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
            f"{head(cfg, title, desc, url, image, data=data, index=index)}</head><body>{body}{SCRIPT}{extra}</body></html>")


SB_JS = "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.45.4/dist/umd/supabase.min.js"
EXTRA_SECTIONS = ["Fundraiser", "Athlete Spotlight", "Business Spotlight", "Event Flyer", "Free Stuff", "Job", "Home for Rent or Sale"]
PRIVATE_REPLY = ["Free Stuff", "Job", "Home for Rent or Sale"]  # people answer these through the reply form, so the poster's email is required


def field(name, label, kind="text", req=False, hint="", show=None, opts=None, extra=None, wide=False, consent=None, need=False):
    """One labeled input. show = sections it appears for; extra = label to fold it into the summary;
    consent = label to keep it in the private consent record that only editors see;
    need = required, but only when its section shows it (browsers can't check hidden required fields)."""
    attrs = f' name="{name}" id="f-{name}"' + (" required" if req else "") + (" data-need" if need else "")
    if extra:
        attrs += f' data-extra="{e(extra)}"'
    if consent:
        attrs += f' data-consent="{e(consent)}"'
    if kind == "checkbox":
        vis = f' data-for="{e("|".join(show))}" hidden' if show else ""
        return f'<label class="check wide"{vis}><input type="checkbox" value="Yes"{attrs}> <span>{label}</span></label>'
    if kind == "select":
        ctl = f'<select{attrs}><option value="">Choose one</option>' + "".join(
            f'<option>{e(o)}</option>' for o in opts) + "</select>"
    elif kind == "textarea":
        ctl = f'<textarea{attrs} rows="5" maxlength="2000"></textarea>'
    elif kind == "file":
        ctl = f'<input type="file" accept="image/jpeg,image/png,image/webp,image/gif"{attrs}>'
    elif kind == "url":  # plain text so "sunnymansfield.com" is accepted; https:// is added for them
        ctl = f'<input type="text" inputmode="url" autocapitalize="off" spellcheck="false" placeholder="sunnymansfield.com" data-url{attrs}>'
    else:
        ctl = f'<input type="{kind}"{attrs}' + (' maxlength="200"' if kind == "text" else "") + ">"
    vis = f' data-for="{e("|".join(show))}" hidden' if show else ""
    cls = "fld wide" if wide or kind == "textarea" else "fld"
    hint = f'<span class="hint">{hint}</span>' if hint else ""
    return f'<label class="{cls}"{vis}><span class="caps">{label}{" *" if req else ""}</span>{ctl}{hint}</label>'


def sunny_form(cfg, fid, section, fields, button, title_tpl="", signin=False, verify=False):
    """verify=True: after sending, the sender types a 6-digit code we email them (the "verify" function)."""
    on = backend_on(cfg)
    sign = ""
    if signin:
        sign = """<div class="signin fld wide"><span class="caps">Verified contributor?</span>
<p class="hint" data-out>Sign in with the email and password your editor gave you, and your posts go live on their own.</p>
<p class="hint" data-in hidden>Signed in as <b data-who></b>. <button class="textbtn" type="button" data-signout>Sign out</button></p>
<div class="row" data-out><input type="email" placeholder="you@example.com" data-signin-email aria-label="Email to sign in" autocomplete="username">
<input type="password" placeholder="password" data-signin-pass aria-label="Password" autocomplete="current-password">
<button class="btn sm" type="button" data-signin>Sign in</button></div>
<p class="hint" data-out>No password? <button class="textbtn" type="button" data-signin-link>Email me a sign-in link</button></p></div>"""
    closed = "" if on else '<p class="formnote">Submissions open soon. Until then, email <b>' + e(cfg["submit_email"]) + "</b>.</p>"
    check = ("""<div class="verify" hidden><span class="script">Almost done!</span><h2 class="hed">Check your email</h2>
<p>We sent a 6-digit code to <b data-v-email></b>. Type it here to send your shout-out to our editors.</p>
<div class="row"><input inputmode="numeric" autocomplete="one-time-code" maxlength="6" placeholder="123456" data-v-code aria-label="6-digit code">
<button class="btn solid" type="button" data-v-check>Confirm</button></div>
<p class="formmsg" data-v-msg role="status" aria-live="polite"></p>
<p class="hint">No email after a minute? Check your spam folder, or <button class="textbtn" type="button" data-v-resend>email me a new code</button>.</p></div>""" if verify else "")
    return f"""<form class="sunny" id="{fid}" data-section="{e(section)}" data-title="{e(title_tpl)}"{" data-verify=\"1\"" if verify else ""} novalidate>
{sign}{"".join(fields)}
<label class="hp" aria-hidden="true">Leave this empty<input name="hp" tabindex="-1" autocomplete="off"></label>
<label class="check wide"><input type="checkbox" required name="agree" value="Yes" data-consent="Agreed to rules, terms, photo permission and OK from people named"> <span>I agree to the <a class="link" href="{e(cfg['site_url'].rstrip('/'))}/rules.html#rules" target="_blank">Sunny rules</a> and <a class="link" href="{e(cfg['site_url'].rstrip('/'))}/terms.html" target="_blank">terms</a>, have permission to share any photo I add, and everyone named or pictured said it's OK (a parent or guardian for anyone under 18). <a class="link" href="{e(cfg['site_url'].rstrip('/'))}/privacy.html" target="_blank">Privacy</a></span></label>
<div class="wide submitrow"><button class="btn solid" type="submit"{"" if on else " disabled"}>{button}</button><span class="formmsg" role="status" aria-live="polite"></span></div>
{closed}</form>
<div class="thanks" hidden><span class="script">Thank you!</span><h2 class="hed">Got it.</h2><p data-thanks></p>
<button class="btn" type="button" data-again>Send another</button></div>{check}"""


def form_script(cfg):
    if not backend_on(cfg):
        return ""
    conf = json.dumps({"url": cfg["supabase_url"], "key": cfg["supabase_anon_key"]})
    return f"""<script src="{SB_JS}"></script><script>
(()=>{{const C={conf};const sb=supabase.createClient(C.url,C.key);
const COLS=['section','title','summary','link','name','role','business','town','event_date','price','email','consent'];
const PRIVATE={json.dumps(PRIVATE_REPLY)};
// Re-draw photos before upload: drops the hidden location (GPS) and camera details phones save in them, and shrinks big ones.
async function clean(file){{let bmp;try{{bmp=await createImageBitmap(file,{{imageOrientation:'from-image'}})}}catch(_){{throw new Error('We could not read that photo. Try a JPG or PNG.')}}
 const k=Math.min(1,2000/Math.max(bmp.width,bmp.height));const c=document.createElement('canvas');c.width=Math.round(bmp.width*k);c.height=Math.round(bmp.height*k);
 c.getContext('2d').drawImage(bmp,0,0,c.width,c.height);const type=file.type==='image/png'?'image/png':'image/jpeg';
 const blob=await new Promise(r=>c.toBlob(r,type,0.86));if(!blob)throw new Error('We could not read that photo. Try a JPG or PNG.');return blob}}
let who=null;
function syncSign(){{document.querySelectorAll('[data-out]').forEach(x=>x.hidden=!!who);document.querySelectorAll('[data-in]').forEach(x=>x.hidden=!who);
 document.querySelectorAll('[data-who]').forEach(x=>x.textContent=who||'');document.querySelectorAll('input[name=email]').forEach(x=>{{if(who){{x.value=who;x.readOnly=true}}else x.readOnly=false}})}}
sb.auth.getSession().then(r=>{{who=r.data.session?.user?.email||null;syncSign()}});
sb.auth.onAuthStateChange((_,s)=>{{who=s?.user?.email||null;syncSign()}});
document.addEventListener('click',async ev=>{{
 const b=ev.target.closest('[data-signin],[data-signin-link]');if(b){{const box=b.closest('.signin');const em=box.querySelector('[data-signin-email]').value.trim();const msg=box.querySelector('[data-out].hint');
  if(!/^\S+@\S+\.\S+$/.test(em)){{msg.textContent='Type your email first.';return}}b.disabled=true;let error;
  if(b.hasAttribute('data-signin')){{const pw=box.querySelector('[data-signin-pass]').value;if(!pw){{msg.textContent='Type your password, or use the email link.';b.disabled=false;return}}
   ({{error}}=await sb.auth.signInWithPassword({{email:em,password:pw}}));msg.textContent=error?'Could not sign in: '+error.message:''}}
  else{{({{error}}=await sb.auth.signInWithOtp({{email:em,options:{{emailRedirectTo:location.href.split('#')[0],shouldCreateUser:false}}}}));
   msg.textContent=error?'Could not send the link: '+error.message:'Check your email for the sign-in link, then come back here.'}}
  b.disabled=false;return}}
 if(ev.target.closest('[data-signout]')){{await sb.auth.signOut();return}}
 const a=ev.target.closest('[data-again]');if(a){{const t=a.closest('.thanks');t.hidden=true;t.previousElementSibling.hidden=false;return}}}});
function fixUrls(f){{f.querySelectorAll('[data-url]').forEach(x=>{{const v=x.value.trim();if(v&&!/^https?:\/\//i.test(v))x.value='https://'+v.replace(/^\/+/,'')}})}}
document.addEventListener('focusout',ev=>{{if(ev.target.matches&&ev.target.matches('[data-url]'))fixUrls(ev.target.form)}});
function showFor(f){{const sec=f.dataset.section||(f.section?f.section.value:'');f.querySelectorAll('[data-for]').forEach(x=>x.hidden=!x.dataset.for.split('|').includes(sec))}}
document.querySelectorAll('form.sunny').forEach(f=>{{showFor(f);f.addEventListener('change',ev=>{{if(ev.target.name==='section')showFor(f)}});
 f.addEventListener('submit',async ev=>{{ev.preventDefault();const msg=f.querySelector('.formmsg');const btn=f.querySelector('[type=submit]');
  fixUrls(f);if(!f.reportValidity())return;
  const val=n=>{{const x=f.querySelector('[name='+n+']');return x&&!x.closest('[hidden]')?(x.type==='checkbox'?x.checked:x.value.trim()):null}};
  const sec=f.dataset.section||val('section');
  const missing=[...f.querySelectorAll('[data-need]')].find(x=>!x.closest('[hidden]')&&(x.type==='checkbox'?!x.checked:!x.value.trim()));
  if(missing){{msg.textContent=missing.type==='checkbox'?'Please check the box that starts \u201c'+missing.nextElementSibling.textContent.slice(0,30)+'\u2026\u201d.':'Please fill in '+missing.closest('label').querySelector('.caps').textContent.replace(' *','')+'.';missing.focus();return}}
  if(PRIVATE.includes(sec)&&!/^\S+@\S+\.\S+$/.test(val('email')||'')){{msg.textContent='Add your email so people can reply. It is never shown on the site.';f.email.focus();return}}
  const pub=[val('title'),val('summary')].join(' ');const pii=/(\(?\d{{3}}\)?[\s.-]?\d{{3}}[\s.-]?\d{{4}})|([\w.+-]+@[\w-]+\.[\w.-]+)/.test(pub);
  if(pii&&PRIVATE.includes(sec)){{msg.textContent='Please take your phone number or email out of the post. People reply through the site, so you stay private.';return}}
  if(pii&&!f.dataset.piiOk){{f.dataset.piiOk=1;msg.textContent='Heads up: there is a phone number or email in your post, and it will be public. Take it out, or press Send again to keep it.';return}}
  if(sec==='Home for Rent or Sale'&&/\\b(no (kids|children|families)|adults only|singles only|couples only|perfect for (a )?(single|couple)|(christians?|whites?|americans?) only|no (wheelchairs?|disabled)|english speakers? only)\\b/i.test(pub)){{msg.textContent='Fair housing laws don\u2019t allow preferences about who can live there (like no kids or adults only). Please describe the home instead.';return}}
  if(sec==='Free Stuff'&&/\$\s?\d|\\b(obo|or best offer|for sale|asking|venmo|cash ?app|zelle|paypal|wanted|wtb|iso)\\b/i.test(pub)){{msg.textContent='Free Stuff is free only. Please take out any price, trade or payment app.';return}}
  if(val('minor')===''){{msg.textContent='Please tell us if the athlete is under 18.';f.minor.focus();return}}
  if(val('minor')==='Yes'&&(!val('guardian')||!val('guardian_ok'))){{msg.textContent='For an athlete under 18, add the parent or guardian name and check that they said OK.';f.guardian.focus();return}}
  const thanks=f.nextElementSibling;
  const done=text=>{{f.reset();delete f.dataset.piiOk;showFor(f);syncSign();f.hidden=true;thanks.querySelector('[data-thanks]').textContent=text;thanks.hidden=false;thanks.scrollIntoView({{behavior:'smooth',block:'center'}})}};
  if(f.hp.value){{done('Thanks!');return}}
  const fd=new FormData(f);const row={{}};const extras=[];const consent=[];
  f.querySelectorAll('[name]').forEach(el=>{{if(el.closest('[hidden]')||(el.type==='checkbox'&&!el.checked))return;const v=(el.type==='file'?'':String(el.value||'').trim());if(!v)return;
   if(el.dataset.consent)consent.push(el.dataset.consent+': '+v);else if(el.dataset.extra)extras.push(el.dataset.extra+': '+v);else if(COLS.includes(el.name))row[el.name]=v}});
  if(consent.length)row.consent=consent.join('\\n');
  if(f.dataset.section)row.section=f.dataset.section;
  if(extras.length)row.summary=[row.summary,...extras].filter(Boolean).join('\\n');
  if(f.dataset.title)row.title=f.dataset.title.replace(/\{{(\w+)\}}/g,(_,k)=>String(fd.get(k)||'').trim()).slice(0,200);
  btn.disabled=true;msg.textContent='Sending…';
  try{{const file=fd.get('photo');
   if(file&&file.size&&!f.querySelector('[name=photo]').closest('[hidden]')){{
    if(file.size>6*1024*1024)throw new Error('That photo is over 6 MB. Try a smaller one.');
    msg.textContent='Uploading photo…';const pic=await clean(file);const ext=pic.type==='image/png'?'png':'jpg';
    const rid=(self.crypto&&crypto.randomUUID)?crypto.randomUUID():Date.now().toString(36)+'-'+Math.random().toString(36).slice(2,12);const path=new Date().getFullYear()+'/'+rid+'.'+ext;
    const up=await sb.storage.from('photos').upload(path,pic,{{contentType:pic.type}});if(up.error)throw up.error;
    row.photo_url=sb.storage.from('photos').getPublicUrl(path).data.publicUrl}}
   if(f.dataset.verify)row.id=uuid();
   let {{error}}=await sb.from('submissions').insert(row);
   if(error&&/consent/.test(error.message||'')){{delete row.consent;({{error}}=await sb.from('submissions').insert(row))}}  // until the consent column exists
   if(error)throw error;
   if(f.dataset.verify){{let why='';try{{await vcall({{action:'send',id:row.id,email:row.email}})}}catch(x){{why=x.message||String(x)}}
    msg.textContent='';delete f.dataset.piiOk;showVerify(f,row.id,row.email,why);return}}
   msg.textContent='';done(f.dataset.section==='Join the team'?'Your application is in. An editor will email you soon.':
    f.dataset.section==='Advertise'?'We will get back to you within two business days.':
    sec==='Birthday'?'Your shout-out is in the review queue. Once an editor approves it, it shows on their birthday.':
    sec==='Job'?'Your job is in the review queue. Once an editor approves it, replies come to your email.':
    who?'If you are verified it goes live within 20 minutes. Otherwise an editor will look at it soon.':'It is in the review queue. An editor will look at it soon, usually within a day.')}}
  catch(err){{msg.textContent='Something went wrong: '+(err.message||err)+'. Please try again.'}}
  finally{{btn.disabled=false}}}});}});
/*VERIFY*/
document.querySelectorAll('[data-pick]').forEach(b=>b.addEventListener('click',()=>{{const f=document.getElementById('send');if(!f)return;f.section.value=b.dataset.pick;showFor(f)}}));
}})();
</script>""".replace("/*VERIFY*/", VERIFY_JS)


VERIFY_JS = r"""// Email check (birthday shout-outs): the row is saved unverified, we email a code, and a match sends it to the queue.
const uuid=()=>(self.crypto&&crypto.randomUUID)?crypto.randomUUID():'10000000-1000-4000-8000-100000000000'.replace(/[018]/g,c=>(c^crypto.getRandomValues(new Uint8Array(1))[0]&15>>c/4).toString(16));
async function vcall(body){const {data,error}=await sb.functions.invoke('verify',{body});
 if(error){let t='';try{t=(await error.context.json()).error}catch(_){}throw new Error(t||error.message)}return data}
function showVerify(f,id,email,why){const v=f.parentElement.querySelector('.verify');f.hidden=true;v.hidden=false;v.dataset.id=id;v.dataset.email=email;
 v.querySelector('[data-v-email]').textContent=email;v.querySelector('[data-v-code]').value='';
 v.querySelector('[data-v-msg]').textContent=why?why+' Press “email me a new code” to try again.':'';
 v.scrollIntoView({behavior:'smooth',block:'center'});v.querySelector('[data-v-code]').focus({preventScroll:true})}
document.addEventListener('keydown',ev=>{if(ev.key==='Enter'&&ev.target.matches&&ev.target.matches('[data-v-code]')){ev.preventDefault();ev.target.closest('.verify').querySelector('[data-v-check]').click()}});
document.addEventListener('click',async ev=>{const c=ev.target.closest('[data-v-check],[data-v-resend]');if(!c)return;const v=c.closest('.verify');const m=v.querySelector('[data-v-msg]');c.disabled=true;
 try{if(c.hasAttribute('data-v-resend')){await vcall({action:'send',id:v.dataset.id,email:v.dataset.email});m.textContent='New code sent. Check your email.';return}
  const code=v.querySelector('[data-v-code]').value.replace(/\D/g,'');if(code.length!==6){m.textContent='Type the 6-digit code from the email.';return}
  m.textContent='Checking…';await vcall({action:'check',id:v.dataset.id,code});m.textContent='';v.hidden=true;
  const f=v.parentElement.querySelector('form.sunny');const t=f.nextElementSibling;f.reset();showFor(f);syncSign();
  t.querySelector('[data-thanks]').textContent='Confirmed! Your shout-out is in the review queue. Once an editor approves it, it shows on their birthday.';
  t.hidden=false;t.scrollIntoView({behavior:'smooth',block:'center'})}
 catch(err){m.textContent=err.message||String(err)}finally{c.disabled=false}});"""


def form_link(cfg, key, subject):
    return cfg.get(key) or f"mailto:{cfg['submit_email']}?subject=" + urllib.parse.quote(subject)


def page_title(script, title, intro):
    return (f'<section class="wrap pagehead"><span class="script">{script}</span><h1 class="retro sm">{title}</h1>'
            f'<p class="intro">{intro}</p></section>')


BIRTHDAY_STEPS = [
    "Type their name and their birthday. No photo, no year, nothing else needed.",
    "Want to say more? Check \u201cAdd a special message\u201d and write a line from you.",
    "Confirm with the 6-digit code we email you. Then an editor approves it. It goes up on our Facebook page on their birthday and stays on the site for two days.",
]


def birthday_cards(birthdays):
    return "".join(
        f'<article class="bday-card" id="bday-{e(b["id"])}"><span class="bday-cake" aria-hidden="true">{CAKE}</span>'
        f'<span class="caps kicker">{"Happy birthday" if b.get("today", True) else "Happy belated birthday"}</span><h3 class="bday-name">{e(b["name"])}</h3>'
        + (f'<p class="bday-msg">\u201c{e(b["message"])}\u201d</p>' if b["message"] else "")
        + (f'<p class="caps bday-from">\u2014 {e(b["by"])}</p>' if b["by"] else "") + "</article>"
        for b in birthdays)


CAKE = ('<svg viewBox="0 0 48 48"><path d="M24 4c2 3 2 5 0 7-2-2-2-4 0-7z" fill="currentColor"/><path d="M22.5 12h3v8h-3z" fill="currentColor"/>'
        '<path d="M8 22h32v8H8zM6 30h36v12H6z" fill="none" stroke="currentColor" stroke-width="2.5"/>'
        '<path d="M8 26c3 3 5 3 8 0s5-3 8 0 5 3 8 0 5-3 8 0" fill="none" stroke="currentColor" stroke-width="2.5"/></svg>')


def birthday_rail(cfg, birthdays, title="Birthdays", page=False):
    """Birthday shout-outs as a carousel shelf, like the other sections. On the front page it has a See all link
    to birthdays.html; on that page it has a Share link instead. With no birthdays it asks for the first one."""
    site = cfg["site_url"].rstrip("/")
    share = (f'<a class="link" href="{e(fb_share(site + "/birthdays.html"))}" target="_blank" rel="noopener">Share</a>'
             if birthdays else "")
    more = sponsor_tag(cfg, "birthday", site) + (share if page else f'<a class="link" href="{e(site)}/birthdays.html">See all</a>')
    if not birthdays:
        return "" if not page else rail(e(title), first_post(cfg, "birthday"), more, rid="birthdays", kind="sec bdays")
    add = ('' if page else
           f'<a class="slide bday-card add" href="{e(site)}/birthdays.html#form"><span class="plus" aria-hidden="true">+</span>'
           f'<span class="caps kicker">Know a birthday?</span><span class="bday-name">Send a shout-out</span>'
           f'<span class="bday-from caps">Free · On the site and Facebook</span></a>')
    cards = birthday_cards(birthdays).replace('<article class="bday-card"', '<article class="slide bday-card"')
    return rail(e(title), cards + add, more, rid="birthdays", kind="sec bdays")


BIRTHDAY_JS = """<script>(()=>{const f=document.getElementById('bday-form');if(!f)return;
const m=f.querySelector('[name=bmonth]'),d=f.querySelector('[name=bday]'),out=f.querySelector('[name=event_date]');
const box=f.querySelector('.bday-extra'),opt=f.querySelector('[name=add_message]');
function days(){const mo=m.selectedIndex;const n=mo?new Date(2000,mo,0).getDate():31;const keep=d.value;
 d.innerHTML='<option value="">Day</option>'+Array.from({length:n},(_,i)=>`<option>${i+1}</option>`).join('');if(+keep<=n)d.value=keep;set()}
function set(){const mo=m.selectedIndex,dy=+d.value;out.value=mo&&dy?`2000-${String(mo).padStart(2,'0')}-${String(dy).padStart(2,'0')}`:''}
function toggle(){box.hidden=!opt.checked;if(opt.checked)box.querySelector('textarea').focus()}
m.addEventListener('change',days);d.addEventListener('change',set);opt.addEventListener('change',toggle);
f.addEventListener('reset',()=>setTimeout(()=>{days();box.hidden=true}));days()})();</script>"""

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def render_birthdays(cfg, birthdays, stories):
    site = cfg["site_url"].rstrip("/")
    today = birthday_rail(cfg, birthdays, f"Celebrating now · {eastern().strftime('%A, %B %-d')}", page=True)
    month = '<label class="fld"><span class="caps">Birthday *</span><span class="bday-date">' + (
        '<select name="bmonth" id="f-bmonth" required aria-label="Month"><option value="">Month</option>'
        + "".join(f"<option>{m}</option>" for m in MONTHS) + '</select>'
        '<select name="bday" id="f-bday" required aria-label="Day"><option value="">Day</option></select></span>'
        '<span class="hint">Just the month and day. We never ask for the year.</span></label>')
    fields = [
        field("who", "Birthday person\u2019s name", req=True, hint="As it should appear, like \u201cGrandma Rose\u201d or \u201cJake Miller\u201d."),
        month,
        '<input type="hidden" name="event_date">',
        field("add_message", "Add a special message <span class=\"muted\">(optional)</span>", "checkbox"),
        '<div class="bday-extra wide" hidden>'
        + field("summary", "Your message", "textarea", hint="A line or two, up to 300 characters. It shows with their shout-out.").replace('maxlength="2000"', 'maxlength="300" rows="3"').replace(' rows="5"', "")
        + field("name", "From", hint="Who it's from, like \u201cLove, Mom and Dad\u201d or \u201cThe crew at Sample Coffee\u201d.")
        + "</div>",
        field("email", "Your email", "email", req=True, hint="We email you a 6-digit code to make sure it's really you. Never shown on the site."),
        field("bday_ok", "Yes, post it. I know them and they'd be happy with a public birthday shout-out on Sunny Mansfield "
              "and its Facebook page (for anyone under 18, I'm their parent or guardian, or have their OK).", "checkbox",
              req=True, consent="Birthday person OK with a public shout-out"),
    ]
    form = sunny_form(cfg, "bday-form", "Birthday", fields, "Send for review", title_tpl="Happy birthday, {who}!", verify=True)
    inner = f"""{page_title("Hey neighbor!", "Birthday shout-outs", "Know someone with a birthday coming up? Send a free shout-out and we'll wish them a happy birthday on the front page and on Facebook, on their big day.")}
{today}
<section class="wrap formbox" id="form"><h2 class="label">Send a birthday shout-out</h2>{form}</section>
<section class="wrap civic"><div class="box"><span class="caps kicker">How it works</span><h2 class="hed">Three quick steps</h2>
<ol class="steps">{"".join(f'<li><span class="n">{i}</span><span>{t}</span></li>' for i, t in enumerate(BIRTHDAY_STEPS, 1))}</ol></div>
<div class="box"><span class="caps kicker">Good to know</span><h2 class="hed">Free, kind and once a year</h2>
<ol class="steps"><li><span class="n">1</span><span>Send it any time before the day. Each shout-out runs once, on their next birthday.</span></li>
<li><span class="n">2</span><span>Use the name they go by. For kids, a parent or guardian should send it.</span></li>
<li><span class="n">3</span><span>Keep it kind and keep phone numbers, emails and addresses out of your message: it's public. <a class="link" href="{e(site)}/rules.html">All the rules</a></span></li></ol></div></section>"""
    image = f"{site}/birthday.jpg"
    return shell(cfg, f"Birthday shout-outs · {cfg['site_name']}",
                 "Send a free birthday shout-out to someone in Mansfield or Richland County. It shows on their birthday.",
                 f"{site}/birthdays.html", inner, stories, image=image, extra=form_script(cfg) + BIRTHDAY_JS)


def render_submit(cfg, stories):
    site = cfg["site_url"].rstrip("/")
    story = "#form"
    paths = [
        ("good", "Share good news", "A ribbon cutting, a 100th birthday, a team win, a garage sale, a neighbor who helps. "
         "Tell us in a few lines and add a photo.", story, "Submit a story", "submit"),
        ("biz", "Spotlight your business", "Free for Richland County businesses. You get your own page on Sunny Mansfield "
         "with your photo, your story and a button people can share.", "#form:Business Spotlight", "Get a spotlight", "spotlight"),
        ("vote", "Post an event flyer", "Concerts, fish fries, fundraisers, festivals, church suppers. Upload your flyer and "
         "it rides the front page carousel until the day of the event.", "#form:Event Flyer", "Post a flyer", "flyer"),
        ("seniors", "Share a fundraiser", "Raising money for a neighbor, a team or a special cause? Post your GoFundMe or "
         "donation link and we'll help spread the word. You're responsible for your fundraiser and how the money is used.", "#form:Fundraiser", "Post a fundraiser", "fundraiser"),
        ("sports", "Spotlight an athlete", "A big game, a personal best, a signing day, a kid who never quits. Send a photo "
         "and a few lines and they get their own page to share.", "#form:Athlete Spotlight", "Spotlight an athlete", "athlete"),
        ("sales", "Give away free stuff", "Clearing out the garage? Give it to a neighbor. Free only: no prices or trades. "
         "People message you through the site, so your phone and email stay private. Up for 30 days.", "#form:Free Stuff", "Give something away", "free"),
        ("biz", "Post a job", "Hiring in Richland County? Post it free for 30 days. Applicants reply through Sunny Mansfield, "
         "so your email is never shown.", "#form:Job", "Post a job", "job"),
        ("hoods", "List a home for rent or sale", "Renting out a house or apartment, or selling one in Richland County? List it "
         "free for 30 days with the price up front. People ask about it through the site, so your phone and email stay private.",
         "#form:Home for Rent or Sale", "List a home", "home"),
        ("seniors", "Send a birthday shout-out", "Just their name and birthday, plus a special message if you like. "
         "It shows on the front page and our Facebook page on their big day.", f"{site}/birthdays.html#form", "Send a shout-out", "birthday"),
        ("research", "Join the team", "Aspiring journalists, marketers, students, mentors and motivators. Get verified and "
         "your posts go live on their own.", f"{site}/rules.html#apply", "Apply to join", "join"),
    ]

    def go(href, cta):
        if href.startswith("#form"):
            pick = href.split(":", 1)[1] if ":" in href else "Good News"
            return f'<a class="btn solid" href="#form" data-pick="{e(pick)}">{cta}</a>'
        return f'<a class="btn solid" href="{e(href)}">{cta}</a>'
    cards = "".join(f"""<article class="card path" id="{anchor}"><span class="art sm"><svg viewBox="0 0 100 100" aria-hidden="true">{ART[art]}</svg></span>
<h2 class="hed">{title}</h2><p class="dek">{text}</p><div class="share">{go(href, cta)}</div></article>"""
                    for art, title, text, href, cta, anchor in paths)
    labels = [n for _, n in SECTIONS] + EXTRA_SECTIONS
    fields = [
        field("section", "What are you sharing?", "select", True, opts=labels),
        field("title", "Headline", req=True, hint="Short and specific, like \u201cOntario band raises $3,000 with a car wash\u201d."),
        field("summary", "Tell us about it", "textarea", True, "Two to four sentences. Who, what, where, when."),
        field("photo", "Photo or flyer", "file", hint="JPG or PNG up to 6 MB. Your own photo, or one you have permission to use."),
        field("event_date", "Event date", "date", show=["Event Flyer"]),
        field("category", "Category", "select", show=["Free Stuff"], opts=[n for _, n, _ in MARKET_CATS], extra="Category"),
        field("free_ok", "It's free: no price, trade or \u201cgas money\u201d. It's legal to give away and not recalled, and I'm 18 or older.",
              "checkbox", show=["Free Stuff"], consent="Free item rules OK", need=True),
        field("business", "Employer", show=["Job"], need=True, hint="The business or person hiring."),
        field("jobtype", "Job type", "select", show=["Job"], opts=JOB_TYPES, extra="Type"),
        field("price", "Pay", show=["Job"], hint="Optional, like $16 an hour or $45,000 a year."),
        field("job_ok", "This is a real job in Richland County. Applying is free, it isn't commission-only or a "
              "\u201cbusiness opportunity\u201d, I follow equal-opportunity laws, and I'm 18 or older and allowed to hire for it.",
              "checkbox", show=["Job"], consent="Job rules OK", need=True),
        field("listing", "For rent or for sale?", "select", show=[HOME_LABEL], opts=HOME_KINDS, extra="Listing", need=True),
        field("price", "Price", show=[HOME_LABEL], need=True, hint="Like $950 a month or $149,900. Every listing shows its price."),
        field("beds", "Bedrooms", "select", show=[HOME_LABEL], opts=["Studio", "1", "2", "3", "4", "5+"], extra="Beds"),
        field("baths", "Bathrooms", "select", show=[HOME_LABEL], opts=["1", "1.5", "2", "2.5", "3+"], extra="Baths"),
        field("business", "Listed by", show=[HOME_LABEL], hint="Optional: your name, property manager or real estate agency."),
        field("home_ok", "This home is in Richland County and I own it or I'm allowed to list it. I follow fair housing laws "
              "(no preferences about race, color, religion, sex, disability, family status, national origin, ancestry or military status), "
              "I'll never ask for money before someone sees it in person, and I'm 18 or older.",
              "checkbox", show=[HOME_LABEL], consent="Home rules OK", need=True),
        field("business", "Business name", show=["Business Spotlight"]),
        field("business", "Special cause", show=["Fundraiser"], hint="Optional. Fundraisers with the same cause, like \u201cBellville flood relief\u201d, are grouped together."),
        field("price", "Goal", show=["Fundraiser"], hint="Optional, like $5,000."),
        field("event_date", "Last day to give", "date", show=["Fundraiser"], hint="Optional. Leave blank and it stays up 90 days."),
        field("sport", "Sport", show=["Athlete Spotlight"], extra="Sport", hint="Like Volleyball or Cross country."),
        field("school", "School or team", show=["Athlete Spotlight"], extra="School"),
        field("grad", "Class of", show=["Athlete Spotlight"], extra="Class of", hint="Graduation year, like 2027."),
        field("minor", "Is this athlete under 18?", "select", show=["Athlete Spotlight"], opts=["No", "Yes"], consent="Under 18"),
        field("guardian", "Parent or guardian's name", show=["Athlete Spotlight"], consent="Parent or guardian",
              hint="Needed if the athlete is under 18. Only editors see it."),
        field("guardian_ok", "Their parent or guardian said it's OK to share this post and photo.", "checkbox",
              show=["Athlete Spotlight"], consent="Parent or guardian OK"),
        field("link", "Link for more", "url", hint="Optional: a website, event page or Facebook post. For a fundraiser, the GoFundMe or donation page."),
        field("name", "Your name as it should appear", req=True),
        field("role", "You are a", "select", opts=["Neighbor", "Student reporter", "Business", "Mentor", "Organization"]),
        field("town", "Town or neighborhood"),
        field("email", "Your email", "email", hint="Never shown on the site. Editors use it if they have a question. "
              "For Free Stuff, jobs and homes it's required: replies are sent here."),
    ]
    inner = f"""{page_title("Hey neighbor!", "Got good news?", "Sunny Mansfield is written by the people who live here. Pick what you want to share. It takes about two minutes.")}
<section class="wrap latest"><div class="grid">{cards}</div></section>
<section class="wrap formbox" id="form"><h2 class="label">Send it in</h2>{sunny_form(cfg, "send", "", fields, "Send for review", signin=True)}</section>
<section class="wrap civic"><div class="box"><span class="caps kicker">What happens next</span><h2 class="hed">From your phone to the front page</h2>
<ol class="steps">
<li><span class="n">1</span><span>You fill out the short form above. It goes into our review queue.</span></li>
<li><span class="n">2</span><span>An editor approves it, usually within a day. <b>Verified contributors</b> who sign in skip the line and go live within 20 minutes.</span></li>
<li><span class="n">3</span><span>Your story gets its own page with your name on it and a Share button, so friends and family can pass it along on Facebook.</span></li>
</ol></div>
<div class="box"><span class="caps kicker">Before you send</span><h2 class="hed">Three quick rules</h2>
<ol class="steps">
<li><span class="n">1</span><span>Keep it true, kind and local to Richland County.</span></li>
<li><span class="n">2</span><span>Use your own photos, or ones you have permission to share. Ask before posting people, and a parent for kids.</span></li>
<li><span class="n">3</span><span>No attacks, gossip or campaigning. Keep phone numbers, emails and home addresses out of what you write: it's all public. <a class="link" href="{e(site)}/rules.html">All the rules</a></span></li>
</ol></div></section>"""
    return shell(cfg, f"Submit Good News · {cfg['site_name']}",
                 "Share good news from Mansfield and Richland County, Ohio: post an event flyer, spotlight a business or athlete, "
                 "share a fundraiser or job. Free, and an editor reviews every post.",
                 f"{site}/submit.html", inner, stories, extra=form_script(cfg))


def render_rules(cfg, stories):
    site = cfg["site_url"].rstrip("/")
    join = "#apply"
    rules = [
        ("Keep it true.", "Real people, real places and real events. If you didn't see it yourself, link to where it came from."),
        ("Keep it kind.", "We celebrate. No put-downs, gossip, rumors or call-outs, even when someone deserves one."),
        ("Keep it local.", "Mansfield, Ontario, Lexington, Shelby, Bellville, Butler, Lucas, Plymouth and everywhere in Richland County."),
        ("Your words, your photos.", "Write it yourself and use photos you took or have permission to share. Never copy a whole article."),
        ("Ask before you post people.", "Get an OK from anyone pictured or named, and a parent or guardian's OK for anyone under 18."),
        ("Civic, not political.", "Voting dates, forums and how-to are welcome. Endorsements, attack posts and campaign ads are not."),
        ("Honest business.", "Spotlights are free, one a month per business. Jobs must be real and free to apply for. Free Stuff is free. No fake reviews, fake deals or pay-to-win."),
        ("Editors have the last word.", "We may trim for length, fix spelling, or decline a post. We'll fix mistakes fast: just tell us."),
    ]
    rule_items = "".join(f'<li><span class="n">{i}</span><span><b>{a}</b> {b}</span></li>' for i, (a, b) in enumerate(rules, 1))
    roles = [
        ("good", "Community editors", "Review new posts, pick the lead story and keep the paper sunny. A few minutes a day."),
        ("research", "Student reporters", "Aspiring journalists in high school or college. Real bylines for your portfolio and "
         "college applications, with a mentor to help."),
        ("biz", "Business correspondents", "Marketers and shop owners who tell the stories of other local businesses, not just their own."),
        ("seniors", "Mentors and motivators", "Teachers, coaches, pastors and leaders who share wins and help new writers grow."),
    ]
    role_cards = "".join(f"""<article class="card role"><span class="art sm"><svg viewBox="0 0 100 100" aria-hidden="true">{ART[a]}</svg></span>
<h3 class="hed">{t}</h3><p class="dek">{d}</p></article>""" for a, t, d in roles)
    inner = f"""{page_title("Good news, by neighbors", "Join the team", "Sunny Mansfield is a free, open paper for the good things happening around Richland County. Anyone can send a story. Verified contributors publish directly.")}
<section class="wrap latest"><h2 class="label">Who we're looking for</h2><div class="grid four">{role_cards}</div></section>
<section class="wrap civic">
<div class="box" id="verify"><span class="caps kicker">Get verified</span><h2 class="hed">How it works</h2>
<ol class="steps">
<li><span class="n">1</span><span><b>Apply.</b> A two-minute form: your name, your town, and what you'd like to cover.</span></li>
<li><span class="n">2</span><span><b>Quick check.</b> An editor confirms you're a real person with a Richland County connection. Under 18? We'll need a parent or guardian's OK.</span></li>
<li><span class="n">3</span><span><b>Post freely.</b> Once verified, your posts go live on their own within 2 hours, with your name on them.</span></li>
</ol>
<div class="share"><a class="btn solid" href="{e(join)}">Apply to join</a><a class="textbtn" href="{e(site)}/submit.html">Or just send a story</a></div></div>
<div class="box" id="rules"><span class="caps kicker">The Sunny rules</span><h2 class="hed">Eight rules, one idea: lift people up</h2>
<ol class="steps">{rule_items}</ol>
<p class="note">Break the rules and your post comes down. Break them twice and verification is removed.</p></div>
</section>
<section class="wrap formbox" id="apply"><h2 class="label">Apply to join</h2>{sunny_form(cfg, "apply-form", "Join the team", [
        field("name", "Your name", req=True),
        field("email", "Your email", "email", True, "We'll email you when you're verified."),
        field("role", "Role", "select", True, opts=["Community editor", "Student reporter", "Business correspondent", "Mentor and motivator"]),
        field("town", "Town or neighborhood"),
        field("summary", "What would you like to cover?", "textarea", True),
        field("age", "Are you 18 or older?", "select", True, opts=["Yes", "No"], extra="18 or older"),
        field("guardian", "If not: parent or guardian's name and email", extra="Parent or guardian", wide=True),
        field("link", "Portfolio, school paper or social link", "url"),
    ], "Send my application", title_tpl="{name}: {role}")}</section>"""
    return shell(cfg, f"Join the team · {cfg['site_name']}", "Become a verified contributor: editors, student reporters, business correspondents, mentors.",
                 f"{site}/rules.html", inner, stories, extra=form_script(cfg))


LEGAL = {
    "privacy": ("Privacy policy", "What we collect, who sees it, and how to get it removed.", [
        ("Who we are", "Sunny Mansfield is a free community news site published by Ofgeneration LLC in Mansfield, Ohio. "
         "Questions about this policy go to {email}."),
        ("What we collect", "Only what you type into our forms: your name, your email, your town, what you write, any link "
         "or photo you add, and for jobs the employer and pay. Team applications also ask your role and, "
         "for anyone under 18, a parent or guardian's name. If you reply to a Free Stuff or job post, we collect the name, email "
         "and message you type. We don't use tracking cookies, ads trackers or analytics."),
        ("What is public", "Once an editor approves a post, its headline, words, photo, link, the name you give, your role and "
         "town appear on the site, in our RSS feeds, and on our Facebook Page. Photos you upload are stored at a public web "
         "address, so only add photos you're happy to share. Before a photo leaves your device, our form redraws it to strip "
         "out hidden details phones save, like the GPS location where it was taken. Please keep phone numbers, emails and home "
         "addresses out of what you write, because it's public."),
        ("Private replies", "Free Stuff, job and home posts never show the poster's email or phone. People answer through a form, "
         "and we email their name, email address and message to the poster. When the poster writes back, the other person "
         "sees the poster's email address, so only reply to people you're comfortable with. To stop spam we keep a scrambled "
         "(hashed) record of each reply for 30 days, not the message itself."),
        ("What stays private", "Your email address is never published. Only our editors can see it, and we use it only to ask "
         "about your post or reply to you. Team applications and advertising questions are never published."),
        ("Email newsletter", "If you sign up for our weekly email, we collect your email address through beehiiv, our "
         "newsletter service. We email you once to confirm, and every newsletter has an unsubscribe link. We use the address only "
         "to send the newsletter and never sell or share it."),
        ("Who helps us run the site", "Our database and photo storage are hosted by Supabase, the site is hosted by GitHub Pages, "
         "sign-in emails and private replies are sent through Resend, the newsletter is sent through beehiiv, and approved posts are shared to Facebook through Zapier. They process data "
         "only to provide those services. We never sell your information."),
        ("How long we keep it", "Approved posts stay up until they're taken down. Event flyers come down after the event, "
         "Free Stuff, job and home posts after 30 days, and fundraisers after 90 days or their end date. We erase the email address "
         "on Free Stuff, job and home posts 60 days after they're posted. Posts we decline are never published and are deleted "
         "after 30 days. We keep the record of what you agreed to when you posted, as proof of permission."),
        ("Children", "Sunny Mansfield is for a general audience and isn't meant for children under 13 to use on their own. "
         "Posts about anyone under 18 need a parent or guardian's OK. A parent can ask us to remove a post or photo of their "
         "child at any time, and we will."),
        ("Your choices", "Ask us to correct or remove anything you sent, or anything about you, by emailing {email} or using "
         "the \u201cReport or remove a post\u201d link at the bottom of every page. We act on removal requests as fast as we can, "
         "usually the same day, and remove the Facebook copy too."),
        ("Your privacy rights", "We don't sell or share your personal information for money. Wherever you live, you can ask "
         "us what we have about you, ask us to correct or delete it, and opt out of personalized ads as described above. "
         "Email {email} and we'll answer within 30 days."),
        ("Changes", "If we change this policy we'll update this page and the date below."),
    ]),
    "terms": ("Terms of use", "The ground rules for reading and posting on Sunny Mansfield.", [
        ("Using the site", "Sunny Mansfield is free to read. By sending a post you agree to these terms and to the Sunny rules. "
         "You must be 13 or older to send a post, and 18 or older to post Free Stuff or a job."),
        ("What you send", "You keep ownership of what you write and the photos you take. You give Ofgeneration LLC permission "
         "to publish, edit for length and clarity, and share your post on this site, in our feeds and on our social media pages. "
         "You can ask us to take it down at any time."),
        ("Your promises", "You promise that what you send is true as far as you know, is yours to share, and that you have the "
         "OK of anyone named or pictured, including a parent or guardian's OK for anyone under 18. Don't send anything "
         "hurtful, private, illegal, political campaigning, or copied from someone else."),
        ("Editors decide", "We review posts before they appear, except for verified contributors, and we may decline, edit or "
         "remove any post. \u201cVerified\u201d means an editor confirmed who the contributor is. It doesn't mean every post has been fact-checked."),
        ("Fundraisers, Free Stuff and jobs", "Fundraisers, Free Stuff and jobs are posted by neighbors and employers, not by "
         "us. We don't run, verify or endorse them, we don't handle any money, and we aren't part of any giveaway or hire. We "
         "aren't an employer or staffing agency. Give, pick up and apply with care."),
        ("Free Stuff rules", "Free means free: no prices, trades, deposits or \u201cgas money\u201d. No weapons or ammunition, "
         "alcohol, tobacco or vapes, medicine, live animals, or recalled or unsafe items like expired car seats. You're "
         "responsible for what you give away."),
        ("Job rules", "Only real jobs in or near Richland County. Applying must be free. No commission-only work, \u201cbusiness "
         "opportunities\u201d, multi-level marketing or work-at-home kits, and never ask applicants for a Social Security "
         "number, bank details or money before hiring them. Follow equal-opportunity laws: no preferences based on race, "
         "color, religion, sex, national origin, ancestry, age, disability, military status or other protected traits. "
         "The employer is responsible for the post and every hiring decision."),
        ("Home listing rules", "Only homes in Richland County that you own or are allowed to list, with the real price. "
         "Follow fair housing laws: no preferences or limits based on race, color, religion, sex, disability, family status, "
         "national origin, ancestry or military status. Never ask for a deposit, fee or rent before someone has seen the home "
         "in person. We don't check listings, handle money or act as a landlord or agent. The person listing is responsible "
         "for the post."),
        ("Private replies", "Use the reply form only to answer the post. No spam, sales pitches or harassment. We may block "
         "anyone who misuses it, and we can't promise every message is delivered."),
        ("Stories from other outlets", "Headlines, short summaries and photos from other news outlets belong to their publishers "
         "and always link back to the original. Publishers who'd like their stories left out can email {email}."),
        ("No guarantees", "We work hard to get things right, but the site is provided as is. Ofgeneration LLC isn't responsible "
         "for posts written by neighbors or employers, for anything given away or any job, or for other websites we link to."),
        ("Your responsibility", "If something you post breaks these terms or the law and someone makes a claim against "
         "Ofgeneration LLC because of it, you agree to cover the costs that claim causes us."),
        ("Copyright", "If you think something here uses your work without permission, email {email} with a link to it and "
         "what you own. We'll take it down while we look into it."),
        ("Problems and takedowns", "If something here is wrong, hurtful or yours and posted without permission, email {email} "
         "or use the \u201cReport or remove a post\u201d link and we'll look at it right away."),
    ]),
}


GOOGLE_PRIVACY = ("We advertise Sunny Mansfield on Google and use the Google tag to learn which ads bring readers here. It "
                  "sets cookies that tell Google you visited, which pages you opened and whether you came from one of our ads. "
                  "We see only totals, never who you are, and we don't sell or share anything you type into our forms. Google's "
                  "use of this data is covered by its privacy policy (policies.google.com/privacy). You can turn off "
                  "personalized ads at adssettings.google.com, or block cookies in your browser and the site still works.")


ADSENSE_PRIVACY = ("To help keep Sunny Mansfield free, we show ads from Google AdSense. Google and its partners use cookies to "
                   "show ads, including ads based on your visits to this and other websites. You can turn off personalized ads at "
                   "adssettings.google.com. Google's use of advertising cookies is explained at "
                   "policies.google.com/technologies/ads. You can also opt out of many companies' personalized ads at "
                   "optout.aboutads.info. Ads from Google are labeled, and we don't pick or endorse them. "
                   "Local ads you see in our Advertise spots are sold by us and don't track you.")


def render_about(cfg, stories):
    """about.html: who publishes Sunny Mansfield, what it covers, how stories get here, and how corrections work.
    A real About page is one of the things Google's AdSense reviewers and searchers look for."""
    site = cfg["site_url"].rstrip("/")
    email = f'<a class="link" href="mailto:{e(cfg["submit_email"])}">{e(cfg["submit_email"])}</a>'
    towns = ", ".join(t for t, _ in TOWNS[:-1]) + " and " + TOWNS[-1][0]
    sections = [
        ("What Sunny Mansfield is",
         f"<p>Sunny Mansfield is a free local news site for Mansfield and Richland County, Ohio, that only shares good news. "
         f"We cover {e(towns)}, and the places in between. Created for the people and written by the people, it's where neighbors "
         f"find the wins, openings, events and helping hands that don't always make the headlines.</p>"),
        ("What we cover",
         f'<ul><li><a class="link" href="{e(site)}/">Good news</a> from around the county, every day.</li>'
         f'<li><a class="link" href="{e(site)}/events.html">Events and things to do</a>, posted by the people putting them on.</li>'
         f'<li><a class="link" href="{e(site)}/sports.html">High school sports</a>, athlete spotlights and tournament brackets.</li>'
         f'<li><a class="link" href="{e(site)}/pets.html">Tyger Paws</a>, a dog of the day from the Richland County Dog Shelter.</li>'
         f'<li><a class="link" href="{e(site)}/businesses.html">Local business spotlights</a>, '
         f'<a class="link" href="{e(site)}/fundraisers.html">fundraisers</a>, '
         f'<a class="link" href="{e(site)}/free.html">free stuff</a> and '
         f'<a class="link" href="{e(site)}/jobs.html">local jobs</a>.</li>'
         f'<li><a class="link" href="{e(site)}/birthdays.html">Birthday shout-outs</a> and a weekly email newsletter.</li></ul>'
         f"<p>We skip crime, crashes and political arguments. There's plenty of that elsewhere.</p>"),
        ("Where our stories come from",
         f"<p>Two places. First, neighbors: students, parents, coaches, business owners, churches and nonprofits send posts through "
         f'our <a class="link" href="{e(site)}/submit.html">submit form</a>, and an editor reads every one before it goes up. '
         f"Second, other local outlets: when Richland Source, the Mansfield News Journal or another local newsroom reports good news, "
         f"we share the headline and a short preview and link straight to their story, so they get the visit and the credit. "
         f"We never copy whole articles. Please read and support them.</p>"),
        ("Our standards",
         f'<p>Every post follows the <a class="link" href="{e(site)}/rules.html#rules">Sunny rules</a>: keep it true, keep it kind, '
         f"keep it local, and only share words and photos you have the right to share. Posts about anyone under 18 need a parent or "
         f"guardian's OK. An editor gives every fundraiser a quick look before it appears, but we don't run or verify them, and "
         f"each organizer is responsible for their own fundraiser.</p>"),
        ("Corrections and takedowns",
         f"<p>If we get something wrong, we fix it. If you're in a post and want it changed or taken down, we'll do that too, usually "
         f'the same day. Use the "Report or remove a post" link at the bottom of any page, or email {email}.</p>'),
        ("Advertising",
         f"<p>Sunny Mansfield is free to read. To pay for it, we show ads from Google AdSense, which are labeled, and from time to time "
         f"local sponsors. Advertisers never decide what we cover, and ads are always kept apart from stories. "
         f'Our <a class="link" href="{e(site)}/privacy.html">privacy policy</a> explains how ads use cookies.</p>'),
        ("Who we are",
         f"<p>Sunny Mansfield is published by Ofgeneration LLC, a small company based in Richland County, Ohio. "
         f'Questions, tips or ideas? <a class="link" href="{e(site)}/contact.html">Contact us</a>. '
         f'Want to help? <a class="link" href="{e(site)}/rules.html#apply">Join the team</a> as an editor, student reporter or '
         f"business correspondent.</p>"),
    ]
    body = "".join(f'<h2 class="hed" id="{slug(h)}">{e(h)}</h2>{t}' for h, t in sections).replace(
        'id="corrections-and-takedowns"', 'id="corrections"')
    inner = f"""{page_title("Welcome, neighbor!", "About Sunny Mansfield", "Good news from Mansfield and Richland County, Ohio. Created for the people. Written by the people.")}
<section class="wrap legaltext">{body}</section>"""
    data = [{"@context": "https://schema.org", "@type": "AboutPage", "name": "About Sunny Mansfield", "url": f"{site}/about.html",
             "about": {"@id": f"{site}/#org"}, "isPartOf": {"@id": f"{site}/#site"}},
            breadcrumbs([("Sunny Mansfield", f"{site}/"), ("About", f"{site}/about.html")])]
    return shell(cfg, f"About Sunny Mansfield · Good News from Mansfield, Ohio",
                 "Sunny Mansfield is a free, good-news-only local news site for Mansfield and Richland County, Ohio. "
                 "Who we are, what we cover, where stories come from and how we fix mistakes.",
                 f"{site}/about.html", inner, stories, data=data)


def render_contact(cfg, stories):
    """contact.html: one place for every way to reach the editors."""
    site = cfg["site_url"].rstrip("/")
    mail = cfg["submit_email"]
    email = f'<a class="link" href="mailto:{e(mail)}">{e(mail)}</a>'
    fb = (f'<li><b>Facebook.</b> Message us on our <a class="link" href="{e(cfg["facebook_page"])}" target="_blank" rel="noopener">'
          f'Facebook Page</a>.</li>') if cfg.get("facebook_page") else ""
    ads = (f'<a class="link" href="{e(site)}/advertise.html">See ad spots and prices</a>, or email' if ads_open(cfg) else "Email")
    inner = f"""{page_title("Say hello", "Contact Sunny Mansfield", "Questions, tips, corrections or ideas: we read every message.")}
<section class="wrap legaltext">
<h2 class="hed">Email</h2><p>The fastest way to reach an editor is {email}.</p>
<h2 class="hed">Share good news</h2><p>Have a story, an event, a fundraiser or a business to spotlight? <a class="link" href="{e(site)}/submit.html">Send it with our free form</a>. An editor reviews every post.</p>
<h2 class="hed">Fix or remove a post</h2><p>Spot a mistake, or are you in a post and want it changed or taken down? <a class="link" href="{e(report_link(cfg))}">Tell an editor</a> and we'll fix it fast, usually the same day.</p>
<h2 class="hed">Advertising and sponsorships</h2><p>{ads} {email} with "Advertising" in the subject.</p>
<h2 class="hed">Other ways to reach us</h2><ul>{fb}<li><b>Mail.</b> {e(mailing_address(cfg))}</li></ul>
<p class="hint">Sunny Mansfield is published by Ofgeneration LLC. <a class="link" href="{e(site)}/about.html">About us</a> · <a class="link" href="{e(site)}/privacy.html">Privacy</a> · <a class="link" href="{e(site)}/terms.html">Terms</a></p>
</section>"""
    data = [{"@context": "https://schema.org", "@type": "ContactPage", "name": "Contact Sunny Mansfield", "url": f"{site}/contact.html",
             "about": {"@id": f"{site}/#org"}, "isPartOf": {"@id": f"{site}/#site"}},
            breadcrumbs([("Sunny Mansfield", f"{site}/"), ("Contact", f"{site}/contact.html")])]
    return shell(cfg, f"Contact Us · {cfg['site_name']}",
                 "Contact the Sunny Mansfield editors: email, mail and Facebook. Share good news, fix or remove a post, or ask about "
                 "advertising in Mansfield and Richland County, Ohio.", f"{site}/contact.html", inner, stories, data=data)


def render_legal(cfg, stories, kind):
    site = cfg["site_url"].rstrip("/")
    title, desc, parts = LEGAL[kind]
    email = f'<a class="link" href="mailto:{e(cfg["submit_email"])}">{e(cfg["submit_email"])}</a>'
    if kind == "privacy" and (cfg.get("google_tag") or adsense_pub(cfg)):
        what = ("Google's measurement and advertising cookies, described below," if adsense_pub(cfg)
                else "Google's measurement tag, described below,")
        parts = [(h, t.replace("We don't use tracking cookies, ads trackers or analytics.",
                               f"We use {what} and no other trackers.")) for h, t in parts]
        extra = ([("Google ads and measurement", GOOGLE_PRIVACY)] if cfg.get("google_tag") else []) + \
                ([("Ads on this site", ADSENSE_PRIVACY)] if adsense_pub(cfg) else [])
        parts[2:2] = extra
    parts = list(parts) + [("Contact us", "Email {email}, or write to us at {address}.")]
    body = "".join(f'<h2 class="hed">{e(h)}</h2><p>{e(t).replace("{address}", mailing_address(cfg)).replace("{email}", email)}</p>'
                   for h, t in parts)
    inner = f"""{page_title("The fine print", title, desc)}
<section class="wrap legaltext">{body}<p class="hint">Last updated November 18, 2026.</p></section>"""
    return shell(cfg, f"{title} · {cfg['site_name']}", desc, f"{site}/{kind}.html", inner, stories)


def biz_card(cfg, b):
    img = (f'<img src="{e(b["image"])}" alt="" loading="lazy" referrerpolicy="no-referrer">' if b.get("image")
           else f'<span class="art"><svg viewBox="0 0 100 100" aria-hidden="true">{ART["biz"]}</svg></span>')
    return (f'<article class="card"><a class="photo" href="{e(biz_url(cfg, b))}" tabindex="-1" aria-hidden="true">{img}</a>'
            f'<span class="caps kicker">{e(b.get("neighborhood") or "Richland County")}</span>'
            f'<h3 class="hed"><a href="{e(biz_url(cfg, b))}">{e(b["name"])}</a></h3><p class="dek">{e(b["what"])}</p>'
            f'<div class="share"><a class="btn fb" href="{e(fb_share(biz_url(cfg, b)))}" target="_blank" rel="noopener">{FB_ICON}Share</a>'
            f'<a class="textbtn" href="{e(biz_url(cfg, b))}">See their page</a></div></article>')


def render_businesses(cfg, businesses, stories):
    site = cfg["site_url"].rstrip("/")
    cards = "".join(biz_card(cfg, b) for b in businesses)
    inner = f"""{page_title("Shop local", "Business spotlights", "Every Richland County business can have a free page here. Share your favorites, and tell them you saw them on Sunny Mansfield.")}
<section class="wrap"><p class="note">Money spent at a local shop stays in Richland County: it pays neighbors' wages, sponsors Little League
teams and keeps Main Street lights on. These spotlights are free, written with the owners, and never paid placements. Know a business
that deserves one? <a class="link" href="{e(site)}/submit.html#spotlight">Tell us about it</a>.</p></section>
<section class="wrap latest"><div class="grid">{cards}</div>
<div class="more-row"><a class="btn solid" href="{e(site)}/submit.html#spotlight">Spotlight your business</a></div></section>"""
    return shell(cfg, f"Local Business Spotlights in Mansfield, Ohio · {cfg['site_name']}",
                 "Free spotlights for local shops, restaurants and services in Mansfield and Richland County, Ohio. "
                 "Find a new favorite and support your neighbors.",
                 f"{site}/businesses.html", inner, stories)


def render_business(cfg, b, businesses, stories):
    site = cfg["site_url"].rstrip("/")
    img = (f'<span class="photo"><img src="{e(b["image"])}" alt="{e(b["name"])}" referrerpolicy="no-referrer"></span>'
           if b.get("image") else f'<span class="photo"><span class="art"><svg viewBox="0 0 100 100" aria-hidden="true">{ART["biz"]}</svg></span></span>')
    visit = f'<a class="textbtn" href="{e(b["link"])}" target="_blank" rel="noopener">Visit their website</a>' if b.get("link") else ""
    by = f'<div class="meta"><span>Spotlight by {e(b["by"])}</span></div>' if b.get("by") else ""
    others = "".join(biz_card(cfg, o) for o in [o for o in businesses if o is not b][:3])
    inner = f"""<section class="wrap front" style="grid-template-columns:minmax(0,1fr)"><article class="lead bizhero">{img}
<span class="caps kicker">Business spotlight · {e(b.get("neighborhood") or "Richland County")}</span><h1 class="hed">{e(b["name"])}</h1>
<p class="dek">{e(b["what"])}</p>{by}
<div class="share"><a class="btn fb" href="{e(fb_share(biz_url(cfg, b)))}" target="_blank" rel="noopener">{FB_ICON}Share this business</a>{visit}</div></article></section>
<section class="wrap latest">{f'<h2 class="label">More local businesses</h2><div class="grid">{others}</div>' if others else ""}
<div class="more-row"><a class="btn" href="{e(site)}/businesses.html">See all businesses</a></div></section>"""
    image = b["image"] if (b.get("image") or "").startswith("https://") else None
    place = {"@context": "https://schema.org", "@type": "LocalBusiness", "name": b["name"], "description": b["what"],
             "url": b.get("link") or biz_url(cfg, b), "image": image or f"{site}/og.png",
             "address": {"@type": "PostalAddress", "addressLocality": b.get("neighborhood") or "Mansfield",
                         "addressRegion": "OH", "addressCountry": "US"}}
    town = b.get("neighborhood") or "Richland County"
    crumbs = breadcrumbs([("Sunny Mansfield", f"{site}/"), ("Business spotlights", f"{site}/businesses.html"),
                          (b["name"], biz_url(cfg, b))])
    return shell(cfg, f"{b['name']} in {town}, Ohio · Local business spotlight · {cfg['site_name']}",
                 f"{b['name']}, a local business in {town}, Ohio. {b['what']}"[:300], biz_url(cfg, b), inner, stories, image,
                 data=[place, crumbs], index=not b.get("sample"))


def parse_bracket(text):
    """Rounds of games from lines like "Semifinals" and "Madison 3 vs Ontario 1"."""
    rounds = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        sides = re.split(r"\s+vs\.?\s+", line, maxsplit=1, flags=re.I)
        if len(sides) < 2:
            rounds.append({"name": line.rstrip(":"), "games": []})
            continue
        if not rounds:
            rounds.append({"name": "", "games": []})
        game = []
        for side in sides:
            star = "*" in side
            side = side.replace("*", "").strip()
            m = re.fullmatch(r"(.*?)\s+\(?(\d{1,3})\)?", side)
            name, score = (m.group(1), int(m.group(2))) if m else (side, None)
            game.append({"name": name or "TBD", "score": score, "won": star})
        a, b = game
        if not (a["won"] or b["won"]) and a["score"] is not None and b["score"] is not None and a["score"] != b["score"]:
            (a if a["score"] > b["score"] else b)["won"] = True
        rounds[-1]["games"].append(game)
    return [r for r in rounds if r["games"]]


def render_bracket(cfg, b):
    rounds = parse_bracket(b["text"])
    if not rounds:
        return ""
    cols = []
    for r in rounds:
        games = "".join(
            '<div class="game">' + "".join(
                f'<div class="team{" won" if t["won"] else ""}"><span>{e(t["name"])}</span>'
                f'<b>{"" if t["score"] is None else t["score"]}</b></div>' for t in g) + "</div>"
            for g in r["games"])
        cols.append(f'<div class="round"><h4 class="caps">{e(r["name"])}</h4><div class="games">{games}</div></div>')
    last = rounds[-1]["games"]
    champ = next((t["name"] for t in last[0] if t["won"]), "") if len(last) == 1 else ""
    if champ:
        cols.append(f'<div class="round champ"><h4 class="caps">Champion</h4><div class="games"><div class="trophy">'
                    f'<svg viewBox="0 0 100 100" aria-hidden="true">{ART["sports"]}</svg><span class="hed">{e(champ)}</span>'
                    f'</div></div></div>')
    more = f'<a class="link" href="{e(b["link"])}" target="_blank" rel="noopener">Full details</a>' if b.get("link") else ""
    return (f'<article class="bracketbox" id="b-{b["id"]}"><div class="rail-head"><h3 class="hed">{e(b["title"])}</h3>{more}</div>'
            f'<div class="bracket" tabindex="0" style="--rounds:{len(cols)}">{"".join(cols)}</div>'
            f'<span class="caps muted">Updated {ago(b["date"])}</span></article>')


def athlete_card(cfg, s):
    f = s.get("athlete") or {}
    line = " · ".join(x for x in (f.get("Sport"), f.get("School"), f"Class of {f['Class of']}" if f.get("Class of") else "") if x)
    html = card(cfg, s).replace('<article class="card"', '<article class="card slide athlete"', 1)
    return html.replace(kicker(s), f'<span class="caps kicker sport">{e(line)}</span>', 1) if line else html


def render_sports(cfg, stories, brackets):
    site = cfg["site_url"].rstrip("/")
    athletes = [s for s in stories if s.get("athlete") is not None]
    news = [s for s in stories if s["section"] == "sports" and s.get("athlete") is None]
    add = (f'<a class="slide flyer add" href="{e(site)}/submit.html#athlete"><span class="flyer-img"><span class="plus">+</span></span>'
           f'<span class="hed">Spotlight an athlete</span><span class="dek">Free. Parents, coaches and teammates welcome.</span></a>')
    spot = rail("Athlete spotlights", "".join(athlete_card(cfg, s) for s in athletes) + add, rid="athletes", kind="athletes")
    boxes = "".join(render_bracket(cfg, b) for b in brackets) or (
        '<p class="empty">Tournament brackets show up here during the playoffs. Coaches and boosters can send results to '
        f'<a class="link" href="{e(site)}/submit.html">Submit news</a>.</p>')
    grid = "".join(card(cfg, s) for s in news) or first_post(cfg, "sports", block=True)
    inner = f"""{page_title("Go team!", "Sports", "Wins, personal bests, signing days and the kids who never quit. Richland County athletes, cheered on by their neighbors.")}
{banner(cfg, "A5")}
{spot}
<section class="wrap latest" id="brackets"><h2 class="label">Brackets</h2>{boxes}</section>
<section class="wrap latest"><h2 class="label">Sports news</h2><div class="grid">{grid}</div></section>"""
    return shell(cfg, f"Richland County High School Sports · {cfg['site_name']}",
                 "Richland County, Ohio high school sports: athlete spotlights, signing days, playoff brackets and good sports news "
                 "from Mansfield, Ontario, Lexington, Madison, Clear Fork and more.",
                 f"{site}/sports.html", inner, stories)


PAW = ('<circle cx="30" cy="36" r="8"/><circle cx="46" cy="24" r="8"/><circle cx="64" cy="26" r="8"/><circle cx="76" cy="42" r="8"/>'
       '<path d="M52 46c-12 0-24 14-24 26 0 8 6 12 13 12 5 0 7-3 11-3s6 3 11 3c7 0 13-4 13-12 0-12-12-26-24-26z"/>')


def dog_photo(d, size=500):
    big = (d.get("big") or "").replace("h_1000", f"h_{size}")
    inner = (f'<img src="{e(big or d["image"])}" alt="{e(d["name"])}, an adoptable {e(d["breed"] or "dog")}" loading="lazy" '
             f'referrerpolicy="no-referrer"' + (f' data-thumb="{e(d["image"])}"' if big else "") + '>'
             if d.get("image") else f'<span class="art"><svg viewBox="0 0 100 100" aria-hidden="true">{PAW}</svg></span>')
    return f'<a class="photo" href="{e(d["url"])}" target="_blank" rel="noopener" tabindex="-1" aria-hidden="true">{inner}</a>'


def dog_line(d):
    return " · ".join(x for x in (d.get("age"), d.get("sex"), d.get("breed")) if x)


def dog_card(cfg, d, day=False):
    """An adoptable dog, built like a fundraiser card: photo (with a Dog of the day badge on today's dog), age and sex,
    breed tag, name, a two-line note, Meet link, and Adopt and Share buttons."""
    shelter = (cfg.get("shelter") or {}).get("name", "Richland County Dog Shelter")
    big = (d.get("big") or "").replace("h_1000", "h_600")
    img = (f'<img src="{e(big or d["image"])}" alt="{e(d["name"])}, an adoptable {e(d.get("breed") or "dog")}" loading="lazy" '
           f'referrerpolicy="no-referrer"' + (f' data-thumb="{e(d["image"])}"' if big else "") + '>'
           if d.get("image") else f'<span class="art"><svg viewBox="0 0 100 100" aria-hidden="true">{PAW}</svg></span>')
    badge = ('<span class="goal"><span class="goal-k">Dog of</span><span class="goal-v">the day</span>'
             '<span class="goal-k">Meet me!</span></span>') if day else ""
    url = e(d["url"])
    who = " · ".join(x for x in (d.get("age"), d.get("sex")) if x) or "Adoptable"
    breed = f'<span class="cause">{e(d["breed"])}</span>' if d.get("breed") else ""
    return (f'<article class="slide listing fund dogcard"><a class="photo" href="{url}" target="_blank" rel="noopener" tabindex="-1" aria-hidden="true">{img}{badge}</a>'
            f'<span class="caps kicker">{e(who)}</span>{breed}'
            f'<h3 class="hed"><a href="{url}" target="_blank" rel="noopener">{e(d["name"])}</a></h3>'
            f'<p class="dek">{e(d["name"])} is waiting at the {e(shelter)} for a family.</p>'
            f'<a class="caps more-link" href="{url}" target="_blank" rel="noopener">Meet {e(d["name"])} <span aria-hidden="true">&rsaquo;</span></a>'
            f'<span class="tagline">{e(shelter)}</span>'
            f'<div class="share"><a class="btn solid sm" href="{url}" target="_blank" rel="noopener">Adopt</a>'
            f'<a class="btn fb sm" href="{e(fb_share(d["url"]))}" target="_blank" rel="noopener">{FB_ICON}Share</a></div></article>')


def dogs_in_order(dogs):
    """Today's dog of the day first, then the rest in the shelter's order."""
    d = dog_of_the_day(dogs)
    return ([d] if d else []) + [x for x in dogs if x is not d]


def shelter_note(cfg):
    shelter = cfg.get("shelter") or {}
    link = (f' <a class="link" href="{e(shelter["page"])}" target="_blank" rel="noopener">See the shelter\'s page</a> for visiting hours and how to adopt.'
            if shelter.get("page") else "")
    return (f'<p class="give-note"><svg class="paw" viewBox="0 0 100 100" aria-hidden="true">{PAW}</svg><span><b>Every dog here is at the '
            f'{e(shelter.get("name", "Richland County Dog Shelter"))}.</b> Photos and details come from the shelter\'s adoption listings.{link}</span></p>')


def happy_tails_box(cfg, dogs):
    """The front page's Tyger Paws spot, laid out like the Donate box: the shelter's dogs on a shelf, dog of the day first."""
    site = cfg["site_url"].rstrip("/")
    if not dogs:
        return ""
    cards = "".join(dog_card(cfg, d, day=i == 0) for i, d in enumerate(dogs_in_order(dogs)[:12]))
    return (f'<section class="wrap givebox" id="happy-tails"><div class="rail-head"><h2 class="label">Tyger Paws</h2>'
            f'<a class="link" href="{e(site)}/pets.html">See all</a></div><div class="give"><div class="give-top">'
            f'<div><span class="script">Tyger Paws!</span><h2 class="retro sm">Adopt a shelter dog</h2>'
            f'<p>A new dog of the day every morning from the Richland County Dog Shelter. Can\'t adopt? A share might reach the right home.</p></div>'
            f'<div class="share"><a class="btn solid sm" href="{e(site)}/pets.html">See all {len(dogs)}</a>'
            f'<a class="btn sm" href="{e(site)}/submit.html">Share an adoption story</a></div></div>'
            + rail("Waiting for a home", cards).replace('class="wrap railbox ', 'class="railbox ')
            + shelter_note(cfg) + '</div></section>')


def series_cta(cfg, key):
    site = cfg["site_url"].rstrip("/")
    label, href, button, examples = FIRST_POST[key]
    return (f'<section class="wrap latest"><p class="note" style="text-align:center">{e(examples)} '
            f'<a class="link" href="{e(site)}/{href}">{e(button)}</a></p></section>')


def series_others(cfg, key):
    site = cfg["site_url"].rstrip("/")
    links = " · ".join(f'<a class="link" href="{e(site)}/{page}">{e(name)}</a>' for k, page, name, _, _ in SERIES if k != key)
    return f'<section class="wrap latest"><p class="note" style="text-align:center">More daily series: {links}</p></section>'


def render_series(cfg, key, items, stories, dogs=()):
    site = cfg["site_url"].rstrip("/")
    _, page, name, script, about = next(x for x in SERIES if x[0] == key)
    grid = "".join(card(cfg, s) for s in items) or first_post(cfg, key, block=True)
    top = ""
    if key == "pets":
        shelter = cfg.get("shelter") or {}
        d = dog_of_the_day(dogs)
        if d:
            top = (f'<section class="wrap latest dogday-wrap"><h2 class="label">Dog of the day</h2><article class="dogday">{dog_photo(d, 900)}'
                   f'<div><span class="caps kicker">{e(dog_line(d))}</span><h3 class="hed"><a href="{e(d["url"])}" target="_blank" rel="noopener">Meet {e(d["name"])}</a></h3>'
                   f'<p class="dek">{e(d["name"])} is waiting at the {e(shelter.get("name", "Richland County Dog Shelter"))} for a family. '
                   f'Share this post and you might help {e(d["name"])} find a home today.</p>'
                   f'<div class="share"><a class="btn solid" href="{e(d["url"])}" target="_blank" rel="noopener">Adopt {e(d["name"])}</a>'
                   f'<a class="btn fb" href="{e(fb_share(d["url"]))}" target="_blank" rel="noopener">{FB_ICON}Share</a></div></div></article></section>')
            top += (f'<section class="wrap givebox"><div class="give"><div class="give-top"><div><span class="script">Neighbors helping dogs</span>'
                    f'<h2 class="retro sm">Waiting for a home ({len(dogs)})</h2></div></div>'
                    f'<div class="grid dogs">{"".join(dog_card(cfg, x) for x in dogs)}</div>{shelter_note(cfg)}</div></section>')
    head_label = "Happy endings and shelter news"
    inner = f"""{page_title(script, e(name), e(about))}
{top}
<section class="wrap latest"><h2 class="label">{head_label}</h2><div class="grid">{grid}</div></section>
{series_cta(cfg, key)}
{series_others(cfg, key)}"""
    data = [{"@context": "https://schema.org", "@type": "CollectionPage", "name": name, "url": f"{site}/{page}",
             "description": about, "isPartOf": {"@id": f"{site}/#site"}},
            breadcrumbs([("Sunny Mansfield", f"{site}/"), (name, f"{site}/{page}")])]
    return shell(cfg, f"{name} · {cfg['site_name']}", about, f"{site}/{page}", inner, stories, data=data)


def render_fund_page(cfg, f, funds, stories):
    """One fundraiser: the full picture, goal, dates and organizer, Donate and Share, the story formatted like
    the story pages, the before-you-give note and a Report link, with more local causes beside it."""
    site = cfg["site_url"].rstrip("/")
    url = f"{site}{fund_url(f)}"
    img = (f'<div class="event-flyer"><img src="{e(img_src(f["image"]))}" alt="{e(f["title"])}" referrerpolicy="no-referrer"></div>'
           if f.get("image") else "")
    about = format_writeup(f.get("summary") or "") or "<p>Details coming soon.</p>"
    facts = [("Goal", money(f["goal"])) if f.get("goal") else None,
             ("Until", f["day"].strftime("%B %-d, %Y")) if f.get("day") else None,
             ("Where", f"{f.get('town') or 'Richland County'}, Ohio"),
             ("Organized by", f["by"]) if f.get("by") else None]
    facts = "".join(f"<div><dt>{k}</dt><dd>{e(v)}</dd></div>" for k, v in filter(None, facts))
    give = (f'<a class="btn solid" href="{e(f["link"])}" target="_blank" rel="noopener nofollow ugc">Donate</a>' if f.get("link") else "")
    cause = f'<span class="cause">{e(f["cause"])}</span>' if f.get("cause") else ""
    others = [o for o in funds if o is not f][:6]
    rows = "".join(f'<a class="watch-row event-row" href="{e(fund_url(o))}"><span class="w-media">'
                   + (f'<img src="{e(img_src(o["image"]))}" alt="" loading="lazy" referrerpolicy="no-referrer">' if o.get("image") else "")
                   + f'</span><span><span class="caps kicker">{e("Goal " + money(o["goal"]) if o.get("goal") else (o.get("town") or "Richland County"))}</span>'
                   f'<span class="hed">{e(o["title"])}</span></span></a>' for o in others)
    aside = (f'<aside class="watch-aside">{section_head("More local causes")}<div class="watch-list">{rows}</div>'
             f'<a class="explore" href="{e(site)}/fundraisers.html">All fundraisers <span aria-hidden="true">&rsaquo;</span></a></aside>') if rows else ""
    report = f"mailto:{cfg['submit_email']}?subject={urllib.parse.quote('Report fundraiser: ' + f['title'])}"
    inner = f"""<section class="wrap watch-layout event-layout{"" if aside else " solo"}"><article class="watch-main">
<div class="event-top">{img}<header class="watch-head"><a class="caps kicker" href="{e(site)}/fundraisers.html">Donate to local causes</a>{cause}<h1 class="hed">{e(f["title"])}</h1>
<dl class="story-facts event-facts">{facts}</dl>
<div class="share">{give}<a class="btn fb" href="{e(fb_share(url))}" target="_blank" rel="noopener">{FB_ICON}Share on Facebook</a>
<button class="textbtn" type="button" data-copy="{e(url)}">Copy link</button></div></header></div>
<section class="wu-card open" aria-labelledby="fu-h"><h2 class="wu-head" id="fu-h">About this fundraiser</h2><div class="wu-body">{about}</div></section>
<p class="give-note">{SHIELD}<span>{FUND_SHORT} <a class="link" href="{e(site)}/fundraisers.html#before-you-give">Read before you give</a></span></p>
<p class="note story-note">Community post, not verified by us. Raising money for a neighbor? <a class="link" href="{e(site)}/submit.html#fundraiser">Share a fundraiser</a>.
Something wrong? <a class="link" href="{e(report)}">Report this fundraiser</a>.</p>
</article>{aside}</section>"""
    desc = " ".join((f.get("summary") or f["title"]).split())
    desc = desc if len(desc) <= 200 else desc[:197].rsplit(" ", 1)[0] + "\u2026"
    image = f["image"] if (f.get("image") or "").startswith("https://") else None
    title = f"{f['title']}{' · Goal ' + money(f['goal']) if f.get('goal') else ''} · {cfg['site_name']}"
    return shell(cfg, title, desc, url, inner, stories, image=image)


def render_fundraisers(cfg, funds, stories):
    site = cfg["site_url"].rstrip("/")
    groups = {}
    for f in funds:
        groups.setdefault(f["cause"].lower() if f["cause"] else "", []).append(f)
    named = [(fs[0]["cause"], fs) for k, fs in groups.items() if k and len(fs) > 1]
    rest = [f for f in funds if not f["cause"] or len(groups[f["cause"].lower()]) < 2]
    blocks = "".join(f'<section class="wrap latest causebox"><span class="script">Special cause</span><h2 class="label">{e(name)}</h2>'
                     f'<p class="hint">{len(fs)} fundraisers for this cause</p><div class="grid market-grid">{"".join(fund_card(cfg, f) for f in fs)}</div></section>'
                     for name, fs in named)
    more = "".join(fund_card(cfg, f) for f in rest)
    if more:
        blocks += f'<section class="wrap latest"><h2 class="label">{"More fundraisers" if named else "Fundraisers"}</h2><div class="grid market-grid">{more}</div></section>'
    if not funds:
        blocks = f'<section class="wrap latest">{first_post(cfg, "funds", block=True)}</section>'
    inner = f"""{page_title("Neighbors helping neighbors", "Donate to local causes", "Medical bills, team trips, fire recovery, school projects and special causes. Give if you can, and share if you can't.")}
<section class="wrap"><div class="more-row" style="margin:0 0 8px"><a class="btn solid" href="{e(site)}/submit.html#fundraiser">Share a fundraiser</a></div>
<p class="give-note">{SHIELD}<span>{FUND_SHORT} <a class="link" href="#before-you-give">Read before you give</a></span></p></section>
{blocks}
<section class="wrap latest" id="before-you-give"><h2 class="label">Read before you give</h2>
<ul class="givefacts">{"".join(f"<li><b>{t}</b>{d}</li>" for t, d in FUND_FACTS[:-1])}
<li><b>{FUND_FACTS[-1][0]}</b>{FUND_FACTS[-1][1]} Email <a class="link" href="mailto:{e(cfg['submit_email'])}">{e(cfg['submit_email'])}</a>.</li></ul>
<p class="note">Sunny Mansfield is a publication of Ofgeneration LLC. Fundraiser listings are provided as a free community service, as is, with no warranty.
Each organizer is responsible for their own fundraiser, its claims and its use of funds.</p></section>"""
    return shell(cfg, f"Local Fundraisers in Mansfield, Ohio · {cfg['site_name']}",
                 "Fundraisers and special causes from Mansfield and Richland County neighbors: medical bills, team trips, fire "
                 "recovery and school projects. Give if you can, share if you can't.",
                 f"{site}/fundraisers.html", inner, stories)


def town_items(name, stories, businesses, funds):
    good = [s for s in stories if s["section"] != "life" and name in story_towns(s)]
    shops = [b for b in businesses if not b.get("sample") and towns_of(b.get("neighborhood"), b["name"], b["what"])[:1] == [name]]
    causes = [f for f in funds if name in towns_of(f.get("town"), f["title"], f["summary"])]
    return good, shops, causes


def render_town(cfg, name, about, stories, businesses, funds):
    site = cfg["site_url"].rstrip("/")
    good, shops, causes = town_items(name, stories, businesses, funds)
    grid = "".join(card(cfg, s) for s in good) or f'<p class="empty">No {e(name)} stories this week yet. Know one? Send it in.</p>'
    shop = (f'<section class="wrap latest"><h2 class="label">Shop local in {e(name)}</h2><div class="grid">'
            + "".join(biz_card(cfg, b) for b in shops) + "</div></section>") if shops else ""
    give = (f'<section class="wrap latest"><h2 class="label">{e(name)} causes</h2><div class="grid">'
            + "".join(fund_card(cfg, f) for f in causes) + "</div></section>") if causes else ""
    others = " · ".join(f'<a class="link" href="{e(town_url(cfg, t))}">{e(t)}</a>' for t, _ in TOWNS if t != name)
    desc = (f"Good news from {name}, Ohio, {about}: community wins, local businesses, high school sports and neighbors "
            f"helping neighbors, updated all day by {cfg['site_name']}.")
    inner = f"""{page_title("Good news in", f"{e(name)}, Ohio", f"The latest positive stories from {e(name)}, {e(about)}. Shared by neighbors and gathered from local news, updated all day.")}
<section class="wrap latest"><h2 class="label">Good news from {e(name)}</h2><div class="grid">{grid}</div>
<p class="note" style="text-align:center">Something good happening in {e(name)}? <a class="link" href="{e(site)}/submit.html">Share it with your neighbors</a>.</p></section>
{shop}{give}
<section class="wrap latest"><p class="note" style="text-align:center">More Richland County towns: {others}</p></section>"""
    data = [{"@context": "https://schema.org", "@type": "CollectionPage", "name": f"Good news in {name}, Ohio",
             "url": town_url(cfg, name), "about": {"@type": "Place", "name": f"{name}, Ohio"},
             "isPartOf": {"@id": f"{site}/#site"}},
            breadcrumbs([("Sunny Mansfield", f"{site}/"), (f"Good news in {name}", town_url(cfg, name))])]
    return shell(cfg, f"Good News in {name}, Ohio · {cfg['site_name']}", desc, town_url(cfg, name), inner, stories,
                 data=data, index=bool(good or shops or causes))


def event_data(cfg, f):
    site = cfg["site_url"].rstrip("/")
    town = f.get("town") or "Mansfield"
    ev = {"@context": "https://schema.org", "@type": "Event", "name": f["title"], "startDate": f["day"].strftime("%Y-%m-%d"),
          "eventStatus": "https://schema.org/EventScheduled",
          "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
          "location": {"@type": "Place", "name": f"{town}, Ohio", "address": {"@type": "PostalAddress",
                       "addressLocality": town, "addressRegion": "OH", "addressCountry": "US"}},
          "url": f"{site}{event_url(f)}", "description": " ".join((f.get("summary") or f["title"]).split())[:500]}
    if (f.get("image") or "").startswith("https://"):
        ev["image"] = [f["image"]]
    return ev


def calendar_link(cfg, f):
    """Google Calendar 'add event' link for an all-day event on the flyer's date."""
    if not f.get("day"):
        return ""
    d0 = f["day"].strftime("%Y%m%d")
    d1 = (f["day"] + timedelta(days=1)).strftime("%Y%m%d")
    q = urllib.parse.urlencode({"action": "TEMPLATE", "text": f["title"], "dates": f"{d0}/{d1}",
                                "location": f"{f.get('town') or 'Mansfield'}, Ohio",
                                "details": f"{cfg['site_url'].rstrip('/')}{event_url(f)}"})
    return "https://calendar.google.com/calendar/render?" + q


def render_event_page(cfg, f, flyers, stories):
    """One event: the whole flyer (not cropped), date, town, Share and Add to calendar, then 'About this event'
    formatted like the story pages, with 'More local events' beside it."""
    site = cfg["site_url"].rstrip("/")
    url = f"{site}{event_url(f)}"
    when = f["day"].strftime("%A, %B %-d, %Y") if f.get("day") else "Date to be announced"
    img = (f'<div class="event-flyer"><img src="{e(img_src(f["image"]))}" alt="Flyer: {e(f["title"])}" referrerpolicy="no-referrer"></div>'
           if f.get("image") else "")
    about = format_writeup(f.get("summary") or "") or "<p>Details coming soon.</p>"
    cal = calendar_link(cfg, f)
    more_info = (f'<a class="btn" href="{e(f["link"])}" target="_blank" rel="noopener nofollow ugc">More info</a>' if f.get("link") else "")
    others = [o for o in flyers if o is not f][:6]
    rows = "".join(f'<a class="watch-row event-row" href="{e(event_url(o))}"><span class="w-media">'
                   + (f'<img src="{e(img_src(o["image"]))}" alt="" loading="lazy" referrerpolicy="no-referrer">' if o.get("image") else "")
                   + f'</span><span><span class="caps kicker">{e(o["day"].strftime("%a, %b %-d") if o.get("day") else "Coming up")}</span>'
                   f'<span class="hed">{e(o["title"])}</span></span></a>' for o in others)
    aside = (f'<aside class="watch-aside">{section_head("More local events")}<div class="watch-list">{rows}</div>'
             f'<a class="explore" href="{e(site)}/events.html">All events <span aria-hidden="true">&rsaquo;</span></a></aside>') if rows else ""
    data = event_data(cfg, f) if f.get("day") else None
    inner = f"""<section class="wrap watch-layout event-layout{"" if aside else " solo"}"><article class="watch-main">
<div class="event-top">{img}<header class="watch-head"><a class="caps kicker" href="{e(site)}/events.html">Local events</a><h1 class="hed">{e(f["title"])}</h1>
<dl class="story-facts event-facts"><div><dt>When</dt><dd>{e(when)}</dd></div><div><dt>Where</dt><dd>{e(f.get("town") or "Richland County")}, Ohio</dd></div></dl>
<div class="share"><a class="btn fb" href="{e(fb_share(url))}" target="_blank" rel="noopener">{FB_ICON}Share on Facebook</a>
{f'<a class="btn solid" href="{e(cal)}" target="_blank" rel="noopener">Add to calendar</a>' if cal else ""}{more_info}
<button class="textbtn" type="button" data-copy="{e(url)}">Copy link</button></div></header></div>
<section class="wu-card open" aria-labelledby="ev-h"><h2 class="wu-head" id="ev-h">About this event</h2><div class="wu-body">{about}</div></section>
<p class="note story-note">Posted by the people putting it on. Putting on an event? <a class="link" href="{e(site)}/submit.html#flyer">Post your flyer free</a>.
Something wrong? <a class="link" href="mailto:{e(cfg['submit_email'])}?subject={urllib.parse.quote('Event: ' + f['title'])}">Tell an editor</a>.</p>
</article>{aside}</section>"""
    desc = " ".join((f.get("summary") or f"{f['title']}, {when}").split())
    desc = desc if len(desc) <= 200 else desc[:197].rsplit(" ", 1)[0] + "\u2026"
    image = f["image"] if (f.get("image") or "").startswith("https://") else None
    return shell(cfg, f"{f['title']} · {when} · {cfg['site_name']}", desc, url, inner, stories, image=image, data=data)


def render_events(cfg, flyers, stories):
    site = cfg["site_url"].rstrip("/")
    today = datetime.now(timezone.utc).date()
    weekend_end = today + timedelta(days=(6 - today.weekday()) % 7)  # through Sunday
    dated = [f for f in flyers if f.get("day")]
    now_ = [f for f in dated if f["day"].date() <= weekend_end]
    later = [f for f in dated if f["day"].date() > weekend_end] + [f for f in flyers if not f.get("day")]
    grid = lambda xs: '<div class="grid events">' + "".join(flyer_card(f) for f in xs) + "</div>"
    block = lambda label, xs, empty: (f'<section class="wrap latest"><h2 class="label">{label}</h2>'
                                      + (grid(xs) if xs else empty) + "</section>")
    inner = f"""{page_title("What's happening", "Things to do in Mansfield", "Concerts, fish fries, festivals, games, church suppers and fundraisers around Mansfield and Richland County, Ohio, posted by the people putting them on.")}
{block("This weekend in Richland County", now_, '<p class="empty">Nothing posted for this weekend yet.</p>' if later else first_post(cfg, "events", block=True))}
{block("Coming up", later, '<p class="empty">No upcoming events posted yet.</p>' if now_ else "")}
<section class="wrap latest"><p class="note" style="text-align:center">Putting on an event? <a class="link" href="{e(site)}/submit.html#flyer">Post your flyer free</a> and it rides the front page until the big day.</p></section>"""
    data = [event_data(cfg, f) for f in dated] + [breadcrumbs([("Sunny Mansfield", f"{site}/"), ("Events", f"{site}/events.html")])]
    return shell(cfg, f"Things to Do in Mansfield, Ohio This Weekend · Events · {cfg['site_name']}",
                 "Local events in Mansfield and Richland County, Ohio this weekend and coming up: concerts, festivals, "
                 "fish fries, games and fundraisers, posted by neighbors.", f"{site}/events.html", inner, stories, data=data)


def check_seo(out):
    """Flag pages search engines can't read well: broken structured data, a missing title, canonical or H1, or a
    sitemap pointing at pages that weren't built. The site still publishes; problems show as warnings on the run."""
    problems = []
    pages = [p for p in out.rglob("*.html") if p.name != "artifact.html" and p.parent.name != "newsletter"]  # the email copy isn't for search
    for p in pages:
        t = p.read_text()
        for block in re.findall(r'<script type="application/ld\+json">(.*?)</script>', t, re.S):
            try:
                json.loads(block)
            except ValueError as exc:
                problems.append(f"{p.relative_to(out)}: bad structured data ({exc})")
        for what, pat in (("title", r"<title>[^<]+</title>"), ("canonical", r'rel="canonical"'), ("description", r'name="description" content="[^"]+')):
            if not re.search(pat, t):
                problems.append(f"{p.relative_to(out)}: no {what}")
        if len(re.findall(r"<h1[ >]", t)) != 1:
            problems.append(f"{p.relative_to(out)}: {len(re.findall(r'<h1[ >]', t))} H1 headings")
    base = (out / "robots.txt").read_text().split("Sitemap: ")[1].split("/sitemap.xml")[0]
    for loc in re.findall(r"<loc>([^<]+)</loc>", (out / "sitemap.xml").read_text()):
        path = html.unescape(loc)[len(base):].lstrip("/") or "index.html"
        if not (out / urllib.parse.urlparse(path).path).exists():
            problems.append(f"sitemap lists {loc}, which wasn't built")
    for p in problems[:40]:
        print(("::warning::" if os.environ.get("GITHUB_ACTIONS") else "  SEO: ") + p, file=sys.stderr)
    if not problems:
        note(f"SEO check: {len(pages)} pages OK")


def ping_indexnow(cfg, stories, flyers):
    """Tell Bing (and through it ChatGPT search) about new pages right away instead of waiting for a crawl."""
    key = cfg.get("indexnow_key")
    if not key:
        return
    site = cfg["site_url"].rstrip("/")
    cutoff = datetime.now(timezone.utc) - timedelta(days=2)
    urls = [f"{site}/", f"{site}/events.html"] + [page_url(cfg, s) for s in stories if indexable(s) and s["date"] > cutoff]
    body = json.dumps({"host": urllib.parse.urlparse(site).netloc, "key": key, "keyLocation": f"{site}/{key}.txt",
                       "urlList": urls[:200]}).encode()
    try:
        req = urllib.request.Request("https://api.indexnow.org/indexnow", data=body,
                                     headers={"Content-Type": "application/json; charset=utf-8", "User-Agent": UA})
        with urllib.request.urlopen(req, timeout=20) as r:
            note(f"IndexNow: sent {len(urls)} pages ({r.status})")
    except Exception as exc:
        note(f"IndexNow: skipped ({exc})")


def render_market(cfg, listings, stories):
    site = cfg["site_url"].rstrip("/")
    ads = ads_for(cfg, "A7")
    counts = {k: sum(1 for x in listings if x.get("cat") == k) for k, _, _ in MARKET_CATS}
    groups = [("Categories", [k for k, _, _ in MARKET_CATS])]
    side = "".join(f'<div class="catgroup"><h3 class="caps">{g}</h3><ul>' + "".join(
        f'<li><a href="#cat={k}" data-cat="{k}">{e(MARKET_CAT_NAMES[k])}<span class="n">{counts[k] or ""}</span></a></li>' for k in keys)
        + "</ul></div>" for g, keys in groups)
    rows = "".join(listing_card(x, cfg) for x in listings)
    empty = '<div class="empty" id="m-empty"' + (" hidden" if listings else "") + '>' + first_post(cfg, "free", block=True) + '</div>'
    inner = f"""{free_hero(site, len(listings))}
<section class="wrap market">
<aside class="catside" aria-label="Categories"><a class="btn solid" href="{e(site)}/submit.html#free">Give something away</a>
<a class="allcat" href="#" data-cat="all">Everything free <span class="n">{len(listings)}</span></a>{side}</aside>
<div class="marketmain">
<div class="mtools"><input type="search" id="m-search" placeholder="Search Free Stuff" aria-label="Search free stuff">
<div class="viewtog" role="group" aria-label="View"><button type="button" class="tab" data-view="list" aria-pressed="true">List</button><button type="button" class="tab" data-view="grid" aria-pressed="false">Photos</button></div></div>
<h2 class="label" id="m-head">Everything free</h2>
<div class="mlist" id="m-list">{rows}</div>{empty}
{f'<div class="madwrap">{mad}</div>' if (mad := ad_card(cfg, ads[0] if ads else None, "A7", i=1)) else ""}
<p class="note">Your phone and email are never shown. Tap Message the giver and they write back to you. Pick up in daylight, bring a friend, and never pay a "fee" for something listed as free. Sunny Mansfield doesn't check items. Report a listing: {e(cfg['submit_email'])}</p>
</div></section>
<script>(()=>{{const list=document.getElementById('m-list');const items=[...list.children];const q=document.getElementById('m-search');
const head=document.getElementById('m-head');const empty=document.getElementById('m-empty');let cat='all';
function go(){{const t=q.value.trim().toLowerCase();let n=0;items.forEach(el=>{{const ok=(cat==='all'||el.dataset.cat===cat)&&(!t||el.dataset.q.includes(t));el.hidden=!ok;n+=ok}});
 const a=document.querySelector('.catside [data-cat="'+cat+'"]');head.textContent=cat==='all'?'Everything free':a?a.firstChild.textContent:'Listings';
 document.querySelectorAll('.catside [data-cat]').forEach(x=>x.setAttribute('aria-current',x.dataset.cat===cat?'true':'false'));empty.hidden=n>0}}
function fromHash(){{const m=location.hash.match(/cat=(\w+)/);cat=m?m[1]:'all';go()}}
document.querySelectorAll('.catside [data-cat]').forEach(a=>a.addEventListener('click',ev=>{{ev.preventDefault();cat=a.dataset.cat;history.replaceState(null,'',cat==='all'?location.pathname:'#cat='+cat);go();
 if(innerWidth<800)head.scrollIntoView({{behavior:'smooth'}})}}));
q.addEventListener('input',go);window.addEventListener('hashchange',fromHash);fromHash();
document.querySelectorAll('[data-view]').forEach(b=>b.addEventListener('click',()=>{{list.classList.toggle('grid',b.dataset.view==='grid');
 document.querySelectorAll('[data-view]').forEach(x=>x.setAttribute('aria-pressed',x===b))}}));}})();</script>"""
    return shell(cfg, f"Free Stuff in Mansfield, Ohio · {cfg['site_name']}",
                 "Free furniture, clothes, firewood, tools and more from neighbors in Mansfield and Richland County, Ohio. Everything is free.",
                 f"{site}/free.html", inner, stories, extra=reply_box(cfg))


# Line drawings for the Free Stuff header, in the same style as ART.
FREE_ART = {
    "couch": '<path d="M14 72V50c0-5 4-8 8-8h56c4 0 8 3 8 8v22M24 42V32c0-4 3-7 7-7h38c4 0 7 3 7 7v10M14 72h72M20 72v8M80 72v8M28 58h44"/>',
    "bike": '<circle cx="26" cy="64" r="15"/><circle cx="74" cy="64" r="15"/><path d="M26 64l14-24h26l8 24M40 40l12 24h22M34 32h12M60 32h10l-4 8"/>',
    "blocks": '<path d="M14 86V62h26v24zM40 86V62h26v24zM27 62V38h26v24zM24 74h6M50 74h6M37 50h6"/>',
    "plant": '<path d="M32 86h36l-4-24H36zM50 62V34M50 46c-12 0-20-8-20-20 12 0 20 8 20 20zM50 38c10 0 18-8 18-18-10 0-18 8-18 18z"/>',
    "camera": '<path d="M14 34h18l6-10h24l6 10h18v46H14z"/><circle cx="50" cy="57" r="14"/>',
    "message": '<path d="M14 24h72v46H44L28 84V70H14z"/><path d="M30 40h40M30 53h26"/>',
    "porch": '<path d="M10 46l40-30 40 30M18 40v46h64V40M40 86V62h20v24M26 86V70M74 86V70"/>',
}


def free_hero(site, n):
    """The Free Stuff page opener: says plainly what it is and asks people to give something away."""
    art = lambda k: f'<svg viewBox="0 0 100 100" aria-hidden="true">{FREE_ART[k]}</svg>'
    tiles = "".join(f'<span class="ftile">{art(k)}<span class="ftag">Free</span><span class="flabel">{t}</span></span>'
                    for k, t in (("couch", "Couches"), ("bike", "Bikes"), ("blocks", "Toys"), ("plant", "Plants")))
    steps = "".join(f'<li>{art(k)}<span><b>{a}</b> {b}</span></li>' for k, a, b in (
        ("camera", "Snap a photo and post it.", "Takes two minutes. An editor gives it a quick look."),
        ("message", "Neighbors message you privately.", "Your phone and email are never shown."),
        ("porch", "Set it out or hand it off.", "Pick who gets it. No money ever changes hands.")))
    browse = f'<a class="btn" href="#m-list">See what\'s free ({n})</a>' if n else ""
    return f"""<section class="wrap freehero"><div class="fcopy"><span class="script">Neighbor to neighbor</span>
<h1 class="retro sm">Free Stuff</h1>
<p class="flead">Got something you don't need? Give it to a neighbor who does.</p>
<p class="intro">Free Stuff is Richland County's giveaway board. Post the couch, crib, bike or garden extras you're done with, and a neighbor picks it up. Everything here is free: no prices, no trades, no selling. Selling belongs on Marketplace. This is neighbors helping neighbors.</p>
<div class="share"><a class="btn solid" href="{e(site)}/submit.html#free">Give something away</a>{browse}</div></div>
<div class="ftiles" aria-hidden="true">{tiles}</div>
<ol class="fsteps">{steps}</ol></section>"""


def render_moved(cfg, stories, path, name):
    """An old address that now lives somewhere else. Sends people on and stays out of search."""
    site = cfg["site_url"].rstrip("/")
    inner = f"""{page_title("We moved", name, "This page has a new home.")}
<section class="wrap" style="text-align:center"><p><a class="btn solid" href="{e(site)}/{path}">Go to {e(name)}</a></p></section>
<script>location.replace({json.dumps(site + "/" + path)})</script>"""
    return shell(cfg, f"{name} · {cfg['site_name']}", f"{name} has moved.", f"{site}/{path}", inner, stories, index=False)


def render_not_found(cfg, stories):
    """404.html. GitHub Pages shows it for any address that doesn't exist, at that same address, so it stays on
    sunnymansfield.com. Every link points at the paper's own domain, and anyone who lands on the github.io copy is
    sent over to the same page on sunnymansfield.com."""
    site = cfg["site_url"].rstrip("/")
    home = (cfg.get("canonical_url") or site).rstrip("/")
    base = urllib.parse.urlparse(site).path.rstrip("/")
    grid = "".join(card(cfg, s) for s in stories[:3])
    latest = (f'<section class="wrap latest"><h2 class="label">The latest good news</h2><div class="grid">{grid}</div></section>'
              if grid else "")
    inner = f"""{page_title("Oops!", "We couldn't find that page", "The link may be old, or the story may have rolled off the front page. There's plenty of good news waiting for you.")}
<section class="wrap" style="text-align:center"><p><a class="btn solid" href="{e(home)}/">Go to the front page</a>
<a class="btn" href="{e(home)}/submit.html">Share your news</a></p>
<p class="note">Looking for something special? Try <a class="link" href="{e(home)}/sports.html">sports</a>,
<a class="link" href="{e(home)}/events.html">events</a>, <a class="link" href="{e(home)}/businesses.html">local businesses</a>
or <a class="link" href="{e(home)}/jobs.html">jobs</a>.</p></section>
{latest}"""
    bounce = ""
    if urllib.parse.urlparse(home).hostname and not urllib.parse.urlparse(home).hostname.endswith("github.io"):
        # A visitor on user.github.io/sunny-mansfield/... goes to the same page on the custom domain.
        bounce = (f"<script>if(/\\.github\\.io$/.test(location.hostname)){{var p=location.pathname;"
                  f"var b={json.dumps(base)};if(b&&p.indexOf(b)===0)p=p.slice(b.length);"
                  f"location.replace({json.dumps(home)}+(p||'/')+location.search+location.hash)}}</script>")
    return shell(cfg, f"Page not found · {cfg['site_name']}", "We couldn't find that page on Sunny Mansfield.",
                 f"{home}/404.html", inner, stories, extra=bounce, index=False)


def job_data(cfg, j):
    """JobPosting structured data, so the job can show up in Google's job search."""
    site = cfg["site_url"].rstrip("/")
    kinds = {"Full-time": "FULL_TIME", "Part-time": "PART_TIME", "Seasonal": "TEMPORARY", "Temporary": "TEMPORARY",
             "Internship": "INTERN"}
    d = {"@context": "https://schema.org", "@type": "JobPosting", "title": j["title"],
         "description": "<p>" + e(j["summary"]).replace("\n", "<br>") + "</p>", "datePosted": j["date"].strftime("%Y-%m-%d"),
         "validThrough": j["until"].strftime("%Y-%m-%dT23:59:00+00:00"), "url": job_url(cfg, j), "directApply": True,
         "hiringOrganization": {"@type": "Organization", "name": j["employer"]},
         "jobLocation": {"@type": "Place", "address": {"@type": "PostalAddress", "addressLocality": j.get("town") or "Mansfield",
                                                        "addressRegion": "OH", "addressCountry": "US"}},
         "identifier": {"@type": "PropertyValue", "name": cfg["site_name"], "value": j["id"]}}
    if j.get("type") in kinds:
        d["employmentType"] = kinds[j["type"]]
    if j.get("image"):
        d["image"] = img_src(j["image"])
    return [d, breadcrumbs([("Sunny Mansfield", f"{site}/"), ("Jobs", f"{site}/jobs.html"), (j["title"], job_url(cfg, j))])]


JOB_SAFETY = ("Real jobs never charge you to apply, send you a check to cash, or ask for your Social Security number or bank "
              "details before an interview. Sunny Mansfield doesn't check employers or hire for them.")


def render_job_page(cfg, j, stories):
    site = cfg["site_url"].rstrip("/")
    facts = "".join(f"<dt>{k}</dt><dd>{e(v)}</dd>" for k, v in (("Employer", j["employer"]), ("Type", j.get("type")),
                    ("Pay", j.get("pay")), ("Where", j.get("town") or "Richland County"),
                    ("Posted", j["date"].strftime("%B %-d, %Y")), ("Open until", j["until"].strftime("%B %-d, %Y"))) if v)
    apply = (f'<a class="btn sm" href="{e(j["link"])}" target="_blank" rel="noopener nofollow ugc">Apply on their site</a>'
             if j.get("link") else "")
    body = "".join(f"<p>{e(p)}</p>" for p in j["summary"].split("\n") if p.strip())
    inner = f"""<section class="wrap pagehead"><span class="script">Now hiring</span><h1 class="retro sm">{e(j["title"])}</h1>
<p class="intro">{e(j["employer"])} · {e(j.get("town") or "Richland County")}</p></section>
<section class="wrap jobpage"><div class="legaltext">{body}</div>
<aside class="box"><dl class="facts">{facts}</dl><div class="share">{reply_button(cfg, j, "Reply privately")}{apply}</div>
<p class="hint">{e(REPLY_NOTE)}</p></aside></section>
<section class="wrap latest"><p class="note safenote">{SHIELD}<span>{e(JOB_SAFETY)} <a class="link" href="{e(report_link(cfg, {"title": j["title"], "link": job_url(cfg, j)}))}">Report this job</a> · <a class="link" href="{e(site)}/jobs.html">All local jobs</a></span></p></section>"""
    return shell(cfg, f"{j['title']} at {j['employer']}, {j.get('town') or 'Richland County'} OH · {cfg['site_name']}",
                 f"{j['title']} at {j['employer']} in {j.get('town') or 'Richland County'}, Ohio. {j['summary'][:110]}",
                 job_url(cfg, j), inner, stories, data=job_data(cfg, j), extra=reply_box(cfg))


def render_jobs(cfg, jobs, stories):
    site = cfg["site_url"].rstrip("/")
    types = [t for t in JOB_TYPES if any(j.get("type") == t for j in jobs)]
    tabs = "".join(f'<button class="tab" type="button" data-jt="{slug(t)}" aria-pressed="false">{t}</button>' for t in types)
    cards = "".join(job_card(cfg, j) for j in jobs)
    inner = f"""{page_title("Now hiring", "Jobs in Richland County", "Local jobs posted by Mansfield-area employers. Free to post, free to apply. Reply privately: nobody's email is shown on the site.")}
<section class="wrap jobs"><div class="mtools"><input type="search" id="j-search" placeholder="Search jobs" aria-label="Search jobs">
<a class="btn solid" href="{e(site)}/submit.html#job">Post a job</a></div>
<div class="qtabs" role="group" aria-label="Job type"><button class="tab" type="button" data-jt="all" aria-pressed="true">All jobs</button>{tabs}</div>
<div class="joblist" id="j-list">{cards}</div>
<div class="empty" id="j-empty"{" hidden" if jobs else ""}>{first_post(cfg, "jobs", block=True)}</div>
<p class="note safenote">{SHIELD}<span>{e(JOB_SAFETY)} Report a job: {e(cfg['submit_email'])}</span></p></section>
<script>(()=>{{const items=[...document.getElementById('j-list').children];const q=document.getElementById('j-search');let t='all';
function go(){{const s=q.value.trim().toLowerCase();let n=0;items.forEach(el=>{{const ok=(t==='all'||el.dataset.type===t)&&(!s||el.dataset.q.includes(s));el.hidden=!ok;n+=ok}});
 document.getElementById('j-empty').hidden=n>0||!items.length;document.querySelectorAll('[data-jt]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.jt===t))}}
document.querySelectorAll('[data-jt]').forEach(b=>b.addEventListener('click',()=>{{t=b.dataset.jt;go()}}));q.addEventListener('input',go)}})();</script>"""
    data = [{"@context": "https://schema.org", "@type": "ItemList", "name": "Jobs in Richland County, Ohio", "itemListElement": [
        {"@type": "ListItem", "position": i, "url": job_url(cfg, j), "name": j["title"]} for i, j in enumerate(jobs[:30], 1)]},
            breadcrumbs([("Sunny Mansfield", f"{site}/"), ("Jobs", f"{site}/jobs.html")])]
    return shell(cfg, f"Jobs in Mansfield, Ohio & Richland County · {cfg['site_name']}",
                 "Local jobs in Mansfield, Ontario, Lexington, Shelby, Bellville and Richland County, Ohio. Full-time, part-time "
                 "and seasonal work posted by local employers. Free to apply.", f"{site}/jobs.html", inner, stories, data=data,
                 extra=reply_box(cfg))


# ---- Homes for rent or sale, posted by owners and agents through the submit form ----
HOME_LABEL = "Home for Rent or Sale"
HOME_DAYS = 30
HOME_KINDS = ["For rent", "For sale"]
HOME_SAFETY = ("Never pay a deposit, application fee or rent before you've seen the home in person and met the owner or agent. "
               "Scammers copy real listings and ask for money by wire, gift card or payment app. Sunny Mansfield doesn't check "
               "listings or handle any money.")
FAIR_HOUSING = ("Every home here is offered under the Fair Housing Act: no listing may prefer or exclude anyone because of race, "
                "color, religion, sex, disability, family status, national origin, ancestry or military status.")
HOME_ART = '<path d="M10 52L50 16l40 36M20 44v42h60V44M42 86V62h16v24"/>'


def home_price(text):
    """The number in a typed price, for sorting: '$950 a month' -> 950, '$149,900' -> 149900, '150k' -> 150000."""
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)\s*(k\b)?", (text or "").replace("$", ""), re.I)
    if not m:
        return None
    n = float(m.group(1).replace(",", ""))
    return int(n * 1000 if m.group(2) else n)


def home_price_label(h, short=False):
    n = h.get("amount")
    if not n:
        return h.get("price") or "Ask"
    monthly = h.get("kind") == "For rent"
    return f"${n:,}" + (("/mo" if short else " a month") if monthly else "")


def home_facts(h):
    beds = h.get("beds")
    beds = "Studio" if beds == "Studio" else (f"{beds} bed" if beds else "")
    baths = f"{h['baths']} bath" if h.get("baths") else ""
    return " · ".join(x for x in (beds, baths, h.get("town") or "Richland County") if x)


def home_url(cfg, h):
    return f'{cfg["site_url"].rstrip("/")}/homes/{h["id"]}.html'


def home_card(cfg, h):
    img = (f'<img src="{e(img_src(h["image"]))}" alt="" loading="lazy" referrerpolicy="no-referrer">' if h.get("image")
           else f'<span class="art"><svg viewBox="0 0 100 100" aria-hidden="true">{HOME_ART}</svg></span>')
    search = e(f'{h["title"]} {h["summary"]} {h.get("town") or ""}'.lower())
    return (f'<article class="card home" id="h-{h["id"]}" data-kind="{slug(h.get("kind") or "other")}" data-price="{h.get("amount") or 0}" '
            f'data-date="{int(h["date"].timestamp())}" data-q="{search}">'
            f'<a class="photo" href="{e(home_url(cfg, h))}" tabindex="-1" aria-hidden="true">{img}<span class="price">{e(home_price_label(h, True))}</span></a>'
            f'<span class="caps kicker">{e(h.get("kind") or "Home")} · {e(home_facts(h))}</span>'
            f'<h3 class="hed"><a href="{e(home_url(cfg, h))}">{e(h["title"])}</a></h3>'
            f'<div class="share">{reply_button(cfg, h, "Ask about it")}<a class="btn sm" href="{e(home_url(cfg, h))}">Details</a></div></article>')


def render_homes(cfg, homes, stories):
    site = cfg["site_url"].rstrip("/")
    cards = "".join(home_card(cfg, h) for h in homes)
    tabs = "".join(f'<button class="tab" type="button" data-hk="{slug(k)}" aria-pressed="false">{k}</button>' for k in HOME_KINDS)
    inner = f"""{page_title("Home sweet home", "Homes for Rent or Sale", "Houses and apartments for rent or sale around Mansfield and Richland County, posted by local owners and agents. Free to post. Ask about a home privately: nobody's email or phone is shown.")}
<section class="wrap jobs"><div class="mtools"><input type="search" id="h-search" placeholder="Search homes" aria-label="Search homes">
<select id="h-sort" aria-label="Sort homes"><option value="new">Newest first</option><option value="low">Lowest price</option><option value="high">Highest price</option></select>
<a class="btn solid" href="{e(site)}/submit.html#home">List a home</a></div>
<div class="qtabs" role="group" aria-label="Rent or sale"><button class="tab" type="button" data-hk="all" aria-pressed="true">All homes</button>{tabs}</div>
<div class="grid homes" id="h-list">{cards}</div>
<div class="empty" id="h-empty"{" hidden" if homes else ""}>{first_post(cfg, "homes", block=True)}</div>
<p class="note safenote">{SHIELD}<span>{e(HOME_SAFETY)} {e(FAIR_HOUSING)} Report a listing: {e(cfg['submit_email'])}</span></p></section>
<script>(()=>{{const box=document.getElementById('h-list');const items=[...box.children];const q=document.getElementById('h-search');const so=document.getElementById('h-sort');let t='all';
function go(){{const s=q.value.trim().toLowerCase();let n=0;const k=so.value;
 items.sort((a,b)=>k==='new'?b.dataset.date-a.dataset.date:k==='low'?(+a.dataset.price||1e12)-(+b.dataset.price||1e12):b.dataset.price-a.dataset.price).forEach(el=>box.appendChild(el));
 items.forEach(el=>{{const ok=(t==='all'||el.dataset.kind===t)&&(!s||el.dataset.q.includes(s));el.hidden=!ok;n+=ok}});
 document.getElementById('h-empty').hidden=n>0||!items.length;document.querySelectorAll('[data-hk]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.hk===t))}}
document.querySelectorAll('[data-hk]').forEach(b=>b.addEventListener('click',()=>{{t=b.dataset.hk;go()}}));q.addEventListener('input',go);so.addEventListener('change',go)}})();</script>"""
    data = [{"@context": "https://schema.org", "@type": "ItemList", "name": "Homes for rent or sale in Richland County, Ohio", "itemListElement": [
        {"@type": "ListItem", "position": i, "url": home_url(cfg, h), "name": h["title"]} for i, h in enumerate(homes[:30], 1)]},
            breadcrumbs([("Sunny Mansfield", f"{site}/"), ("Homes", f"{site}/homes.html")])]
    return shell(cfg, f"Homes for Rent or Sale in Mansfield, Ohio · {cfg['site_name']}",
                 "Houses and apartments for rent or sale in Mansfield, Ontario, Lexington, Shelby, Bellville and Richland County, "
                 "Ohio, posted by local owners and agents.", f"{site}/homes.html", inner, stories, data=data, extra=reply_box(cfg))


def render_home_page(cfg, h, stories):
    site = cfg["site_url"].rstrip("/")
    facts = "".join(f"<dt>{k}</dt><dd>{e(v)}</dd>" for k, v in (("Price", home_price_label(h)), ("Listing", h.get("kind")),
                    ("Bedrooms", h.get("beds")), ("Bathrooms", h.get("baths")), ("Where", h.get("town") or "Richland County"),
                    ("Listed by", h.get("by")), ("Posted", h["date"].strftime("%B %-d, %Y"))) if v)
    more = (f'<a class="btn sm" href="{e(h["link"])}" target="_blank" rel="noopener nofollow ugc">See the full listing</a>'
            if h.get("link") else "")
    photo_html = (f'<span class="photo"><img src="{e(img_src(h["image"]))}" alt="{e(h["title"])}" referrerpolicy="no-referrer"></span>'
                  if h.get("image") else "")
    body = "".join(f"<p>{e(p)}</p>" for p in h["summary"].split("\n") if p.strip())
    inner = f"""<section class="wrap pagehead"><span class="script">{e(h.get("kind") or "Home")}</span><h1 class="retro sm">{e(home_price_label(h))}</h1>
<p class="intro">{e(h["title"])} · {e(home_facts(h))}</p></section>
<section class="wrap jobpage"><div class="legaltext">{photo_html}{body}</div>
<aside class="box"><dl class="facts">{facts}</dl><div class="share">{reply_button(cfg, h, "Ask about it")}{more}
<a class="btn fb sm" href="{e(fb_share(home_url(cfg, h)))}" target="_blank" rel="noopener">{FB_ICON}Share</a></div>
<p class="hint">{e(REPLY_NOTE)}</p></aside></section>
<section class="wrap latest"><p class="note safenote">{SHIELD}<span>{e(HOME_SAFETY)} {e(FAIR_HOUSING)} <a class="link" href="{e(report_link(cfg, {"title": h["title"], "link": home_url(cfg, h)}))}">Report this listing</a> · <a class="link" href="{e(site)}/homes.html">All homes</a></span></p></section>"""
    data = [breadcrumbs([("Sunny Mansfield", f"{site}/"), ("Homes", f"{site}/homes.html"), (h["title"], home_url(cfg, h))])]
    return shell(cfg, f"{home_price_label(h)}: {h['title']}, {h.get('town') or 'Richland County'} OH · {cfg['site_name']}",
                 f"{h.get('kind') or 'Home'} in {h.get('town') or 'Richland County'}, Ohio: {home_price_label(h)}, {home_facts(h)}. {h['summary'][:100]}",
                 home_url(cfg, h), inner, stories, image=img_src(h["image"]) if (h.get("image") or "").startswith("https://") else None,
                 data=data, extra=reply_box(cfg))


AD_RULES = [
    ("Labeled, always.", "Every ad says Advertisement or Sponsor. Ads never look like stories, and sponsors never decide what we cover."),
    ("Local and family friendly.", "We welcome Richland County businesses, nonprofits, churches, schools and events. No alcohol, tobacco, vaping, "
     "cannabis, gambling, weapons, adult content, payday or title loans, or get-rich offers."),
    ("True and fair.", "No false claims, fake buttons, before-and-after health promises or prices you won't honor."),
    ("Political and issue ads.", "Accepted only with a clear \u201cPaid for by\u201d line, as Ohio law requires, and only for local "
     "races and issues. We may decline any political ad."),
    ("Your files.", "JPG or PNG, under 1 MB, at the sizes listed. Keep words big and few; most people read on a phone. "
     "Send a short description of the image for people who use screen readers."),
    ("A working link.", "Every ad links to your website, Facebook page, menu or event page. The page must load on a phone."),
    ("Timing.", "Send files three business days before your start date. Ads run by the month and stop on the last day you paid for."),
    ("Payment.", "Prepaid by the month, cancel any time before the next month. Founding sponsor prices are locked in for a year."),
    ("Our call.", "We can turn down or take down any ad that doesn't fit these rules. If we take one down, we refund the unused days."),
]


def render_ads_soon(cfg, stories):
    """Stand-in while advertising is paused, so old links don't 404. Kept out of search."""
    site = cfg["site_url"].rstrip("/")
    inner = f"""{page_title("Coming soon", "Advertising is paused", "We're not selling ad spots right now. Local businesses can still share news, deals and events for free.")}
<section class="wrap" style="text-align:center"><p><a class="btn solid" href="{e(site)}/submit.html">Share your news</a></p>
<p class="note">Questions? Email <a href="mailto:{e(cfg['submit_email'])}">{e(cfg['submit_email'])}</a>.</p></section>"""
    return shell(cfg, f"Advertising · {cfg['site_name']}", "Advertising on Sunny Mansfield is paused for now.",
                 f"{site}/advertise.html", inner, stories, index=False)


def render_advertise(cfg, stories):
    site = cfg["site_url"].rstrip("/")
    spots = "".join(f"""<article class="spot"><span class="code">Spot {x['spot']}</span><h3>{e(x['name'])}</h3>
<p>{e(x['where'])}.</p><p class="hint">{e(x['size'])}</p><span class="price">{e(x['price'])}</span></article>""" for x in AD_SPOTS)
    rules = "".join(f"<li><b>{t}</b> {d}</li>" for t, d in AD_RULES)
    tiers = "".join(f"""<article class="tier{' pick' if t.get('pick') else ''}">{'<span class="caps picked">Most popular</span>' if t.get('pick') else ''}
<h3>{e(t['name'])}</h3><p class="cost"><b>{t['price']}</b> {t['per']}</p><p class="hint">{e(t['best'])}</p>
<ul>{''.join(f'<li>{e(g)}</li>' for g in t['gets'])}</ul><a class="btn{' solid' if t.get('pick') else ''} sm" href="#ask">Choose {e(t['name'])}</a></article>""" for t in AD_TIERS)
    specials = "".join(f'<article class="spot"><h3>{e(n)}</h3><p>{e(d)}</p><span class="price">{e(pr)}</span></article>' for n, pr, d in AD_SPECIALS)
    deals = "".join(f"<li>{e(d)}</li>" for d in AD_DEALS)
    kit = (f'<a class="btn" href="{e(site)}/brand/Sunny-Mansfield-Media-Kit.pdf" download>Download the media kit (PDF)</a>'
           if (HERE / "brand" / "Sunny-Mansfield-Media-Kit.pdf").exists() else "")
    inner = f"""{page_title("Shop local!", "Advertise with us", "Put your business in front of neighbors who read, share and shop local. Every ad is labeled, and sponsors never decide what we cover.")}
<section class="wrap latest" id="spots"><h2 class="label">Ad spots</h2><div class="spots">{spots}</div>
<div class="more-row"><a class="btn solid" href="#ask">Ask about a spot</a>{kit}</div>
<p class="note" style="text-align:center">Prices are founding sponsor rates. Every paid sponsor is also thanked by name at the bottom of every page. Business spotlights and community posts are always free.</p></section>
<section class="wrap latest" id="packages"><h2 class="label">Packages</h2><div class="tiers">{tiers}</div>
<h3 class="hed" style="margin:32px 0 12px">Specialty spots</h3><div class="spots">{specials}</div>
<ul class="deals">{deals}</ul></section>
<section class="wrap latest" id="requirements"><h2 class="label">Ad requirements</h2><ul class="reqs">{rules}</ul></section>
<section class="wrap formbox" id="ask"><h2 class="label">Ask about advertising</h2>{sunny_form(cfg, "ask-form", "Advertise", [
        field("business", "Business name", req=True),
        field("name", "Your name", req=True),
        field("email", "Email", "email", True),
        field("contact", "Phone", "tel"),
        field("interest", "Interested in", "select", opts=[f"{t['name']} package" for t in AD_TIERS] + [n for n, _, _ in AD_SPECIALS] + [f"{x['spot']}: {x['name']}" for x in AD_SPOTS] + ["Not sure yet"], extra="Interested in"),
        field("summary", "Anything else?", "textarea", hint="Dates you'd like, and whether you need help making the ad."),
    ], "Send", title_tpl="Ad inquiry: {business}")}</section>"""
    return shell(cfg, f"Advertise · {cfg['site_name']}", "Ad spots, prices and requirements for advertising on Sunny Mansfield.",
                 f"{site}/advertise.html", inner, stories, extra=form_script(cfg))


def render_admin(cfg, stories, headline=None):
    site = cfg["site_url"].rstrip("/")
    if not backend_on(cfg):
        inner = (page_title("Editors only", "Admin", "The review queue isn't connected yet. Follow BACKEND.md in the repository, "
                            "then send the project URL and anon key to whoever builds the site."))
        return shell(cfg, f"Admin · {cfg['site_name']}", "Editors only.", f"{site}/admin.html", inner, stories, index=False)
    conf = json.dumps({"url": cfg["supabase_url"], "key": cfg["supabase_anon_key"]})
    news = [s for s in stories if s["section"] != "life"][:40]

    def pick_row(s):
        src = s.get("image") or ""
        src = src if src.startswith("https://") else ("/" + src if src else "")
        img = f'<img src="{e(src)}" alt="" loading="lazy" referrerpolicy="no-referrer">' if src else ""
        now = s is headline
        btn = ('<span class="caps kicker">Headline now</span>' if now else
               f'<button class="btn sm solid" type="button" data-head="{e(json.dumps({"title": s["title"], "link": s["link"]}))}">Make headline</button>')
        return (f'<article class="qitem pick{" on" if now else ""}"><span class="qphoto">{img}</span><div class="qbody">'
                f'<span class="caps kicker">{e(SECTION_NAMES[s["section"]])} · {e(s["source"])}</span><h3 class="hed">{e(s["title"])}</h3>'
                f'<div class="share">{btn}</div></div></article>')
    current = (f'Right now the headline is <b>{e(headline["title"])}</b>, picked by an editor.' if headline
               else "Right now the site picks the headline on its own: the newest story with a photo.")
    picker = f"""<div id="headline-ed" hidden><div class="box"><span class="caps kicker">Front page</span><h2 class="hed">Pick the headline</h2>
<p class="hint">{current} Your pick stays at the top until you pick another one or the story leaves the site. Changes show within 15 minutes.</p>
<div class="share"><button class="btn sm" type="button" id="head-auto">Go back to automatic</button></div></div>
<div class="queue" style="margin-top:16px">{"".join(pick_row(s) for s in news)}</div></div>"""
    inner = f"""{page_title("Editors only", "Review queue", "Approve posts to put them on the site. Approved posts appear within 20 minutes.")}
<section class="wrap admin">
<div class="box" id="login"><span class="caps kicker">Sign in</span><h2 class="hed">Editor sign-in</h2>
<p class="hint">Use the email and password set up for you in Supabase. Only emails on the admin list can review posts.</p>
<form class="logform" id="login-form"><input type="email" id="login-email" placeholder="you@example.com" aria-label="Your email" autocomplete="username" required>
<input type="password" id="login-pass" placeholder="password" aria-label="Password" autocomplete="current-password">
<button class="btn solid" type="submit" id="login-btn">Sign in</button></form>
<p class="hint">No password yet? <button class="textbtn" type="button" id="login-link">Email me a sign-in link</button></p>
<p class="formmsg" id="login-msg"></p></div>
<div id="desk" hidden>
<div class="deskbar"><nav class="sections" aria-label="Queue"><div class="qtabs">
<button class="tab" type="button" data-q="pending">Waiting <span class="n"></span></button>
<button class="tab" type="button" data-q="apps">Team applications <span class="n"></span></button>
<button class="tab" type="button" data-q="ads">Ad inquiries <span class="n"></span></button>
<button class="tab" type="button" data-q="approved">Approved</button>
<button class="tab" type="button" data-q="headline">Headline</button>
<button class="tab" type="button" data-q="brackets">Brackets</button>
<button class="tab" type="button" data-q="rejected">Rejected</button>
<button class="tab" type="button" data-q="verified">Verified people</button></div></nav>
<div class="row"><p class="hint">Signed in as <b id="me"></b>. <button class="textbtn" type="button" id="logout">Sign out</button></p>
<input type="password" id="pw-new" placeholder="new password" aria-label="New password" autocomplete="new-password" style="flex:0 1 200px">
<button class="btn sm" type="button" id="pw-btn">Set password</button></div></div>
<div id="addv" class="row" hidden><input type="email" id="v-email" placeholder="email to verify" aria-label="Email to verify"><input id="v-name" placeholder="name" aria-label="Name">
<button class="btn sm solid" type="button" id="v-add">Add verified person</button></div>
<div id="bracket-ed" class="box" hidden><span class="caps kicker">Sports page</span><h2 class="hed" id="b-head">New bracket</h2>
<form class="sunny" id="b-form" novalidate><input type="hidden" id="b-id">
<label class="fld wide"><span class="caps">Name *</span><input id="b-title" maxlength="200" required placeholder="Division III District Volleyball 2026"></label>
<label class="fld wide"><span class="caps">Games *</span><textarea id="b-text" rows="12" maxlength="2000" required placeholder="Semifinals&#10;Madison 3 vs Ontario 1&#10;Lexington 2 vs Shelby 3&#10;Final&#10;Madison vs Shelby"></textarea>
<span class="hint">Type a round name on its own line, then one game per line with "vs" in the middle. Add scores after each team when they're in. The higher score wins, or put * after the winner's name.</span></label>
<label class="fld wide"><span class="caps">Link for more</span><input id="b-link" inputmode="url" placeholder="Optional: OHSAA or school page"></label>
<div class="wide submitrow"><button class="btn solid" type="submit">Publish bracket</button><button class="btn" type="button" id="b-new">Start a new one</button></div></form></div>
<div id="link-ed" class="box"><span class="caps kicker">Add news</span><h2 class="hed">Post a news link</h2>
<p class="hint">Found a good story the feeds missed? Paste its address. The site reads the headline, summary and photo from the article and posts it as news within 15 minutes, linking to the original. Some sites block that, so typing a headline and a line of summary always works.</p>
<form class="sunny" id="l-form" novalidate>
<label class="fld wide"><span class="caps">Article link *</span><input id="l-url" inputmode="url" autocapitalize="off" spellcheck="false" placeholder="https://www.richlandsource.com/..." required></label>
<label class="fld"><span class="caps">Section</span><select id="l-sec"><option value="">Pick for me</option>{"".join(f"<option>{e(n)}</option>" for _, n in SECTIONS)}</select></label>
<label class="fld"><span class="caps">Headline</span><input id="l-title" maxlength="200" placeholder="Optional"></label>
<label class="fld wide"><span class="caps">Summary</span><textarea id="l-sum" rows="3" maxlength="400" placeholder="Optional: leave blank to use the article's"></textarea></label>
<div class="wide submitrow"><button class="btn solid" type="submit" id="l-btn">Post it</button><span class="formmsg" id="l-msg" role="status" aria-live="polite"></span></div></form></div>
{picker}<div id="list" class="queue"></div></div>
<p class="formmsg" id="desk-msg"></p></section>"""
    script = f"""<script src="{SB_JS}"></script><script>
(()=>{{const C={conf};const sb=supabase.createClient(C.url,C.key);const $=id=>document.getElementById(id);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
const SPECIAL=['Join the team','Advertise'];const NOTQ='("Join the team","Advertise","Bracket","Headline")';let me=null,q='pending';
const say=t=>{{$('desk-msg').textContent=t}};
$('login-form').onsubmit=async ev=>{{ev.preventDefault();const em=$('login-email').value.trim(),pw=$('login-pass').value;
 if(!em||!pw){{$('login-msg').textContent='Type your email and password.';return}}$('login-btn').disabled=true;
 const {{data,error}}=await sb.auth.signInWithPassword({{email:em,password:pw}});$('login-btn').disabled=false;
 if(error){{$('login-msg').textContent='Could not sign in: '+error.message;return}}$('login-msg').textContent='';start(data.session)}};
$('login-link').onclick=async()=>{{const em=$('login-email').value.trim();if(!em){{$('login-msg').textContent='Type your email first.';return}}
 const {{error}}=await sb.auth.signInWithOtp({{email:em,options:{{emailRedirectTo:location.href.split('#')[0],shouldCreateUser:false}}}});
 $('login-msg').textContent=error?'Could not send: '+error.message:'Check your email for the sign-in link.'}};
$('pw-btn').onclick=async()=>{{const pw=$('pw-new').value;if(pw.length<8){{say('Use at least 8 characters.');return}}
 const {{error}}=await sb.auth.updateUser({{password:pw}});$('pw-new').value='';say(error?'Could not change it: '+error.message:'Password saved. Use it to sign in from now on.')}};
$('logout').onclick=()=>sb.auth.signOut().then(()=>location.reload());
async function start(s){{if(me&&s&&s.user.email===me)return;if(!s){{$('login').hidden=false;$('desk').hidden=true;return}}
 me=s.user.email;const {{data}}=await sb.from('admins').select('email').limit(1);
 if(!data||!data.length){{$('login').hidden=false;$('login-msg').textContent=me+' is not on the admin list.';return}}
 $('login').hidden=true;$('desk').hidden=false;$('me').textContent=me;load()}}
sb.auth.getSession().then(r=>start(r.data.session));sb.auth.onAuthStateChange((_,s)=>{{if(s&&!me)start(s)}});
function base(){{return sb.from('submissions').select('*').order('created_at',{{ascending:false}}).limit(200)}}
let NOV=false;const ok=x=>NOV?x:x.or('section.neq.Birthday,verified_at.not.is.null');  // unconfirmed birthday shout-outs wait for their code
const QUERIES={{pending:()=>ok(base().eq('status','pending').not('section','in',NOTQ)),brackets:()=>base().eq('section','Bracket').neq('status','rejected'),
 apps:()=>base().eq('section','Join the team').neq('status','rejected'),ads:()=>base().eq('section','Advertise').eq('status','pending'),
 approved:()=>base().eq('status','approved').not('section','in',NOTQ),rejected:()=>base().eq('status','rejected')}};
async function counts(){{for(const [k,f] of [['pending',()=>ok(sb.from('submissions').select('id',{{count:'exact',head:true}}).eq('status','pending').not('section','in',NOTQ))],
 ['apps',()=>sb.from('submissions').select('id',{{count:'exact',head:true}}).eq('section','Join the team').eq('status','pending')],
 ['ads',()=>sb.from('submissions').select('id',{{count:'exact',head:true}}).eq('section','Advertise').eq('status','pending')]]){{
 const {{count}}=await f();document.querySelector(`[data-q="${{k}}"] .n`).textContent=count||''}}}}
function item(r){{const when=new Date(r.created_at).toLocaleString();
 const facts=[['Section',r.section],['By',r.name],['Role',r.role],['Town',r.town],['Business',r.business],[r.section==='Birthday'?'Birthday':'Event date',r.section==='Birthday'&&r.event_date?new Date(r.event_date+'T12:00').toLocaleDateString('en-US',{{month:'long',day:'numeric'}}):r.event_date],['Price',r.price],['Contact',r.contact],['Email',r.email],['Email check',r.section==='Birthday'?(r.verified_at?'Confirmed with a code':'Not confirmed'):''],['Consent',r.consent&&r.consent.split('\\n').join(' · ')],['Sent',when],['Status',r.status+(r.reviewed_by?' by '+r.reviewed_by:'')]].filter(x=>x[1]);
 const link=/^https?:\/\//i.test(r.link||'')?`<a class="link" href="${{esc(r.link)}}" target="_blank" rel="noopener">Open link</a>`:'';
 const btn=(a,t,c='')=>`<button class="btn sm ${{c}}" type="button" data-act="${{a}}" data-id="${{r.id}}">${{t}}</button>`;
 let acts='';if(r.section==='Join the team')acts=btn('verify-approve','Approve and verify','solid')+btn('reject','Decline');
 else if(r.section==='Bracket')acts=btn('edit-bracket','Edit','solid');
 else if(r.status!=='approved')acts=btn('approve','Approve','solid')+btn('reject','Reject')+(r.email?btn('verify-approve','Approve and verify sender'):'');
 else acts=btn('reject','Take down');acts+=btn('delete','Delete','ghost');
 return `<article class="qitem" data-row='${{esc(JSON.stringify({{email:r.email,name:r.name,role:r.role,title:r.title,summary:r.summary,link:r.link,photo:r.photo_url}}))}}'>
 ${{r.photo_url?`<span class="qphoto"><img src="${{esc(r.photo_url)}}" alt=""></span>`:''}}
 <div class="qbody"><span class="caps kicker">${{esc(r.section)}}</span><h3 class="hed">${{esc(r.title)}}</h3><p class="qtext">${{esc(r.summary||'')}}</p>
 <dl class="facts">${{facts.map(([k,v])=>`<dt>${{k}}</dt><dd>${{esc(v)}}</dd>`).join('')}}</dl>${{link}}<div class="share">${{acts}}</div></div></article>`}}
async function load(){{document.querySelectorAll('[data-q]').forEach(b=>b.setAttribute('aria-pressed',b.dataset.q===q));$('addv').hidden=q!=='verified';$('bracket-ed').hidden=q!=='brackets';$('headline-ed').hidden=q!=='headline';counts();
 const list=$('list');list.innerHTML=q==='headline'?'':'<p class="hint">Loading…</p>';if(q==='headline')return;
 if(q==='verified'){{const {{data,error}}=await sb.from('verified').select('*').order('added_at',{{ascending:false}});
  if(error){{list.innerHTML='';say(error.message);return}}
  list.innerHTML=data.length?data.map(v=>`<article class="qitem"><div class="qbody"><h3 class="hed">${{esc(v.name||v.email)}}</h3><p class="hint">${{esc(v.email)}} ${{v.role?'· '+esc(v.role):''}}</p>
  <div class="share"><button class="btn sm ghost" type="button" data-unverify="${{esc(v.email)}}">Remove</button></div></div></article>`).join(''):'<p class="hint">Nobody verified yet.</p>';return}}
 let {{data,error}}=await QUERIES[q]();if(error&&/verified_at/.test(error.message||'')){{NOV=true;(({{data,error}}=await QUERIES[q]()))}}  // schema not updated yet
 if(error){{list.innerHTML='';say(error.message);return}}
 list.innerHTML=data.length?data.map(item).join(''):'<p class="hint">All caught up. Nothing waiting here.</p>'}}
document.addEventListener('click',async ev=>{{const t=ev.target.closest('[data-q]');if(t){{q=t.dataset.q;load();return}}
 const h=ev.target.closest('[data-head]'),auto=ev.target.closest('#head-auto');
 if(h||auto){{(h||auto).disabled=true;let {{error}}=await sb.from('submissions').delete().eq('section','Headline');let data;
  if(!error&&h){{const p=JSON.parse(h.dataset.head);(({{data,error}}=await sb.from('submissions').insert({{section:'Headline',title:p.title,link:p.link,name:'Sunny Mansfield'}}).select('id')));
   if(!error)(({{error}}=await sb.from('submissions').update({{status:'approved',reviewed_at:new Date().toISOString(),reviewed_by:me}}).eq('id',data[0].id)))}}
  if(error){{say(error.message);(h||auto).disabled=false;return}}
  document.querySelectorAll('.pick.on').forEach(x=>x.classList.remove('on'));
  if(h){{h.closest('.pick').classList.add('on');h.outerHTML='<span class="caps kicker">Headline now</span>'}}
  say(h?'Done. That story will lead the front page within 15 minutes.':'Back to automatic. The newest story with a photo will lead within 15 minutes.');return}}
 const u=ev.target.closest('[data-unverify]');if(u){{const {{error}}=await sb.from('verified').delete().eq('email',u.dataset.unverify);if(error)say(error.message);load();return}}
 const b=ev.target.closest('[data-act]');if(!b)return;const id=b.dataset.id,act=b.dataset.act;const row=JSON.parse(b.closest('.qitem').dataset.row||'{{}}');
 if(act==='edit-bracket'){{$('b-id').value=id;$('b-title').value=row.title||'';$('b-text').value=row.summary||'';$('b-link').value=row.link||'';
  $('b-head').textContent='Edit bracket';$('bracket-ed').scrollIntoView({{behavior:'smooth'}});return}}
 if(act==='delete'&&!b.dataset.sure){{b.dataset.sure=1;b.textContent='Tap again to delete';return}}
 b.disabled=true;let error;
 if(act==='delete'){{(({{error}}=await sb.from('submissions').delete().eq('id',id)));const m=(row.photo||'').match(/\/object\/public\/photos\/(.+)$/);
  if(!error&&m)await sb.storage.from('photos').remove([decodeURIComponent(m[1])])}}
 else{{if(act==='verify-approve'&&row.email){{({{error}}=await sb.from('verified').upsert({{email:row.email.toLowerCase(),name:row.name,role:row.role}}));}}
  if(!error)(({{error}}=await sb.from('submissions').update({{status:act==='reject'?'rejected':'approved',reviewed_at:new Date().toISOString(),reviewed_by:me}}).eq('id',id)));}}
 if(error){{say(error.message);b.disabled=false;return}}say(act==='reject'?'Done. It will not appear on the site.':act==='delete'?'Deleted.':b.closest('.qitem').querySelector('.kicker').textContent==='Birthday'?'Approved. It will show on their birthday (and on Facebook that morning).':'Approved. It will be on the site within 20 minutes.');rebuild();load()}});
// Ask the site to rebuild now (the "rebuild" function), a few seconds after the last change, so approvals,
// take-downs and deletes show in about 2 minutes. Without the function the regular 15-minute refresh still runs.
let rbT;function rebuild(){{clearTimeout(rbT);rbT=setTimeout(async()=>{{const {{error}}=await sb.functions.invoke('rebuild');
 if(!error)say('Updating the site now. Changes show in about 2 minutes.')}},8000)}}
function newBracket(){{$('b-form').reset();$('b-id').value='';$('b-head').textContent='New bracket'}}
$('b-new').onclick=newBracket;
$('l-form').onsubmit=async ev=>{{ev.preventDefault();const m=$('l-msg');let url=$('l-url').value.trim();
 if(url&&!/^https?:\\/\\//i.test(url))url='https://'+url;
 if(!/^https?:\\/\\/[^\\s/]+\\.[^\\s]+/i.test(url)){{m.textContent='Paste the full article link.';return}}
 $('l-btn').disabled=true;m.textContent='Posting…';
 let {{data,error}}=await sb.from('submissions').insert({{section:'Link',link:url,title:$('l-title').value.trim()||'{LINK_TITLE}',
  summary:$('l-sum').value.trim()||null,business:$('l-sec').value||null,name:'Sunny Mansfield'}}).select('id');
 if(!error)(({{error}}=await sb.from('submissions').update({{status:'approved',reviewed_at:new Date().toISOString(),reviewed_by:me}}).eq('id',data[0].id)));
 $('l-btn').disabled=false;if(error){{m.textContent='Could not post it: '+error.message;return}}
 $('l-form').reset();m.textContent='Posted. It will be on the front page within 15 minutes. To remove it, find it under Approved and choose Take down.'}};
$('b-form').onsubmit=async ev=>{{ev.preventDefault();const title=$('b-title').value.trim(),text=$('b-text').value.trim();let link=$('b-link').value.trim();
 if(title.length<3||!/ vs /i.test(text)){{say('Give the bracket a name and at least one game with "vs" in it.');return}}
 if(link&&!/^https?:\\/\\//i.test(link))link='https://'+link;
 const fields={{title,summary:text,link:link||null}};const id=$('b-id').value;let error,data;
 if(id)(({{error}}=await sb.from('submissions').update(fields).eq('id',id)));
 else{{(({{data,error}}=await sb.from('submissions').insert({{...fields,section:'Bracket',name:'Sunny Mansfield'}}).select('id')));
  if(!error)(({{error}}=await sb.from('submissions').update({{status:'approved',reviewed_at:new Date().toISOString(),reviewed_by:me}}).eq('id',data[0].id)))}}
 if(error){{say(error.message);return}}newBracket();say('Bracket saved. It will be on the Sports page within 20 minutes.');load()}};
$('v-add').onclick=async()=>{{const em=$('v-email').value.trim().toLowerCase();if(!em)return;
 const {{error}}=await sb.from('verified').upsert({{email:em,name:$('v-name').value.trim()||null}});if(error)say(error.message);else{{$('v-email').value='';$('v-name').value='';load()}}}};
}})();
</script>"""
    return shell(cfg, f"Admin · {cfg['site_name']}", "Editors only.", f"{site}/admin.html", inner, stories, extra=script, index=False)


def render_story_page(cfg, s, stories):
    """A story page, laid out like the Watch Mansfield episode pages: photo or video, headline, byline and date,
    Share buttons, then the story in a card (neighbors' posts are formatted like the episode write-ups: section
    labels, bullet lists, links), with 'More good news' beside it. Rebuilt every run, so old and new posts match."""
    site = cfg["site_url"].rstrip("/")
    image = s["image"] if (s.get("image") or "").startswith("https://") else None
    life = s["section"] == "life"
    # Celebrations of Life only sit next to each other; every other story page shows other good news.
    same = [o for o in stories if o is not s and (o["section"] == "life") == life]
    media = ""
    if s.get("video"):
        media = (f'<div class="photo video"><iframe src="https://www.youtube-nocookie.com/embed/{e(s["video"])}" title="{e(s["title"])}" '
                 f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" allowfullscreen></iframe></div>')
    elif s.get("community") and "/s/" in s["link"] and s.get("image"):
        src = s["image"] if s["image"].startswith("https://") else "/" + s["image"]
        media = (f'<span class="photo"><img src="{e(src)}" alt="" referrerpolicy="no-referrer"></span>')  # plain photo, no link to its Supabase storage address
    elif s.get("image"):
        media = photo(s)
    section = SECTION_NAMES.get(s["section"], "Celebrations of Life" if life else "Good News")
    by = f'By {e(s["source"])}' if s.get("community") else e(s["source"])
    bits = [by] + ([e(s["role"])] if s.get("community") and s.get("role") else []) + [s["date"].strftime("%B %-d, %Y")]
    facts = s.get("athlete") or {}
    facts_html = ('<dl class="story-facts">' + "".join(f"<div><dt>{e(k)}</dt><dd>{e(v)}</dd></div>" for k, v in facts.items() if v)
                  + "</dl>") if facts else ""
    if s.get("community") and s.get("body"):
        story = format_writeup("\n".join(s["body"]))
        card_head = "The story"
    else:
        story = f"<p>{linkify(s['summary'])}</p>" if s["summary"] else ""
        card_head = "In brief"
        if s.get("link") and not s.get("community"):
            # Outside news: the feed only gives a short excerpt and we can't republish the article, so point to it clearly.
            story += (f'<div class="readfull"><span>This is a short preview from {e(s["source"])}.</span>'
                      f'<a class="btn solid" href="{e(s["link"])}" target="_blank" rel="noopener">Read the full story at {e(s["source"])} &rarr;</a></div>')
    extra = ""
    if s.get("community") and s.get("more"):
        extra = f'<a class="btn" href="{e(s["more"])}" target="_blank" rel="noopener nofollow ugc">Read more</a>'
    url = page_url(cfg, s)
    rows = "".join(thumb(o) for o in same[:6])
    aside = (f'<aside class="watch-aside">{section_head("More Celebrations of Life" if life else "More good news")}<div class="side-list">{rows}</div>'
             f'<a class="explore" href="{e(site)}/{"celebrations.html" if life else ""}">{"All Celebrations of Life" if life else "All stories"} '
             f'<span aria-hidden="true">&rsaquo;</span></a></aside>') if rows else ""
    towns = town_links(cfg, s).replace('class="wrap note towns"', 'class="note towns story-note"')
    body = f"""<div class="sheet"><div class="stripes" aria-hidden="true"></div>{masthead(cfg, site, stories)}<main>
<section class="wrap watch-layout story-layout{"" if aside else " solo"}"><article class="watch-main">
{media}
<header class="watch-head"><span class="caps kicker">{e(section)}</span><h1 class="hed">{e(s["title"])}</h1>
<p class="caps watch-meta">{" &middot; ".join(bits)}{' &middot; <span class="tag">Community</span>' if s.get("community") else ""}</p>
<div class="share"><a class="btn fb" href="{e(fb_share(url))}" target="_blank" rel="noopener">{FB_ICON}Share on Facebook</a>{extra}
<button class="textbtn" type="button" data-copy="{e(url)}">Copy link</button></div></header>
{facts_html}
<section class="wu-card open" aria-labelledby="st-h"><h2 class="wu-head" id="st-h">{card_head}</h2><div class="wu-body">{story}</div></section>
{towns}<p class="note story-note report">Spot a mistake, or are you in this post and want it changed or taken down? <a class="link" href="{e(report_link(cfg, s))}">Tell an editor</a> and we'll fix it fast.</p>
</article>{aside}</section>
{banner(cfg, "A4")}
{submit_band(cfg, site)}<div class="stripes" aria-hidden="true"></div></main>{footer(cfg)}</div>
<div class="toast" id="toast" role="status" aria-live="polite" hidden></div>"""
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
            f"{head(cfg, story_title(cfg, s), story_desc(cfg, s), url, image, 'article', story_data(cfg, s, image), indexable(s))}"
            f"</head><body>{body}{SCRIPT}</body></html>")


# Richland County towns that get their own "good news in ..." page, with a short true description of each.
TOWNS = [
    ("Mansfield", "the county seat of Richland County"), ("Ontario", "a city just west of Mansfield"),
    ("Shelby", "a city in northern Richland County"), ("Lexington", "a village south of Mansfield"),
    ("Bellville", "a village in southern Richland County"), ("Butler", "a village in southeastern Richland County"),
    ("Lucas", "a village southeast of Mansfield"), ("Plymouth", "a village on the Richland and Huron county line"),
    ("Shiloh", "a village in northern Richland County"),
]
TOWN_SURE = {"Mansfield", "Ontario", "Lexington", "Bellville", "Shiloh"}  # the rest are also people's names


def towns_of(*texts):
    """Towns a story is about. Names that are also surnames (Shelby, Lucas, Butler, Plymouth) need a place-like context."""
    text = " ".join(t or "" for t in texts)
    found = []
    for name, _ in TOWNS:
        if name in TOWN_SURE:
            hit = re.search(rf"\b{name}\b", text)
        else:
            hit = re.search(rf"\b(?:in|at|of|from|near|around) {name}\b|\b{name}(?:,? Ohio|,? OH\b| High| City| Schools?\b| Village| Middle| Elementary| Public Library)", text)
        if hit:
            found.append(name)
    return found


def town_url(cfg, name):
    return f"{cfg['site_url'].rstrip('/')}/town/{slug(name)}.html"


def story_towns(s):
    picked = towns_of(s.get("town"), s["title"], s["summary"])
    return [t for t in picked if t != "Mansfield"] + (["Mansfield"] if "Mansfield" in picked else [])


def indexable(s):
    """Only neighbors' own posts go to search engines. Pages that summarize another outlet's story stay out of the
    index, so Google sees original reporting here and sends people to the outlet for theirs."""
    return bool(s.get("community"))


def town_links(cfg, s):
    names = story_towns(s) if s["section"] != "life" else []
    if not names:
        return ""
    links = " · ".join(f'<a class="link" href="{e(town_url(cfg, t))}">More good news from {e(t)}</a>' for t in names[:3])
    return f'<p class="wrap note towns">{links}</p>'


def story_title(cfg, s):
    """The browser and search title: headline, then where it happened, so local searches match."""
    names = story_towns(s)
    where = f" · {names[0]}, Ohio" if names and s.get("community") else ""
    return f"{s['title']}{where} · {cfg['site_name']}"


def story_desc(cfg, s):
    a = s.get("athlete") or {}
    facts = ", ".join(x for x in (a.get("School"), a.get("Sport"), f"Class of {a['Class of']}" if a.get("Class of") else "") if x)
    text = s["summary"] or f"Good news from {cfg['site_name']}"
    return (f"{facts}. {text}" if facts else text)[:300]


def story_data(cfg, s, image):
    site = cfg["site_url"].rstrip("/")
    by = (s.get("source") or "").strip()
    art = {"@context": "https://schema.org", "@type": "NewsArticle", "headline": s["title"][:110],
           "description": s["summary"] or s["title"], "url": page_url(cfg, s), "mainEntityOfPage": page_url(cfg, s),
           "datePublished": s["date"].isoformat(), "dateModified": s["date"].isoformat(), "inLanguage": "en-US",
           "articleSection": SECTION_NAMES.get(s["section"], "Good News"),
           "contentLocation": {"@type": "Place", "name": "Richland County, Ohio"},
           "publisher": publisher(cfg), "image": [image or f"{site}/og.png"]}
    names = story_towns(s)
    if names:
        art["contentLocation"] = {"@type": "Place", "name": f"{names[0]}, Ohio"}
    if s.get("community"):
        art["author"] = {"@type": "Person", "name": by} if by and by != cfg["site_name"] else {"@id": f"{site}/#org"}
    else:
        art["isBasedOn"] = s.get("more") or s["link"]
    life = s["section"] == "life"
    crumbs = [("Sunny Mansfield", f"{site}/")]
    if life:
        crumbs.append(("Celebrations of Life", f"{site}/celebrations.html"))
    elif names:
        crumbs.append((f"Good news in {names[0]}", town_url(cfg, names[0])))
    crumbs.append((s["title"], page_url(cfg, s)))
    return [art, breadcrumbs(crumbs)]


def breadcrumbs(items):
    return {"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": i, "name": n, "item": u} for i, (n, u) in enumerate(items, 1)]}


LINK_TITLE = "(from link)"  # what the admin saves when the editor leaves the headline blank


def page_meta(page, *names):
    """The first filled-in <meta> tag among names (og:title, description...), from an article's HTML."""
    tags = re.findall(r"<meta\b[^>]*>", page, re.I)
    for n in names:
        for tag in tags:
            if re.search(rf"""(?:property|name)\s*=\s*["']{re.escape(n)}["']""", tag, re.I):
                m = re.search(r'content\s*=\s*"([^"]*)"', tag) or re.search(r"content\s*=\s*'([^']*)'", tag)
                if m and m.group(1).strip():
                    return html.unescape(m.group(1)).strip()
    return ""


def add_links(cfg, stories, links, sample=False):
    """Articles an editor pasted in the admin page. We read the headline, summary, photo and outlet from the
    article itself (its share tags), unless the editor typed their own. They show like any feed story."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=cfg.get("max_age_days", 21))
    seen = {re.sub(r"\W+", "", s["title"].lower())[:60] for s in stories} | {s["link"] for s in stories}
    added = []
    for row in links:
        url = full_link(row["link"])
        if not url or row["date"] < cutoff or url in seen:
            continue
        title = "" if row["title"] == LINK_TITLE else row["title"]
        summary, image = row["summary"], None
        source = urllib.parse.urlparse(url).netloc.removeprefix("www.")
        if not sample:
            try:
                _, page = fetch(url, limit=1_500_000)
                found = re.search(r"<title[^>]*>(.*?)</title>", page, re.I | re.S)
                title = title or page_meta(page, "og:title", "twitter:title") or (clean(found.group(1)) if found else "")
                summary = summary or page_meta(page, "og:description", "description", "twitter:description")
                image = page_meta(page, "og:image", "og:image:secure_url", "twitter:image") or None
                source = page_meta(page, "og:site_name") or source
            except Exception as exc:
                note(f"Pasted link {url}: couldn't read it ({exc})")
        if not title:  # the site wouldn't let us read it; make a headline from the address, like "/new-park-opens/"
            parts = urllib.parse.urlparse(url).path.split("/")
            words = re.sub(r"\.\w+$", "", max(parts, key=lambda x: x.count("-")))  # the slug has the most dashes
            words = re.sub(r"[-_]+", " ", re.sub(r"[-_]?\d{5,}$|^\d+[-_]", "", words)).strip()
            title = words[:1].upper() + words[1:] if len(words) > 12 else ""
        if not title:
            note(f"Pasted link {url}: no headline found. Type one in the admin page.")
            continue
        s = {"title": clean(title, 200), "summary": clean(summary, 400), "link": url, "source": source,
             "image": image if image and image.startswith("https://") else None, "photos": True, "date": row["date"],
             "picked": True}
        key = re.sub(r"\W+", "", s["title"].lower())[:60]
        if key in seen:
            continue
        seen |= {key, url}
        s["section"] = SECTION_BY_LABEL.get((row["section"] or "").lower()) or classify(s) or "good"
        if s["section"] not in SECTION_NAMES:
            s["section"] = "good"
        s["id"] = hashlib.sha1((s["link"] + s["title"]).encode()).hexdigest()[:10]
        added.append(s)
    if added:
        note(f"Pasted links: {len(added)} stories")
    return sorted(stories + added, key=lambda s: s["date"], reverse=True)


def merge_community(cfg, stories, community):
    for s in community:
        # A neighbor's post always opens its own page; any outside link becomes a "Read more" button there.
        s["more"], s["link"] = s["link"], page_url(cfg, s)
    return sorted(stories + community, key=lambda s: s["date"], reverse=True)


PAGES = [("", "daily", "1.0"), ("events.html", "daily", "0.9"), ("sports.html", "daily", "0.8"), ("pets.html", "daily", "0.8"), ("businesses.html", "weekly", "0.8"),
         ("fundraisers.html", "daily", "0.7"), ("free.html", "daily", "0.6"), ("jobs.html", "daily", "0.7"), ("homes.html", "daily", "0.7"), ("watch.html", "daily", "0.7"),
         ("submit.html", "monthly", "0.6"), ("birthdays.html", "daily", "0.6"), ("rules.html", "monthly", "0.4"), ("about.html", "monthly", "0.5"), ("contact.html", "yearly", "0.4"),
         ("advertise.html", "monthly", "0.4"), ("privacy.html", "yearly", "0.2"), ("terms.html", "yearly", "0.2")]


AI_BOTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "Claude-SearchBot", "Claude-User", "PerplexityBot",
           "Perplexity-User", "Google-Extended", "Applebot", "Applebot-Extended", "Bingbot", "DuckAssistBot",
           "Amazonbot", "Meta-ExternalAgent", "MistralAI-User", "YouBot", "CCBot"]


def write_search_files(cfg, stories, businesses, out, towns=(), flyers=(), jobs=()):
    """robots.txt, sitemap.xml, news-sitemap.xml and llms.txt: how search engines and AI assistants find the paper."""
    site = cfg["site_url"].rstrip("/")
    now = datetime.now(timezone.utc)
    day = lambda d: d.strftime("%Y-%m-%d")
    urls = [(f"{site}/{p}", day(now), f, pr) for p, f, pr in PAGES if p != "advertise.html" or ads_open(cfg)]
    urls += [(page_url(cfg, s), day(s["date"]), "weekly", "0.7") for s in stories if indexable(s)]
    urls += [(biz_url(cfg, b), day(now), "monthly", "0.6") for b in businesses if not b.get("sample")]
    urls += [(town_url(cfg, t), day(now), "daily", "0.8") for t in towns]
    urls += [(job_url(cfg, j), day(j["date"]), "weekly", "0.6") for j in jobs if j.get("pid")]
    today = eastern().date()
    dayof = lambda f: f["day"].date() if isinstance(f["day"], datetime) else f["day"]
    urls += [(site + event_url(f), day(now), "daily", "0.6") for f in flyers if f.get("day") and dayof(f) >= today]
    (out / "sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n' + "".join(
            f"<url><loc>{e(u)}</loc><lastmod>{m}</lastmod><changefreq>{f}</changefreq><priority>{p}</priority></url>\n"
            for u, m, f, p in urls) + "</urlset>\n")
    # Google News only wants articles from the last two days.
    fresh = [s for s in stories if indexable(s) and s["date"] > now - timedelta(days=2)]
    (out / "news-sitemap.xml").write_text('<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">\n'
        + "".join(f"<url><loc>{e(page_url(cfg, s))}</loc><news:news><news:publication><news:name>{e(cfg['site_name'])}</news:name>"
                  f"<news:language>en</news:language></news:publication><news:publication_date>{s['date'].isoformat()}"
                  f"</news:publication_date><news:title>{e(s['title'])}</news:title></news:news></url>\n" for s in fresh)
        + "</urlset>\n")
    welcome = "".join(f"User-agent: {b}\nAllow: /\nDisallow: /admin.html\nDisallow: /newsletter/\n\n" for b in AI_BOTS)
    if adsense_pub(cfg):  # AdSense checks /ads.txt to confirm which ad sellers may sell space on this site
        (out / "ads.txt").write_text(f"google.com, {adsense_pub(cfg)}, DIRECT, f08c47fec0942fa0\n")
    (out / "robots.txt").write_text(f"""# Everyone is welcome to read, index and cite Sunny Mansfield, including AI assistants.
# Neighbors' good news should be easy to find wherever people ask.
{welcome}User-agent: *
Allow: /
Disallow: /admin.html
Disallow: /artifact.html
Disallow: /newsletter/

Sitemap: {site}/sitemap.xml
Sitemap: {site}/news-sitemap.xml
""")
    recent = [s for s in stories if indexable(s)][:15]
    (out / "llms.txt").write_text(f"""# {cfg['site_name']}

> Sunny Mansfield is a curated local media outlet for Mansfield and Richland County, Ohio, that only shares positive news:
> community wins, local businesses, high school sports, events, fundraisers, free stuff and local jobs. It is created for the
> people and written by the people: neighbors, students, shop owners and mentors send stories, and editors review them.
> It skips crime, crashes and political arguments. Published by Ofgeneration LLC in Mansfield, Ohio.

Coverage area: Mansfield, Ontario, Lexington, Shelby, Bellville, Butler, Lucas, Plymouth and all of Richland County, Ohio.
Updated every 15 minutes. Stories from other outlets (Richland Source, Mansfield News Journal and others) are summarized with a
link to the original reporting; neighbors' own posts are original to Sunny Mansfield.

## Sections

- [Front page]({site}/): today's good news, the editor's headline, local business, causes, sports and events
- [Events]({site}/events.html): things to do in Mansfield and Richland County this weekend and coming up
- [Sports]({site}/sports.html): athlete spotlights, tournament brackets and good sports news
- [Tyger Paws]({site}/pets.html): adoptable dogs at the Richland County Dog Shelter, a dog of the day, and adoption stories
- [Business spotlights]({site}/businesses.html): free pages for Richland County businesses
- [Fundraisers]({site}/fundraisers.html): local causes posted by neighbors (not run or verified by Sunny Mansfield)
- [Free Stuff]({site}/free.html): things neighbors are giving away, free only
- [Jobs]({site}/jobs.html): local jobs posted by Richland County employers
- [Homes]({site}/homes.html): houses and apartments for rent or sale, posted by local owners and agents

## Towns

""" + "".join(f"- [Good news in {t}, Ohio]({town_url(cfg, t)})\n" for t in towns) + f"""
## How to take part

- [Submit good news]({site}/submit.html)
- [Sunny rules and how verification works]({site}/rules.html)
{f"- [Advertise]({site}/advertise.html)" + chr(10) if ads_open(cfg) else ""}- [About]({site}/about.html) and [contact]({site}/contact.html)
- [Privacy policy]({site}/privacy.html) and [terms]({site}/terms.html)
- Contact: {cfg['submit_email']}

## Feeds

- [All stories (RSS)]({site}/feed.xml)
- [Neighbor posts (RSS)]({site}/community.xml)
- [Sitemap]({site}/sitemap.xml)
- [Full text of recent posts and events for AI tools]({site}/llms-full.txt)

## Recent neighbor posts

""" + "".join(f"- [{s['title']}]({page_url(cfg, s)}): {(s['summary'] or '')[:160]}\n" for s in recent))
    full = [f"# {cfg['site_name']}: full text\n\nPositive local news from Mansfield and Richland County, Ohio. "
            f"Generated {now.strftime('%B %-d, %Y at %H:%M UTC')}. Each item links to its page on {site}.\n"]
    soon = [f for f in flyers if f.get("day")]
    if soon:
        full.append("\n## Upcoming events\n")
        full += [f"\n### {f['title']}\n- When: {f['day'].strftime('%A, %B %-d, %Y')}\n- Where: {f.get('town') or 'Richland County'}, Ohio\n"
                 + (f"- Details: {f['link']}\n" if f.get("link") else "") + (f"\n{f['summary']}\n" if f.get("summary") else "")
                 for f in soon]
    full.append("\n## Neighbor posts\n")
    for s in [s for s in stories if indexable(s)][:60]:
        where = ", ".join(story_towns(s)) or "Richland County"
        full.append(f"\n### {s['title']}\n- URL: {page_url(cfg, s)}\n- Published: {s['date'].strftime('%B %-d, %Y')}\n"
                    f"- Section: {SECTION_NAMES.get(s['section'], 'Good News')}\n- Where: {where}, Ohio\n"
                    f"- By: {s.get('source') or cfg['site_name']}\n\n{s['summary']}\n")
    (out / "llms-full.txt").write_text("".join(full))
    if cfg.get("indexnow_key"):
        (out / f"{cfg['indexnow_key']}.txt").write_text(cfg["indexnow_key"])


STORAGE_RE = re.compile(r"https://[a-z0-9]+\.supabase\.co/storage/v1/object/public/[^\s\"'<>()\\]+")


def localize_photos(cfg, out):
    """Copy every Supabase photo the pages use into /photos/, so visitors only ever see our own domain."""
    files = [f for f in out.rglob("*") if f.suffix in (".html", ".xml", ".json", ".txt") and f.is_file()]
    found = set()
    for f in files:
        found.update(STORAGE_RE.findall(f.read_text(errors="ignore")))
    if not found:
        return
    (out / "photos").mkdir(exist_ok=True)
    site = cfg["site_url"].rstrip("/")
    swap, failed = {}, 0
    for url in sorted(found):
        ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
        ext = ext if ext in (".jpg", ".jpeg", ".png", ".webp", ".gif") else ".jpg"
        name = hashlib.sha1(url.encode()).hexdigest()[:16] + ext
        try:
            if not (out / "photos" / name).exists():
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=20) as resp:
                    (out / "photos" / name).write_bytes(resp.read(7_000_000))
            swap[url] = f"{site}/photos/{name}"
        except Exception:
            failed += 1
    for f in files:
        text = f.read_text(errors="ignore")
        new = STORAGE_RE.sub(lambda m: swap.get(m.group(0), m.group(0)), text)
        if new != text:
            f.write_text(new)
    note(f"Photos: copied {len(swap)} community photos to /photos/" + (f" ({failed} failed, left as is)" if failed else ""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="use sample_stories.json instead of live feeds")
    ap.add_argument("--out", default=str(HERE / "site"))
    args = ap.parse_args()
    cfg = json.loads((HERE / "config.json").read_text())
    cfg["canonical_url"] = cfg["site_url"]  # the paper's own domain, from config.json
    cfg["site_url"] = os.environ.get("SITE_URL") or cfg["site_url"]  # set by the GitHub workflow
    watch_file = HERE / "watch.json"
    cfg["watch"] = load_watch(json.loads(watch_file.read_text()), args.sample) if watch_file.exists() else []
    for key in ("supabase_url", "supabase_anon_key"):
        cfg[key] = os.environ.get(key.upper()) or cfg.get(key, "")
    # Sites served from a sub-folder (like user.github.io/sunny-mansfield) need it on local file paths.
    base = urllib.parse.urlparse(cfg["site_url"]).path.rstrip("/")
    rebase = lambda text: re.sub(r'(["(])/(assets|brand)/', rf"\1{base}/\2/", text)
    businesses = json.loads((HERE / "businesses.json").read_text())
    stories = collect(cfg, args.sample)
    got = load_submissions(cfg, args.sample)
    stories = add_links(cfg, stories, got["links"], args.sample)
    stories = merge_community(cfg, stories, got["stories"])
    series, series_extra = collect_series(cfg, stories, args.sample)
    dogs = fetch_shelter_dogs(cfg, args.sample)
    flyers, listings = got["flyers"], got["listings"]
    # Newest spotlights first; the example businesses only fill in until real ones arrive.
    real = list(reversed(got["spotlights"])) + [b for b in businesses if not b.get("sample")]
    businesses = real + [b for b in businesses if b.get("sample")][:max(0, 3 - len(real))]
    for b in businesses:
        b.setdefault("slug", slug(b["name"]))
    out = Path(args.out)
    (out / "s").mkdir(parents=True, exist_ok=True)
    quote = quote_of_the_day()
    page = render_index(cfg, stories, businesses, flyers, listings, got["fundraisers"], got["headlines"], quote, got["jobs"],
                        got["birthdays"], dogs)
    try:
        make_newsletter_issue(cfg, out, stories, flyers, got["fundraisers"], listings, got["jobs"], got["birthdays"], dogs)
    except Exception as exc:  # the newsletter never stops the site from building
        note(f"Newsletter issue: skipped ({exc})")
    (out / "index.html").write_text(rebase(
        "<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1,viewport-fit=cover\">"
        + page.replace('<div class="sheet">', '</head><body><div class="sheet">', 1) + "</body></html>"))
    for folder in ("assets", "brand"):
        shutil.copytree(HERE / folder, out / folder, dirs_exist_ok=True)
    if (HERE / "brand" / "og.png").exists():
        shutil.copy(HERE / "brand" / "og.png", out / "og.png")
    if (HERE / "brand" / "favicon.ico").exists():
        shutil.copy(HERE / "brand" / "favicon.ico", out / "favicon.ico")  # browsers and Google ask for /favicon.ico
    if (HERE / "postcards").is_dir():
        shutil.copytree(HERE / "postcards", out / "postcards", dirs_exist_ok=True)
    # The private preview can't load files, so inline every /assets image and drop the favicon link.
    mime = {"jpg": "jpeg", "webp": "webp", "png": "png"}
    inline = lambda m: (m.group(1) + f"data:image/{mime[m.group(3)]};base64,"
                        + base64.b64encode((HERE / m.group(2)).read_bytes()).decode())
    preview = re.sub(r'(["(])/(assets/[\w./-]+\.(jpg|webp|png))', inline, page).replace('<link rel="icon" type="image/svg+xml" href="/brand/mark.svg">\n', "")
    (out / "artifact.html").write_text(preview)
    try:
        from posts import (make_birthday_invite_image, make_community_feed, make_daily_post, make_post_images,
                           make_quote_feed, make_quote_image, make_series_feed, make_top_story_feed)
        credit = "sunnymansfield.com"  # the line along the bottom of every post image
        make_post_images(stories, out, SECTION_NAMES, UA, credit, outlet_photos=cfg.get("frame_outlet_photos", False), note=note)
        make_daily_post(cfg, stories, businesses, flyers, got["fundraisers"], listings, out, SECTION_NAMES, UA, credit,
                        lambda s: page_url(cfg, s), note=note)
        make_quote_image(quote, out, credit, note=note)
        make_birthday_invite_image(out, credit, note=note)
        make_quote_feed(cfg, quote, out, credit, birthdays=got["birthdays"], note=note)
        make_top_story_feed(cfg, stories, out, lambda s: page_url(cfg, s), note=note)
        make_community_feed(cfg, stories, flyers, got["fundraisers"], businesses, out, lambda s: page_url(cfg, s), UA, credit, note=note)
        make_series_feed(cfg, series, dogs, out, lambda s: page_url(cfg, s), UA, credit, dog_of_the_day, note=note,
                         homes=[{**h, "price_label": home_price_label(h), "facts": home_facts(h), "url": home_url(cfg, h)} for h in got["homes"]])
    except ImportError as exc:
        note(f"Post images: skipped ({exc})")
    for s in stories + series_extra:
        (out / "s" / f"{s['id']}.html").write_text(rebase(render_story_page(cfg, s, stories)))
    for key, page, *_ in SERIES:
        (out / page).write_text(rebase(render_series(cfg, key, series[key], stories, dogs)))
    videos = watch_entries(cfg.get("watch") or [])
    pics = make_watch_images(videos, out)
    for v in videos:
        (out / "watch").mkdir(exist_ok=True)
        (out / v["page"]).write_text(rebase(render_watch_page(cfg, v, videos, stories, v["id"] in pics)))
    (out / "watch.html").write_text(rebase(render_watch_index(cfg, videos, stories)))
    (out / "e").mkdir(exist_ok=True)
    for f in flyers:
        (out / "e" / f"{f['id']}.html").write_text(rebase(render_event_page(cfg, f, flyers, stories)))
    # The quote of the day only goes to Facebook (quotes.xml + quote.jpg from posts.py); the old page sends people home.
    (out / "quote.html").write_text(rebase(render_moved(cfg, stories, "", "Good news")))
    (out / "feed.xml").write_text(render_feed(cfg, stories))
    try:
        from posts import fill_enclosure_lengths
        fill_enclosure_lengths(out, cfg["site_url"])
    except ImportError:
        pass
    (out / "b").mkdir(exist_ok=True)
    for b in businesses:
        (out / "b" / f"{b['slug']}.html").write_text(rebase(render_business(cfg, b, businesses, stories)))
    (out / "businesses.html").write_text(rebase(render_businesses(cfg, businesses, stories)))
    (out / "submit.html").write_text(rebase(render_submit(cfg, stories)))
    (out / "birthdays.html").write_text(rebase(render_birthdays(cfg, got["birthdays"], stories)))
    (out / "rules.html").write_text(rebase(render_rules(cfg, stories)))
    (out / "about.html").write_text(rebase(render_about(cfg, stories)))
    (out / "contact.html").write_text(rebase(render_contact(cfg, stories)))
    for kind in LEGAL:
        (out / f"{kind}.html").write_text(rebase(render_legal(cfg, stories, kind)))
    (out / "fundraisers.html").write_text(rebase(render_fundraisers(cfg, got["fundraisers"], stories)))
    (out / "give").mkdir(exist_ok=True)
    for f in got["fundraisers"]:
        (out / "give" / f"{f['id']}.html").write_text(rebase(render_fund_page(cfg, f, got["fundraisers"], stories)))
    (out / "celebrations.html").write_text(rebase(render_moved(cfg, stories, "", "Good news")))  # page retired; old links go home
    (out / "sports.html").write_text(rebase(render_sports(cfg, stories, got["brackets"])))
    (out / "free.html").write_text(rebase(render_market(cfg, listings, stories)))
    (out / "market.html").write_text(rebase(render_moved(cfg, stories, "free.html", "Free Stuff")))
    (out / "jobs.html").write_text(rebase(render_jobs(cfg, got["jobs"], stories)))
    (out / "jobs").mkdir(exist_ok=True)
    for j in got["jobs"]:
        (out / "jobs" / f"{j['id']}.html").write_text(rebase(render_job_page(cfg, j, stories)))
    (out / "homes.html").write_text(rebase(render_homes(cfg, got["homes"], stories)))
    (out / "homes").mkdir(exist_ok=True)
    for h in got["homes"]:
        (out / "homes" / f"{h['id']}.html").write_text(rebase(render_home_page(cfg, h, stories)))
    (out / "404.html").write_text(rebase(render_not_found(cfg, stories)))
    (out / "advertise.html").write_text(rebase(render_advertise(cfg, stories) if ads_open(cfg) else render_ads_soon(cfg, stories)))
    (out / "admin.html").write_text(rebase((render_admin(cfg, stories, pinned([s for s in stories if s["section"] != "life"], got["headlines"])))))
    (out / "town").mkdir(exist_ok=True)
    live_towns = []
    for name, about in TOWNS:
        (out / "town" / f"{slug(name)}.html").write_text(rebase(render_town(cfg, name, about, stories, businesses, got["fundraisers"])))
        if any(town_items(name, stories, businesses, got["fundraisers"])):
            live_towns.append(name)
    (out / "events.html").write_text(rebase(render_events(cfg, flyers, stories)))
    write_search_files(cfg, stories, businesses, out, live_towns, flyers, got["jobs"])
    localize_photos(cfg, out)
    check_seo(out)
    if not args.sample and os.environ.get("GITHUB_ACTIONS") and datetime.now(timezone.utc).minute < 15:
        ping_indexnow(cfg, stories, flyers)  # once an hour on the live site
    print(f"Built {len(stories)} stories -> {out}", file=sys.stderr)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        import traceback
        print("::error::Build failed: " + traceback.format_exc()[-700:].replace("\n", " | "), file=sys.stderr)
        raise
