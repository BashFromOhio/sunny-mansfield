"""Framed 1080x1350 post images for Facebook and Instagram, one per story, in the Sunny Mansfield style.

build.py calls make_post_images(); each image lands at site/p/<story id>.jpg and is listed in feed.xml,
so an RSS-to-Facebook service can post the framed picture instead of a bare link.
"""
import io
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

HERE = Path(__file__).resolve().parent
FONTS = HERE / "brand" / "fonts"
W, H = 1080, 1350
CREAM, BROWN, ORANGE, MUTED = (251, 246, 236), (46, 26, 16), (217, 83, 30), (138, 111, 92)
BORDER = 26  # candy stripe width


def font(name, size):
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)


def stripes():
    """Cream sheet inside an orange and cream candy-stripe border."""
    im = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(im)
    for x in range(-H, W + H, 28):
        d.polygon([(x, H), (x + 14, H), (x + 14 + H, 0), (x + H, 0)], fill=ORANGE)
    im.paste(CREAM, (BORDER, BORDER, W - BORDER, H - BORDER))
    d.rectangle((BORDER + 12, BORDER + 12, W - BORDER - 13, H - BORDER - 13), outline=BROWN, width=2)
    return im


def spaced(d, xy, text, f, fill, track=0.14, anchor="l"):
    """Letter-spaced caps, like the site's Josefin labels. anchor is l, m or r."""
    text = text.upper()
    gap = f.size * track
    width = sum(d.textlength(c, font=f) for c in text) + gap * (len(text) - 1)
    x, y = xy
    x -= {"l": 0, "m": width / 2, "r": width}[anchor]
    for c in text:
        d.text((x, y), c, font=f, fill=fill)
        x += d.textlength(c, font=f) + gap
    return width


def wrap(d, text, f, width, lines):
    """Word-wrap to at most `lines` lines, or None if it doesn't fit."""
    out, cur = [], ""
    for word in text.split():
        test = (cur + " " + word).strip()
        if d.textlength(test, font=f) <= width:
            cur = test
        else:
            if cur:
                out.append(cur)
            cur = word
    out.append(cur)
    return out if len(out) <= lines else None


def headline(d, text, box_w, sizes, lines):
    for size in sizes:
        f = font("Caslon-700", size)
        got = wrap(d, text, f, box_w, lines)
        if got:
            return f, got
    f = font("Caslon-700", sizes[-1])
    got = wrap(d, text, f, box_w, 99)[:lines]
    got[-1] = got[-1].rstrip(".,;:") + "…"
    return f, got


def footer(im, d, credit, center=False):
    logo = Image.open(HERE / "brand" / "post-logo.png").convert("RGBA")
    logo = logo.resize((300, round(300 * logo.height / logo.width)))
    y = H - BORDER - (72 if center else 40) - logo.height
    x = (W - logo.width) // 2 if center else 80
    im.paste(logo, (x, y), logo)
    small = font("Josefin-700", 17)
    if center:
        spaced(d, (W / 2, y - 44), "Good news for all seasons", small, MUTED, anchor="m")
        spaced(d, (W / 2, H - BORDER - 36), credit, font("Josefin-700", 13), MUTED, anchor="m")
    else:
        spaced(d, (W - 80, y + 18), "Good news for all seasons", small, BROWN, anchor="r")
        spaced(d, (W - 80, y + 48), "Mansfield & Richland County", small, MUTED, anchor="r")
        spaced(d, (W - 80, y + 78), credit, font("Josefin-700", 13), MUTED, anchor="r")


def photo_post(s, section, photo, credit):
    im = stripes()
    d = ImageDraw.Draw(im)
    box = (80, 80, W - 80, 800)
    pic = ImageOps.fit(ImageOps.exif_transpose(photo).convert("RGB"), (box[2] - box[0], box[3] - box[1]),
                       centering=(0.5, 0.4))
    im.paste(pic, box[:2])
    d.rectangle((box[0] - 4, box[1] - 4, box[2] + 3, box[3] + 3), outline=BROWN, width=5)
    spaced(d, (box[2], box[3] + 16), f"Photo: {s['source']}", font("Josefin-700", 14), MUTED, anchor="r")
    spaced(d, (80, 850), section, font("Josefin-700", 22), ORANGE)
    f, lines = headline(d, s["title"], W - 160, (62, 56, 50, 44), 3)
    y = 892
    for line in lines:
        d.text((80, y), line, font=f, fill=BROWN)
        y += round(f.size * 1.18)
    footer(im, d, credit)
    return im


def text_post(s, section, credit, script="Good news!"):
    im = stripes()
    d = ImageDraw.Draw(im)
    d.text((W / 2, 190), script, font=font("Yellowtail", 84), fill=ORANGE, anchor="mm")
    spaced(d, (W / 2, 270), section, font("Josefin-700", 26), BROWN, anchor="m")
    d.line((W / 2 - 60, 322, W / 2 + 60, 322), fill=ORANGE, width=4)
    f, lines = headline(d, s["title"], W - 220, (84, 76, 68, 60, 54), 6)
    step = round(f.size * 1.2)
    y = 380 + (6 - len(lines)) * step // 2
    for line in lines:
        d.text((W / 2, y), line, font=f, fill=BROWN, anchor="ma")
        y += step
    if s.get("source"):
        spaced(d, (W / 2, y + 30), f"via {s['source']}", font("Josefin-700", 22), MUTED, anchor="m")
    footer(im, d, credit, center=True)
    return im


def load_photo(src, ua):
    if not src:
        return None
    try:
        if src.startswith("assets/"):
            raw = (HERE / src).read_bytes()
        else:
            req = urllib.request.Request(src, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read(8_000_000)
        photo = Image.open(io.BytesIO(raw))
        photo.load()
        return photo
    except Exception:
        return None


def make_post_images(stories, out, section_names, ua, credit, outlet_photos=False, limit=40, note=print):
    """Write out/p/<id>.jpg for the newest stories and set s["post_image"] to its file name.

    Outlet photos belong to their publishers, so only community photos are framed unless outlet_photos is on.
    Everything else gets the text card.
    """
    folder = Path(out) / "p"
    folder.mkdir(parents=True, exist_ok=True)

    def one(s):
        own = s.get("community") or outlet_photos
        photo = load_photo(s.get("image"), ua) if own else None
        section = section_names.get(s["section"], "Good News")
        im = photo_post(s, section, photo, credit) if photo else text_post(s, section, credit)
        im.save(folder / f"{s['id']}.jpg", quality=86, optimize=True, progressive=False)
        s["post_image"] = f"p/{s['id']}.jpg"
        return bool(photo)

    done = framed = 0
    with ThreadPoolExecutor(8) as pool:
        for ok in pool.map(lambda s: _safe(one, s), stories[:limit]):
            done += ok is not None
            framed += bool(ok)
    note(f"Post images: {done} made, {framed} with photos")


def _safe(fn, s):
    try:
        return fn(s)
    except Exception:
        return None


# ---- Social autopilot: one ready-made post a day, in the weekly rhythm from the social media kit ----
# In these posting feeds each item's <link> is the framed image, because Zapier's RSS trigger doesn't always
# expose enclosures. The story link is in the caption (description) instead.

TAGS = "#SunnyMansfield #MansfieldOhio #RichlandCounty"
THEMES = {  # weekday (Monday = 0): theme name, hashtag
    0: ("Neighbor news", "#GoodNews"),
    1: ("Shop Local Tuesday", "#ShopLocal"),
    2: ("What's happening", "#ThingsToDoMansfield"),
    3: ("Game day", "#RichlandSports"),
    4: ("5 good things", "#FiveGoodThings"),
    5: ("Neighbors helping neighbors", "#GiveLocal"),
    6: ("Story of the week", "#GoodNews"),
}


def roundup_post(items, credit):
    im = stripes()
    d = ImageDraw.Draw(im)
    d.text((W / 2, 170), "This week in Mansfield", font=font("Yellowtail", 72), fill=ORANGE, anchor="mm")
    spaced(d, (W / 2, 240), f"{len(items)} good things", font("Josefin-700", 40), BROWN, anchor="m")
    y = 350
    num, f = font("Caslon-700", 60), font("Caslon-700", 42)
    for i, title in enumerate(items, 1):
        lines = wrap(d, title, f, W - 260, 2)
        if not lines:
            lines = wrap(d, title, f, W - 300, 99)[:2]
            lines[-1] = lines[-1].rstrip(".,;:") + "…"
        d.text((96, y - 8), str(i), font=num, fill=ORANGE)
        for line in lines:
            d.text((170, y), line, font=f, fill=BROWN)
            y += 54
        y += 40
        if i < len(items):
            d.line((170, y - 22, W - 96, y - 22), fill=(216, 194, 160), width=2)
    footer(im, d, credit, center=True)
    return im


def quote_post(quote, credit):
    """Quote of the day in the logo's framed retro style, inverted: cream letters with a brown drop on orange."""
    im = Image.new("RGB", (W, H), ORANGE)
    d = ImageDraw.Draw(im)
    d.rectangle((44, 44, W - 45, H - 45), outline=BROWN, width=6)

    def drop(xy, text, f, off, **kw):
        d.text((xy[0] + off, xy[1] + off), text, font=f, fill=BROWN, **kw)
        d.text(xy, text, font=f, fill=CREAM, **kw)

    drop((W / 2, 200), "Mansfield", font("Yellowtail", 110), 4, anchor="mm")
    spaced(d, (W / 2, 278), "Quote of the day", font("Josefin-700", 32), BROWN, track=0.24, anchor="m")
    text = "\u201c" + quote["text"].strip().strip('"\u201c\u201d') + "\u201d"
    box_top, box_h = 380, 640
    for size in (84, 76, 68, 62, 56, 50, 44, 40):
        f = font("Caslon-700", size)
        lines = wrap(d, text, f, W - 220, 9)
        if lines and len(lines) * size * 1.24 <= box_h:
            break
    else:
        lines = (wrap(d, text, f, W - 220, 99) or [text])[:9]
        lines[-1] = lines[-1].rstrip(".,;:") + "\u2026"
    step = round(f.size * 1.24)
    y = box_top + (box_h - step * len(lines)) // 2
    for line in lines:
        drop((W / 2, y), line, f, max(3, f.size // 16), anchor="ma")
        y += step
    if quote.get("author"):
        spaced(d, (W / 2, y + 26), "\u2014 " + quote["author"], font("Josefin-700", 28), BROWN, anchor="m")
    d.line((140, 1120, W - 140, 1120), fill=BROWN, width=5)
    spaced(d, (W / 2, 1160), "sunnymansfield.com", font("Josefin-700", 46), BROWN, track=0.16, anchor="m")
    spaced(d, (W / 2, 1238), "Good news for all seasons", font("Josefin-700", 17), CREAM, anchor="m")
    return im


def make_quote_image(quote, out, credit, note=print):
    """site/quote.jpg: today's quote as a post image, for the site's Share button and the daily post."""
    if not quote:
        return None
    try:
        im = quote_post(quote, credit)
    except Exception as exc:
        note(f"Quote image: skipped ({exc})")
        return None
    im.save(Path(out) / "quote.jpg", quality=88, optimize=True, progressive=False)
    return "quote.jpg"


def pick(stories, *sections, athletes=False):
    pool = [s for s in stories if s["section"] in sections and (not athletes or s.get("athlete") is not None)]
    pool.sort(key=lambda s: (not s.get("community"), not s.get("image")))
    return pool[0] if pool else None


def make_daily_post(cfg, stories, businesses, flyers, funds, listings, out, section_names, ua, credit, page_url, note=print, quote=None):
    """Write site/social/<date>.jpg and social.xml with today's post. It switches over at 8 a.m. Eastern."""
    from datetime import datetime, timedelta
    from email.utils import format_datetime
    from html import escape as e
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    day = (now - timedelta(hours=8)).replace(hour=8, minute=0, second=0, microsecond=0)
    theme, tag = THEMES[day.weekday()]
    site = cfg["site_url"].rstrip("/")
    story_photo = lambda s: load_photo(s.get("image"), ua) if s.get("community") else None
    im = link = title = text = None
    wd = day.weekday()
    if wd == 1:
        biz = next((b for b in businesses if not b.get("sample")), None)
        if biz:
            fake = {"title": biz["name"], "source": biz.get("neighborhood") or ""}
            photo = load_photo(biz.get("image") if (biz.get("image") or "").startswith("https://") else None, ua)
            im = photo_post(fake, "Shop local", photo, credit) if photo else text_post(fake, "Business spotlight", credit, "Shop local!")
            link, title = f"{site}/b/{biz['slug']}.html", biz["name"]
            text = f"Shop Local Tuesday: {biz['name']}. {biz.get('what') or ''}".strip()
    elif wd == 2 and flyers:
        f = flyers[0]
        when = f["day"].strftime("%A, %B %-d") if f.get("day") else "coming up"
        fake = {"title": f["title"], "source": f.get("town") or ""}
        photo = load_photo(f.get("image"), ua)
        im = photo_post(fake, f"Event · {when}", photo, credit) if photo else text_post(fake, f"Event · {when}", credit, "Save the date!")
        link, title, text = f"{site}/#events", f["title"], f"Coming up {when}: {f['title']}. {f.get('summary') or ''}".strip()
    elif wd == 4:
        top = [s for s in stories if s["section"] != "life"][:5]
        if top:
            im = roundup_post([s["title"] for s in top], credit)
            link, title = f"{site}/", "5 good things this week in Mansfield"
            text = "5 good things this week in Richland County:\n" + "\n".join(f"{i}. {s['title']}" for i, s in enumerate(top, 1))
    elif wd == 5 and funds:
        f = funds[0]
        fake = {"title": f["title"], "source": f.get("by") or ""}
        photo = load_photo(f.get("image"), ua)
        im = photo_post(fake, "Fundraiser", photo, credit) if photo else text_post(fake, "Fundraiser", credit, "Lend a hand!")
        link, title = f"{site}/fundraisers.html#f-{f['id']}", f["title"]
        text = f"Neighbors helping neighbors: {f['title']}. {f.get('summary') or ''} Give if you can, share if you can't.".strip()
    if im is None:
        s = {0: pick(stories, "hoods", "good"), 3: pick(stories, "sports", athletes=True) or pick(stories, "sports"),
             6: pick(stories, "good", "biz", "seniors")}.get(wd) or pick(stories, "good", "hoods", "biz", "seniors", "sports") or (stories or [None])[0]
        if s is None:
            note("Daily post: nothing to post")
            return
        photo = story_photo(s)
        section = section_names.get(s["section"], "Good News")
        im = photo_post(s, section, photo, credit) if photo else text_post(s, section, credit)
        link, title = page_url(s), s["title"]
        text = f"{s['title']}. {s.get('summary') or ''}".strip()
    folder = Path(out) / "social"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{day:%Y-%m-%d}.jpg"
    im.save(folder / name, quality=86, optimize=True, progressive=False)
    image = f"{site}/social/{name}"
    said = ""
    if quote:
        said = f"\n\n💬 Mansfield quote of the day:\n\u201c{quote['text'].strip().strip(chr(34))}\u201d" + (f"\n\u2014 {quote['author']}" if quote.get("author") else "")
    extra = " ".join(x for x in (quote or {}).get("tags", "").split() if x.startswith("#") and x not in TAGS and x != tag)
    caption = f"{theme} ☀️\n\n{text}\n\nRead more: {link}{said}\n\n{TAGS} {tag}{' ' + extra if extra else ''}"
    (Path(out) / "social.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>{e(cfg['site_name'])} daily post</title>
<link>{e(site)}/</link><description>One ready-made post a day for Facebook and Instagram</description>
<item><title>{e(theme + ': ' + title)}</title><link>{e(image)}</link><guid isPermaLink="false">sunny-daily2-{day:%Y-%m-%d}</guid>
<pubDate>{format_datetime(day)}</pubDate><description>{e(caption)}</description>
<enclosure url="{e(image)}" type="image/jpeg" length="0"/></item>
</channel></rss>""")
    note(f"Daily post: {theme} - {title}")


def make_top_story_feed(cfg, stories, out, page_url, note=print):
    """top.xml: the single best new story each day, switching over at noon Eastern. Kept to one post a day
    so the Page stays inside Zapier's free plan alongside the daily post."""
    from datetime import datetime, timedelta
    from email.utils import format_datetime
    from html import escape as e
    from zoneinfo import ZoneInfo
    now = datetime.now(ZoneInfo("America/New_York"))
    noon = (now - timedelta(hours=12)).replace(hour=12, minute=0, second=0, microsecond=0)
    fresh = [s for s in stories if s.get("post_image") and s["section"] != "life"
             and noon - timedelta(hours=36) <= s["date"] <= noon]
    fresh.sort(key=lambda s: (not s.get("community"), not s.get("image"), -s["date"].timestamp()))
    site = cfg["site_url"].rstrip("/")
    item = ""
    if fresh:
        s = fresh[0]
        link, image = page_url(s), f"{site}/{s['post_image']}"
        caption = f"{s['title']}\n\n{s.get('summary') or ''}\n\nRead more: {link}\n\n#SunnyMansfield #MansfieldOhio #GoodNews"
        item = (f"<item><title>{e(s['title'])}</title><link>{e(image)}</link><guid isPermaLink=\"false\">sunny-top2-{s['id']}</guid>"
                f"<pubDate>{format_datetime(noon)}</pubDate><description>{e(caption)}</description>"
                f"<enclosure url=\"{e(image)}\" type=\"image/jpeg\" length=\"0\"/>"
                f"</item>")
        note(f"Top story: {s['title']}")
    (Path(out) / "top.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>{e(cfg['site_name'])} top story</title>
<link>{e(site)}/</link><description>The best new story each day, for Facebook</description>{item}</channel></rss>""")


def make_community_feed(cfg, stories, flyers, funds, businesses, out, page_url, ua, credit, note=print):
    """community.xml: everything people post through the site (stories, athlete spotlights, event flyers,
    fundraisers and business spotlights), each with a framed image, for posting to the Facebook Page."""
    from datetime import datetime, timezone
    from email.utils import format_datetime
    from html import escape as e
    site = cfg["site_url"].rstrip("/")
    folder = Path(out) / "p"
    folder.mkdir(parents=True, exist_ok=True)
    tags = "#SunnyMansfield #MansfieldOhio #RichlandCounty"
    items = []

    def framed(key, title, by, label, image, script):
        path = folder / f"c-{key}.jpg"
        photo = load_photo(image, ua)
        fake = {"title": title, "source": by}
        im = photo_post(fake, label, photo, credit) if photo else text_post(fake, label, credit, script)
        im.save(path, quality=86, optimize=True, progressive=False)
        return f"{site}/p/{path.name}"

    for s in stories:
        if s.get("community") and s.get("post_image"):
            link = page_url(s)
            items.append((s["date"], f"c-{s['id']}", s["title"], link, f"{site}/{s['post_image']}",
                          f"{s['title']}\n\n{s.get('summary') or ''}\n\nRead more: {link}\n\n{tags}"))
    for f in flyers:
        when = f["day"].strftime("%A, %B %-d") if f.get("day") else "Coming up"
        img = framed(f"f{f['id']}", f["title"], f.get("town") or "", f"Event · {when}", f.get("image"), "Save the date!")
        items.append((f["date"], f"f-{f['id']}", f["title"], f"{site}/#events", img,
                      f"{when}: {f['title']}\n\n{f.get('summary') or ''}\n\nMore local events: {site}/#events\n\n{tags} #ThingsToDoMansfield"))
    for f in funds:
        link = f"{site}/fundraisers.html#f-{f['id']}"
        img = framed(f"d{f['id']}", f["title"], f.get("by") or "", "Fundraiser", f.get("image"), "Lend a hand!")
        give = f"\n\nGive here: {f['link']}" if f.get("link") else ""
        items.append((f["date"], f"d-{f['id']}", f["title"], link, img,
                      f"Neighbors helping neighbors: {f['title']}\n\n{f.get('summary') or ''}{give}\n\nGive if you can, share if you can't.\n\n{tags} #GiveLocal"))
    for b in businesses:
        if b.get("community"):  # spotlights sent in through the site
            link = f"{site}/b/{b['slug']}.html"
            img = framed(f"b{b['slug']}", b["name"], b.get("neighborhood") or "", "Business spotlight",
                         b.get("image") if (b.get("image") or "").startswith("https://") else None, "Shop local!")
            items.append((b.get("date") or datetime.now(timezone.utc), f"b-{b['slug']}", b["name"], link, img,
                          f"Shop local: {b['name']}\n\n{b.get('what') or ''}\n\nRead more: {link}\n\n{tags} #ShopLocal"))
    items.sort(key=lambda x: x[0], reverse=True)
    body = "".join(
        f"<item><title>{e(t)}</title><link>{e(img)}</link><guid isPermaLink=\"false\">sunny2-{gid}</guid>"
        f"<pubDate>{format_datetime(d)}</pubDate><description>{e(cap)}</description>"
        f"<enclosure url=\"{e(img)}\" type=\"image/jpeg\" length=\"0\"/>"
        f"</item>"
        for d, gid, t, link, img, cap in items[:30])
    (Path(out) / "community.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>{e(cfg['site_name'])}: posted by neighbors</title>
<link>{e(site)}/</link><description>Everything posted through Sunny Mansfield, for the Facebook Page</description>{body}</channel></rss>""")
    note(f"Community feed: {len(items)} posts")


def fill_enclosure_lengths(out, site):
    """Give each enclosure its real file size. Some RSS readers, Zapier among them, skip enclosures with length 0."""
    import re
    site = site.rstrip("/")
    for name in ("social.xml", "community.xml", "top.xml", "feed.xml"):
        path = Path(out) / name
        if not path.exists():
            continue
        def size(m):
            local = Path(out) / m.group(1)[len(site) + 1:] if m.group(1).startswith(site + "/") else None
            n = local.stat().st_size if local and local.exists() else 0
            return f'<enclosure url="{m.group(1)}" type="image/jpeg" length="{n}"/>'
        path.write_text(re.sub(r'<enclosure url="([^"]+)" type="image/jpeg" length="0"/>', size, path.read_text()))
