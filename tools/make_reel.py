"""This week's Facebook Reel: a 15-second vertical video (720x1280) of the week's most-read neighbor stories, made from
their real photos and headlines. No AI footage: every picture is the one the neighbor sent in.

  python3 tools/make_reel.py site            # live: stories from Supabase, ranked by page views this week
  python3 tools/make_reel.py site --sample   # preview: sample photos and headlines

Writes site/reel/this-week.mp4, site/reel/this-week.jpg (cover) and site/reel/caption.txt. Needs ffmpeg (GitHub's
Ubuntu runners have it). If anything is missing it prints why and exits 0, so it never breaks the site build.
"""
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent.parent
FONTS = HERE / "brand" / "fonts"
W, H = 720, 1280
CREAM, BROWN, ORANGE, GOLD, WHITE = (251, 246, 236), (46, 26, 16), (232, 115, 42), (246, 190, 60), (255, 255, 255)
STORY_SECTIONS = {"Good News", "Sports", "Neighborhoods", "Local Business", "Student Voices", "Seniors",
                  "Athlete Spotlight", "Business Spotlight"}
SLIDES = 4                     # stories in the reel
INTRO, OUTRO, FADE = 2.0, 2.2, 0.35
STORY = (15 - INTRO - OUTRO + FADE * (SLIDES + 1)) / SLIDES   # so the whole reel lands on 15 seconds
FFMPEG = "ffmpeg"
UA = "Mozilla/5.0 (sunnymansfield.com reel maker)"


def font(name, size):
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)


def headline(t):
    """Neighbors often type headlines in ALL CAPS or with emoji. Make them read well on a phone."""
    t = re.sub(r"[^\w\s.,:;!?'’\"“”&$%#()\-–—/]", "", t or "").strip()
    t = re.sub(r"\s+", " ", t)
    letters = [c for c in t if c.isalpha()]
    if letters and sum(c.isupper() for c in letters) / len(letters) > 0.7:
        small = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the", "to", "is", "it's", "its"}
        words = t.lower().split()
        t = " ".join(w if i and w in small else w[:1].upper() + w[1:] for i, w in enumerate(words))
    return t


def wrap(d, text, f, width, max_lines):
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        if d.textlength(test, font=f) <= width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        while d.textlength(lines[-1] + "…", font=f) > width and " " in lines[-1]:
            lines[-1] = lines[-1].rsplit(" ", 1)[0]
        lines[-1] += "…"
    return lines


def cover(img, w, h):
    img = img.convert("RGB")
    s = max(w / img.width, h / img.height)
    img = img.resize((max(w, round(img.width * s)), max(h, round(img.height * s))), Image.LANCZOS)
    x, y = (img.width - w) // 2, (img.height - h) // 3   # keep a bit more of the top: faces, signs
    return img.crop((x, y, x + w, y + h))


def gradient(w, h, top, bottom):
    g = Image.new("RGB", (w, h), top)
    d = ImageDraw.Draw(g)
    for y in range(h):
        t = y / max(1, h - 1)
        d.line([(0, y), (w, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)))
    return g


def intro_slide():
    img = gradient(W, H, (240, 132, 52), (196, 72, 22))
    d = ImageDraw.Draw(img)
    for i in range(14):   # sunburst rays
        import math
        a = math.pi * (i / 13)
        d.line([(W / 2, H * 0.62), (W / 2 - math.cos(a) * W, H * 0.62 - math.sin(a) * W)], fill=(250, 160, 70), width=18)
    d.ellipse([W / 2 - 150, H * 0.62 - 150, W / 2 + 150, H * 0.62 + 150], fill=GOLD)
    d.text((W / 2, 300), "This week in", font=font("Yellowtail", 86), fill=CREAM, anchor="mm")
    d.text((W / 2, 410), "MANSFIELD", font=font("Caslon-700", 96), fill=WHITE, anchor="mm", stroke_width=4, stroke_fill=BROWN)
    d.text((W / 2, 520), "GOOD NEWS · TOP STORIES", font=font("Josefin-700", 30), fill=CREAM, anchor="mm")
    d.text((W / 2, H - 120), "SUNNYMANSFIELD.COM", font=font("Josefin-700", 32), fill=CREAM, anchor="mm")
    return img


def story_slide(photo, title, kicker, n, total):
    img = cover(photo, W, H)
    shade = gradient(W, H // 2, (0, 0, 0), (0, 0, 0)).convert("L")
    mask = Image.linear_gradient("L").resize((W, H // 2))
    dark = Image.new("RGB", (W, H // 2), (24, 14, 8))
    img.paste(dark, (0, H - H // 2), Image.eval(mask, lambda v: int(v * 0.92)))
    d = ImageDraw.Draw(img)
    f = font("Caslon-700", 58)
    lines = wrap(d, title, f, W - 96, 4)
    y = H - 140 - len(lines) * 68
    d.rounded_rectangle([48, y - 74, 48 + d.textlength(kicker.upper(), font=font("Josefin-700", 26)) + 36, y - 30],
                        radius=8, fill=ORANGE)
    d.text((66, y - 52), kicker.upper(), font=font("Josefin-700", 26), fill=WHITE, anchor="lm")
    for i, line in enumerate(lines):
        d.text((48, y + i * 68), line, font=f, fill=WHITE, stroke_width=2, stroke_fill=(20, 10, 4))
    d.text((48, H - 70), "SUNNYMANSFIELD.COM", font=font("Josefin-700", 26), fill=GOLD, anchor="lm")
    d.text((W - 48, H - 70), f"{n}/{total}", font=font("Josefin-700", 26), fill=CREAM, anchor="rm")
    return img


def outro_slide():
    img = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, W, 24], fill=ORANGE)
    d.rectangle([0, H - 24, W, H], fill=ORANGE)
    d.text((W / 2, 430), "Read every story", font=font("Yellowtail", 80), fill=ORANGE, anchor="mm")
    d.text((W / 2, 540), "SUNNYMANSFIELD.COM", font=font("Caslon-700", 46), fill=BROWN, anchor="mm")
    d.text((W / 2, 700), "Know a neighbor doing", font=font("Josefin-700", 34), fill=BROWN, anchor="mm")
    d.text((W / 2, 748), "great things? Tell us!", font=font("Josefin-700", 34), fill=BROWN, anchor="mm")
    d.text((W / 2, 900), "Link in the comments", font=font("Josefin-700", 28), fill=(138, 111, 92), anchor="mm")
    return img


def get(url, headers=None, limit=12_000_000):
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=25) as r:
        return r.read(limit)


def live_stories():
    cfg = json.loads((HERE / "config.json").read_text())
    base, key = cfg["supabase_url"].rstrip("/") + "/rest/v1/", cfg["supabase_anon_key"]
    h = {"apikey": key, "Authorization": f"Bearer {key}"}
    since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%S")
    rows = json.loads(get(base + f"published?select=id,created_at,section,title,photo_url&created_at=gte.{since}&order=created_at.desc", h))
    try:
        views = {p["path"]: p["views"] for p in json.loads(get(base + "reach_pages?select=path,views", h))}
    except Exception:
        views = {}
    out = []
    for r in rows:
        photo = (r.get("photo_url") or "").split()
        if r["section"] not in STORY_SECTIONS or not photo or not photo[0].startswith("https://"):
            continue
        sid = hashlib.sha1(("community" + r["id"] + r["created_at"] + r["title"]).encode()).hexdigest()[:10]
        out.append({"title": r["title"], "kicker": r["section"], "photo": photo[0], "views": views.get(f"/s/{sid}.html", 0),
                    "url": f"{cfg['site_url'].rstrip('/')}/s/{sid}.html"})
    out.sort(key=lambda s: s["views"], reverse=True)   # most read first; ties keep newest first
    return out


def sample_stories():
    names = ["Ocie Hill kids paint a mural on the old corner store", "LADY TYGERS DOMINATE IN DOUBLE WIN!",
             "Neighbors fill the food pantry shelves before Thanksgiving", "A new bakery opens on Main Street"]
    return [{"title": t, "kicker": k, "photo": str(HERE / "assets" / "sample" / f"{i + 1}.jpg"), "views": 0, "url": ""}
            for i, (t, k) in enumerate(zip(names, ["Neighborhoods", "Sports", "Good News", "Local Business"]))]


def render(stories, out):
    tmp = Path(tempfile.mkdtemp())
    slides = [(intro_slide(), INTRO, False)]
    for i, s in enumerate(stories, 1):
        raw = Path(s["photo"]).read_bytes() if not s["photo"].startswith("http") else get(s["photo"])
        slides.append((story_slide(Image.open(io.BytesIO(raw)), headline(s["title"]), s["kicker"], i, len(stories)), STORY, True))
    slides.append((outro_slide(), OUTRO, False))
    args, filters = [FFMPEG, "-y", "-loglevel", "error"], []
    fps = 30
    for i, (img, dur, zoom) in enumerate(slides):
        p = tmp / f"{i}.png"
        img.save(p)
        args += ["-loop", "1", "-t", f"{dur:.3f}", "-i", str(p)]
        frames = round(dur * fps)
        if zoom:   # slow push-in so photos feel alive
            filters.append(f"[{i}:v]scale={W * 2}:{H * 2},zoompan=z='1+0.07*on/{frames}':x='iw/2-(iw/zoom/2)':y='ih/3-(ih/zoom/3)'"
                           f":d={frames}:s={W}x{H}:fps={fps},setsar=1,format=yuv420p[v{i}]")
        else:
            filters.append(f"[{i}:v]fps={fps},scale={W}:{H},setsar=1,format=yuv420p[v{i}]")
    last, t = "v0", slides[0][1]
    for i in range(1, len(slides)):
        t -= FADE
        filters.append(f"[{last}][v{i}]xfade=transition=fade:duration={FADE}:offset={t:.3f}[x{i}]")
        last, t = f"x{i}", t + slides[i][1]
    out.mkdir(parents=True, exist_ok=True)
    args += ["-filter_complex", ";".join(filters), "-map", f"[{last}]", "-r", str(fps), "-c:v", "libx264",
             "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out / "this-week.mp4")]
    subprocess.run(args, check=True)
    slides[1][0].save(out / "this-week.jpg", quality=88)
    shutil.rmtree(tmp, ignore_errors=True)


def caption(stories):
    lines = ["This week's good news from Mansfield ☀️", ""]
    lines += [f"{i}. {headline(s['title'])}" for i, s in enumerate(stories, 1)]
    lines += ["", "Read every story at sunnymansfield.com (link in the comments). Know a neighbor doing great things? Tell us!",
              "", "#Mansfield #MansfieldOhio #RichlandCounty #GoodNews"]
    return "\n".join(lines)


def ffmpeg_path():
    """System ffmpeg if there is one, else the static build that ships with the imageio-ffmpeg pip package."""
    if shutil.which("ffmpeg"):
        return shutil.which("ffmpeg")
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def main():
    site = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "site")
    sample = "--sample" in sys.argv
    global FFMPEG
    FFMPEG = ffmpeg_path()
    if not FFMPEG:
        print("Reel: ffmpeg not installed (pip install imageio-ffmpeg), skipped")
        return
    try:
        stories = sample_stories() if sample else live_stories()
    except Exception as ex:
        print(f"Reel: couldn't read stories ({ex}), skipped")
        return
    if len(stories) < 2:
        print(f"Reel: only {len(stories)} story with a photo this week, skipped")
        return
    stories = stories[:SLIDES]
    try:
        render(stories, site / "reel")
    except Exception as ex:
        print(f"Reel: couldn't make the video ({ex}), skipped")
        return
    (site / "reel" / "caption.txt").write_text(caption(stories))
    (site / "reel" / "stories.json").write_text(json.dumps(stories, indent=1))
    print(f"Reel: made a {15}-second reel of {len(stories)} stories -> {site / 'reel' / 'this-week.mp4'}")


if __name__ == "__main__":
    main()
