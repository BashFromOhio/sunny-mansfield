"""Framed 1080x1350 post images for Facebook and Instagram, one per story, in the Sunny Mansfield style.

build.py calls make_post_images(); each image lands at site/p/<story id>.jpg and is listed in feed.xml,
so an RSS-to-Facebook service can post the framed picture instead of a bare link.
"""
import io
import urllib.request
from datetime import date, datetime, timedelta
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


def cake(d, cx, top, color=ORANGE, scale=1.0):
    """A little line-drawn birthday cake, like the one on the site."""
    k = scale
    w2, t = 150 * k, 5 * k
    d.polygon([(cx, top), (cx + 12 * k, top + 26 * k), (cx, top + 40 * k), (cx - 12 * k, top + 26 * k)], fill=color)  # flame
    d.rectangle((cx - 7 * k, top + 46 * k, cx + 7 * k, top + 90 * k), fill=color)  # candle
    d.rectangle((cx - w2 * .8, top + 90 * k, cx + w2 * .8, top + 140 * k), outline=color, width=round(t))
    d.rectangle((cx - w2, top + 140 * k, cx + w2, top + 210 * k), outline=color, width=round(t))
    pts = [(cx - w2 * .8 + i * (w2 * 1.6 / 8), top + (112 if i % 2 else 100) * k) for i in range(9)]
    d.line(pts, fill=color, width=round(t), joint="curve")


def birthday_post(b, credit):
    """One approved birthday shout-out: the name big, the optional message and who it's from."""
    im = stripes()
    d = ImageDraw.Draw(im)
    d.text((W / 2, 190), "Happy Birthday!", font=font("Yellowtail", 110), fill=ORANGE, anchor="mm")
    spaced(d, (W / 2, 280), "A Sunny Mansfield shout-out", font("Josefin-700", 24), BROWN, anchor="m")
    f, lines = headline(d, b["name"], W - 200, (110, 96, 84, 72, 62), 2)
    step = round(f.size * 1.12)
    got, mf = [], None
    if b.get("message"):
        text = "\u201c" + b["message"].strip().strip('"\u201c\u201d') + "\u201d"
        for size in (46, 42, 38, 34, 30):
            mf = font("Caslon-700", size)
            got = wrap(d, text, mf, W - 240, 5)
            if got:
                break
        else:
            got = (wrap(d, text, mf, W - 240, 99) or [text])[:5]
            got[-1] = got[-1].rstrip(".,;:") + "\u2026"
    # Center the cake, name, message and "From" between the heading and the logo.
    height = 170 + step * len(lines) + (40 + round(mf.size * 1.3) * len(got) if got else 0) + (60 if b.get("by") else 0)
    y = 330 + max(0, (1060 - 330 - height) // 2)
    cake(d, W / 2, y, scale=0.7)
    y += 170
    for line in lines:
        d.text((W / 2, y), line, font=f, fill=BROWN, anchor="ma")
        y += step
    if got:
        y += 40
        for line in got:
            d.text((W / 2, y), line, font=mf, fill=MUTED, anchor="ma")
            y += round(mf.size * 1.3)
    if b.get("by"):
        spaced(d, (W / 2, y + 30), "\u2014  " + b["by"], font("Josefin-700", 26), ORANGE, anchor="m")
    footer(im, d, credit, center=True)
    return im


def birthday_invite_post(credit):
    """The every-other-day ask: send a free birthday shout-out. Orange, like the quote of the day."""
    im = Image.new("RGB", (W, H), ORANGE)
    d = ImageDraw.Draw(im)
    d.rectangle((44, 44, W - 45, H - 45), outline=BROWN, width=6)

    def drop(xy, text, f, off, **kw):
        d.text((xy[0] + off, xy[1] + off), text, font=f, fill=BROWN, **kw)
        d.text(xy, text, font=f, fill=CREAM, **kw)

    drop((W / 2, 190), "Birthday", font("Yellowtail", 150), 5, anchor="mm")
    spaced(d, (W / 2, 290), "Shout-outs", font("Josefin-700", 56), BROWN, track=0.24, anchor="m")
    cake(d, W / 2, 380, color=CREAM, scale=0.62)
    f = font("Caslon-700", 54)
    y = 560
    for line in ("Know someone with a", "birthday coming up?"):
        drop((W / 2, y), line, f, 3, anchor="ma")
        y += 66
    y += 30
    small = font("Caslon-700", 36)
    for n, text in enumerate(("Type their name and birthday", "Add a special message (optional)",
                              "We wish them a happy birthday here!"), 1):
        d.ellipse((150, y - 4, 202, y + 48), fill=BROWN)
        d.text((176, y + 22), str(n), font=font("Josefin-700", 30), fill=CREAM, anchor="mm")
        d.text((228, y + 4), text, font=small, fill=CREAM)
        y += 84
    d.line((140, 1120, W - 140, 1120), fill=BROWN, width=5)
    spaced(d, (W / 2, 1160), "sunnymansfield.com/birthdays", font("Josefin-700", 40), BROWN, track=0.12, anchor="m")
    spaced(d, (W / 2, 1238), "Free · Every shout-out is approved by an editor", font("Josefin-700", 17), CREAM, anchor="m")
    return im


def make_birthday_invite_image(out, credit, note=print):
    """site/birthday.jpg: the birthday shout-out invite, also the share picture for birthdays.html."""
    try:
        birthday_invite_post(credit).save(Path(out) / "birthday.jpg", quality=88, optimize=True, progressive=False)
    except Exception as exc:
        note(f"Birthday invite image: skipped ({exc})")


BIRTHDAY_TAGS = "#SunnyMansfield #MansfieldOhio #RichlandCounty #HappyBirthday"
BIRTHDAY_HOUR = 9   # approved shout-outs post at 9 a.m. Eastern on the birthday
INVITE_HOUR = 12    # the "send a shout-out" invite posts at noon Eastern, every other day


def birthday_items(cfg, birthdays, folder, credit, now, note=print):
    """Feed items for today's approved shout-outs (from 9 a.m.) and the every-other-day invite (at noon)."""
    from email.utils import format_datetime
    from html import escape as e
    site = cfg["site_url"].rstrip("/")
    link = f"{site}/birthdays.html"
    items = []
    at = now.replace(hour=BIRTHDAY_HOUR, minute=0, second=0, microsecond=0)
    if now >= at:
        for b in birthdays[:8]:
            key = f"{at:%Y-%m-%d}-{b['id']}"
            try:
                birthday_post(b, credit).save(folder / f"birthday-{key}.jpg", quality=88, optimize=True, progressive=False)
            except Exception as exc:
                note(f"Birthday post: skipped ({exc})")
                continue
            note_line = f"\n\n\u201c{b['message']}\u201d" if b.get("message") else ""
            note_line += f"\n\u2014 {b['by']}" if b.get("by") else ""
            caption = (f"\U0001F382 Happy birthday, {b['name']}! \U0001F389{note_line}\n\n"
                       f"From all of us at Sunny Mansfield, have a sunny day!\n\n"
                       f"Know someone with a birthday coming up? Send a free shout-out: {link}\n\n{BIRTHDAY_TAGS}")
            image = f"{site}/social/birthday-{key}.jpg"
            items.append((at, f"""<item><title>{e("Happy birthday, " + b["name"] + "!")}</title><link>{e(image)}</link>
<guid isPermaLink="false">sunny-birthday-{key}</guid><pubDate>{format_datetime(at)}</pubDate>
<description>{e(caption)}</description><enclosure url="{e(image)}" type="image/jpeg" length="0"/></item>"""))
    # The invite: every other day at noon. Keep the last two, so the feed always has one to show.
    day = now.replace(hour=INVITE_HOUR, minute=0, second=0, microsecond=0)
    if now < day:
        day -= timedelta(days=1)
    invites = []
    while len(invites) < 2:
        if (day.date() - INVITE_START).days % 2 == 0:
            invites.append(day)
        day -= timedelta(days=1)
    caption = ("\U0001F382 Know someone with a birthday coming up?\n\n"
               "Send them a free Sunny Mansfield birthday shout-out! Here's how:\n"
               "1\ufe0f\u20e3 Type their name and birthday (just the month and day)\n"
               "2\ufe0f\u20e3 Want to say more? Add a special message and who it's from\n"
               "3\ufe0f\u20e3 An editor approves it, and we wish them a happy birthday on the site and right here on Facebook on their big day\n\n"
               f"Send one here: {link}\n\n{BIRTHDAY_TAGS}")
    image = f"{site}/birthday.jpg"
    for when in invites:
        items.append((when, f"""<item><title>Send a free birthday shout-out</title><link>{e(image)}</link>
<guid isPermaLink="false">sunny-birthday-invite-{when:%Y-%m-%d}</guid><pubDate>{format_datetime(when)}</pubDate>
<description>{e(caption)}</description><enclosure url="{e(image)}" type="image/jpeg" length="0"/></item>"""))
    return items


INVITE_START = date(2026, 11, 17)  # invites go out on this day and every second day after


def make_quote_feed(cfg, quote, out, credit, birthdays=(), note=print):
    """quotes.xml: a framed quote image twice a day (8 a.m. and 8 p.m. Eastern), ready to post to Facebook.
    Like social.xml, each item's link is the image itself and the caption is in the description.
    The same feed carries birthday posts, so the quote's Facebook Zap posts them too: each approved shout-out
    at 9 a.m. on the birthday, and an invite to send one every other day at noon."""
    from email.utils import format_datetime
    from html import escape as e
    from zoneinfo import ZoneInfo
    site = cfg["site_url"].rstrip("/")
    folder = Path(out) / "social"
    folder.mkdir(parents=True, exist_ok=True)
    items = []
    for q in (quote or {}).get("recent", []):
        key = q["at"].strftime("%Y-%m-%d-") + ("pm" if q["at"].hour >= 12 else "am")
        try:
            quote_post(q, credit).save(folder / f"quote-{key}.jpg", quality=88, optimize=True, progressive=False)
        except Exception as exc:
            note(f"Quote feed: skipped ({exc})")
            break
        text = q["text"].strip().strip('"\u201c\u201d')
        tags = " ".join(dict.fromkeys(TAGS.split() + [x for x in q.get("tags", "").split() if x.startswith("#")]))
        caption = (f"💬 Mansfield quote of the day\n\n\u201c{text}\u201d" + (f"\n\u2014 {q['author']}" if q.get("author") else "")
                   + f"\n\nMore good news: {site}/\n\n{tags}")
        image = f"{site}/social/quote-{key}.jpg"
        items.append((q["at"], f"""<item><title>{e("Quote of the day: " + text[:80])}</title><link>{e(image)}</link>
<guid isPermaLink="false">sunny-quote-{key}</guid><pubDate>{format_datetime(q["at"])}</pubDate>
<description>{e(caption)}</description><enclosure url="{e(image)}" type="image/jpeg" length="0"/></item>"""))
    items += birthday_items(cfg, list(birthdays), folder, credit, datetime.now(ZoneInfo("America/New_York")), note=note)
    items.sort(key=lambda x: x[0], reverse=True)
    (Path(out) / "quotes.xml").write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>{e(cfg['site_name'])} quote of the day</title><link>{e(site)}/</link>
<description>A Mansfield quote twice a day, birthday shout-outs, ready for Facebook</description>
{"".join(x for _, x in items)}
</channel></rss>""")
    note(f"Quote feed: {quote['text'][:60] if quote else 'no quote'}; {len(birthdays)} birthdays today")


def pick(stories, *sections, athletes=False):
    pool = [s for s in stories if s["section"] in sections and (not athletes or s.get("athlete") is not None)]
    pool.sort(key=lambda s: (not s.get("community"), not s.get("image")))
    return pool[0] if pool else None


def make_daily_post(cfg, stories, businesses, flyers, funds, listings, out, section_names, ua, credit, page_url, note=print):
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
        link, title = f"{site}/give/{f['id']}.html", f["title"]
        text = f"Neighbors helping neighbors: {f['title']}. {f.get('summary') or ''} Give if you can, share if you can't.".strip()
    if im is None:
        s = {0: pick(stories, "hoods", "good"), 3: pick(stories, "sports", athletes=True) or pick(stories, "sports"),
             6: pick(stories, "good", "biz", "seniors")}.get(wd) or pick(stories, "good", "hoods", "biz", "seniors", "sports") or next((x for x in stories if x["section"] != "life"), None)
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
    caption = f"{theme} ☀️\n\n{text}\n\nRead more: {link}\n\n{TAGS} {tag}"
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
        items.append((f["date"], f"f-{f['id']}", f["title"], f"{site}/e/{f['id']}.html", img,
                      f"{when}: {f['title']}\n\n{f.get('summary') or ''}\n\nMore local events: {site}/#events\n\n{tags} #ThingsToDoMansfield"))
    for f in funds:
        link = f"{site}/give/{f['id']}.html"
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
    for name in ("social.xml", "community.xml", "top.xml", "quotes.xml", "feed.xml"):
        path = Path(out) / name
        if not path.exists():
            continue
        def size(m):
            local = Path(out) / m.group(1)[len(site) + 1:] if m.group(1).startswith(site + "/") else None
            n = local.stat().st_size if local and local.exists() else 0
            return f'<enclosure url="{m.group(1)}" type="image/jpeg" length="{n}"/>'
        path.write_text(re.sub(r'<enclosure url="([^"]+)" type="image/jpeg" length="0"/>', size, path.read_text()))


def watch_image(title, host, photo=None, credit="sunnymansfield.com"):
    """Facebook link preview for a Watch Mansfield video (1200x630): the video's picture in the same frame as the site,
    with a Play button, the orange WATCH MANSFIELD tag, the title, and the orange timer bar along the bottom."""
    from PIL import ImageChops, ImageFilter
    ww, hh = 1200, 630
    im = Image.new("RGB", (ww, hh), (20, 20, 20))
    if photo:
        im.paste(ImageOps.fit(ImageOps.exif_transpose(photo).convert("RGB"), (ww, hh), centering=(0.5, 0.45)))
    else:  # no picture: the site's dark card with an orange glow
        glow = Image.new("L", (ww, hh), 0)
        ImageDraw.Draw(glow).ellipse((620, -260, 1320, 440), fill=150)
        im = Image.composite(Image.new("RGB", (ww, hh), ORANGE), im, glow.filter(ImageFilter.GaussianBlur(120)))
    # darken toward the bottom so the white title always reads
    ramp = Image.linear_gradient("L").resize((ww, hh))
    shade = ramp.point(lambda v: int(min(235, max(0, v - 40) * 1.45)))  # bottom: up to ~92% black under the title
    top = ramp.point(lambda v: int(max(0, 140 - v * 1.6)))  # top: a light shade behind the play button and name
    im = Image.composite(Image.new("RGB", (ww, hh), (0, 0, 0)), im, ImageChops.lighter(shade, top))
    d = ImageDraw.Draw(im)
    white = (255, 255, 255)
    d.ellipse((56, 56, 152, 152), fill=ORANGE)
    d.polygon([(93, 80), (93, 128), (133, 104)], fill=white)
    spaced(d, (ww - 56, 70), "Sunny Mansfield", font("Josefin-700", 22), white, track=0.2, anchor="r")
    f, lines = headline(d, title, ww - 112, (64, 58, 52, 46, 42), 3)
    step = round(f.size * 1.16)
    meta_y = hh - 74
    y = meta_y - 22 - step * len(lines)
    for line in lines:
        d.text((56, y), line, font=f, fill=white)
        y += step
    tag = font("Josefin-700", 22)
    ty = meta_y - 22 - step * len(lines) - 58
    tw = spaced(d, (0, -100), "Watch Mansfield", tag, white, track=0.16)  # measure off-canvas
    d.rectangle((56, ty, 56 + tw + 28, ty + 40), fill=ORANGE)
    spaced(d, (70, ty + 10), "Watch Mansfield", tag, white, track=0.16)
    meta = f"{host}  ·  {credit}" if host else credit
    spaced(d, (56, meta_y), meta, font("Josefin-700", 18), (225, 225, 225), track=0.12)
    d.rectangle((0, hh - 10, ww * 0.62, hh), fill=ORANGE)
    return im
