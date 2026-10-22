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


def text_post(s, section, credit):
    im = stripes()
    d = ImageDraw.Draw(im)
    d.text((W / 2, 190), "Good news!", font=font("Yellowtail", 84), fill=ORANGE, anchor="mm")
    spaced(d, (W / 2, 270), section, font("Josefin-700", 26), BROWN, anchor="m")
    d.line((W / 2 - 60, 322, W / 2 + 60, 322), fill=ORANGE, width=4)
    f, lines = headline(d, s["title"], W - 220, (84, 76, 68, 60, 54), 6)
    step = round(f.size * 1.2)
    y = 380 + (6 - len(lines)) * step // 2
    for line in lines:
        d.text((W / 2, y), line, font=f, fill=BROWN, anchor="ma")
        y += step
    spaced(d, (W / 2, y + 30), f"via {s['source']}", font("Josefin-700", 22), MUTED, anchor="m")
    footer(im, d, credit, center=True)
    return im


def make_post_images(stories, out, section_names, ua, credit, outlet_photos=False, limit=40, note=print):
    """Write out/p/<id>.jpg for the newest stories and set s["post_image"] to its file name.

    Outlet photos belong to their publishers, so only community photos are framed unless outlet_photos is on.
    Everything else gets the text card.
    """
    folder = Path(out) / "p"
    folder.mkdir(parents=True, exist_ok=True)

    def one(s):
        own = s.get("community") or outlet_photos
        photo = None
        if own and s.get("image"):
            try:
                src = s["image"]
                if src.startswith("assets/"):
                    raw = (HERE / src).read_bytes()
                else:
                    req = urllib.request.Request(src, headers={"User-Agent": ua})
                    with urllib.request.urlopen(req, timeout=15) as resp:
                        raw = resp.read(8_000_000)
                photo = Image.open(io.BytesIO(raw))
                photo.load()
            except Exception:
                photo = None
        section = section_names.get(s["section"], "Good News")
        im = photo_post(s, section, photo, credit) if photo else text_post(s, section, credit)
        im.save(folder / f"{s['id']}.jpg", quality=86, optimize=True, progressive=True)
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
