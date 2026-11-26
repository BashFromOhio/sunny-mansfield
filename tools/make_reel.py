"""This week's Facebook Reel: a vertical video (1080x1920, about 29 seconds) of the week's 7 most-read neighbor stories, made from
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
W, H = 1080, 1920
CREAM, BROWN, ORANGE, GOLD, WHITE = (251, 246, 236), (46, 26, 16), (232, 115, 42), (246, 190, 60), (255, 255, 255)
STORY_SECTIONS = {"Good News", "Sports", "Neighborhoods", "Local Business", "Student Voices", "Seniors",
                  "Athlete Spotlight", "Business Spotlight"}
SLIDES = 7                     # top stories in the reel (fewer if the week had fewer)
INTRO, OUTRO, FADE = 2.6, 3.0, 0.6
STORY = 3.8                    # seconds per story: time to read each headline; 7 stories make a reel of about 29 seconds
FFMPEG = "ffmpeg"
UA = "Mozilla/5.0 (sunnymansfield.com reel maker)"


def font(name, size):
    return ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)


def headline(t):
    """Same headline style as the site (build.headline_style), so the Reel and the stories match."""
    try:
        import warnings
        warnings.simplefilter("ignore", SyntaxWarning)   # build.py's inline JavaScript trips harmless escape warnings
        sys.path.insert(0, str(HERE))
        from build import headline_style
        return headline_style(t)
    except Exception:
        pass
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


LOGO = HERE / "brand" / "post-logo.png"
MUSIC = HERE / "brand" / "reel-music.m4a"   # optional soundtrack ("Run Up" from 0:47, 40 s, faded in); delete the file for a silent Reel
SAFE_TOP, SAFE_BOTTOM = 230, 1520   # Facebook covers the top bar and the bottom caption area of a Reel


def blurred_bg(photo):
    bg = cover(photo, W // 4, H // 4).filter(ImageFilter.GaussianBlur(10)).resize((W, H), Image.LANCZOS)
    return Image.blend(bg, Image.new("RGB", (W, H), (20, 12, 6)), 0.55)


def progress(d, n, total):
    """Story-style progress segments across the top, like Instagram and Facebook stories."""
    gap, x0, x1, y = 10, 60, W - 60, 150
    seg = (x1 - x0 - gap * (total - 1)) / total
    for k in range(total):
        a = x0 + k * (seg + gap)
        d.rounded_rectangle([a, y, a + seg, y + 8], radius=4, fill=CREAM if k < n else (255, 255, 255, 90))


def brand_tag(img):
    d = ImageDraw.Draw(img)
    d.text((60, 205), "SUNNY MANSFIELD", font=font("Josefin-700", 30), fill=CREAM, anchor="lm")
    d.text((W - 60, 205), "GOOD NEWS", font=font("Josefin-700", 30), fill=GOLD, anchor="rm")


def story_layers(photo, title, kicker, n, total):
    """(background with frame, the photo for the push-in, text layer with alpha) for one story."""
    bg = blurred_bg(photo)
    d = ImageDraw.Draw(bg, "RGBA")
    progress(d, n, total)
    brand_tag(bg)
    fx, fy, fw, fh = 60, 270, W - 120, 1000
    d.rounded_rectangle([fx - 8, fy - 8, fx + fw + 8, fy + fh + 8], radius=26, fill=CREAM)
    fg = cover(photo, fw, fh)
    text = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    t = ImageDraw.Draw(text)
    kf, hf = font("Josefin-700", 32), font("Caslon-700", 74)
    lines = wrap(t, title, hf, W - 140, 3)
    y = fy + fh + 70
    kw = t.textlength(kicker.upper(), font=kf)
    t.rounded_rectangle([70, y, 70 + kw + 44, y + 58], radius=10, fill=ORANGE + (255,))
    t.text((92, y + 30), kicker.upper(), font=kf, fill=WHITE, anchor="lm")
    for k, line in enumerate(lines):
        t.text((70, y + 90 + k * 86), line, font=hf, fill=WHITE)
    return bg, fg, (fx, fy, fw, fh), text


def intro_layers(span):
    bg = gradient(W, H, (244, 140, 56), (190, 66, 20))
    d = ImageDraw.Draw(bg)
    import math
    cx, cy = W / 2, 1180
    for k in range(18):
        a = math.pi * (k / 17)
        d.line([(cx, cy), (cx - math.cos(a) * 1400, cy - math.sin(a) * 1400)], fill=(248, 158, 70), width=26)
    sun = Image.new("RGBA", (420, 420), (0, 0, 0, 0))
    ImageDraw.Draw(sun).ellipse([0, 0, 419, 419], fill=GOLD + (255,))
    text = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    t = ImageDraw.Draw(text)
    t.text((W / 2, 470), "This week in", font=font("Yellowtail", 120), fill=CREAM, anchor="mm")
    t.text((W / 2, 630), "MANSFIELD", font=font("Caslon-700", 150), fill=WHITE, anchor="mm", stroke_width=6, stroke_fill=BROWN)
    t.text((W / 2, 770), span.upper(), font=font("Josefin-700", 40), fill=CREAM, anchor="mm")
    t.text((W / 2, 840), "TOP GOOD-NEWS STORIES", font=font("Josefin-700", 40), fill=CREAM, anchor="mm")
    return bg, sun, text


def outro_layers():
    bg = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(bg)
    d.rectangle([0, 0, W, 36], fill=ORANGE)
    d.rectangle([0, H - 36, W, H], fill=ORANGE)
    text = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    t = ImageDraw.Draw(text)
    t.text((W / 2, 640), "Read every story", font=font("Yellowtail", 120), fill=ORANGE + (255,), anchor="mm")
    t.text((W / 2, 790), "SUNNYMANSFIELD.COM", font=font("Caslon-700", 68), fill=BROWN + (255,), anchor="mm")
    t.rounded_rectangle([W / 2 - 330, 900, W / 2 + 330, 1000], radius=50, fill=ORANGE + (255,))
    t.text((W / 2, 950), "LINK IN THE POST", font=font("Josefin-700", 38), fill=WHITE + (255,), anchor="mm")
    t.text((W / 2, 1120), "Know a neighbor doing great things?", font=font("Josefin-700", 40), fill=BROWN + (255,), anchor="mm")
    t.text((W / 2, 1180), "Tell us and we'll share it.", font=font("Josefin-700", 40), fill=BROWN + (255,), anchor="mm")
    return bg, text


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
             "Neighbors fill the food pantry shelves before Thanksgiving", "A new bakery opens on Main Street",
             "Madison band raises $3,000 for new uniforms", "Seniors teach a quilting class at the library",
             "Volunteers plant 40 trees in North Lake Park"]
    kinds = ["Neighborhoods", "Sports", "Good News", "Local Business", "Student Voices", "Seniors", "Good News"]
    return [{"title": t, "kicker": k, "photo": str(HERE / "assets" / "sample" / f"{i + 1}.jpg"), "views": 0, "url": ""}
            for i, (t, k) in enumerate(zip(names, kinds))]


def render(stories, out):
    """Layers per slide (background, moving photo or sun, text that fades and slides in), joined with smooth transitions."""
    tmp = Path(tempfile.mkdtemp())
    fps, args, flt, slides = 30, [FFMPEG, "-y", "-loglevel", "error"], [], []
    now = datetime.now(timezone.utc)
    span = f"{(now - timedelta(days=6)):%b %-d} – {now:%b %-d}"
    n_in = 0

    def add(img, dur, name):
        nonlocal n_in
        p = tmp / f"{name}.png"
        img.save(p)
        args.extend(["-loop", "1", "-t", f"{dur:.3f}", "-i", str(p)])
        n_in += 1
        return n_in - 1

    def text_in(base, txt_idx, dur, k, delay=0.25):
        """Text fades in and rises 40px over half a second."""
        flt.append(f"[{txt_idx}:v]format=rgba,fade=in:st={delay}:d=0.8:alpha=1[t{k}]")
        flt.append(f"[{base}][t{k}]overlay=x=0:y='40*max(0,1-(t-{delay})/0.8)':shortest=1[s{k}]")
        return f"s{k}"

    # intro: the sun rises behind the title
    bg, sun, txt = intro_layers(span)
    i_bg, i_sun, i_txt = add(bg, INTRO, "i_bg"), add(sun, INTRO, "i_sun"), add(txt, INTRO, "i_txt")
    flt.append(f"[{i_bg}:v]fps={fps},format=yuv420p[ib]")
    flt.append(f"[{i_sun}:v]format=rgba[isun]")
    flt.append(f"[ib][isun]overlay=x=(W-w)/2:y='1500-530*min(1,t/1.8)':shortest=1[ibs]")
    slides.append((text_in("ibs", i_txt, INTRO, 0, 0.2), INTRO))
    # stories: soft blurred backdrop, framed photo with a slow push-in, headline sliding up
    for k, s in enumerate(stories, 1):
        raw = Path(s["photo"]).read_bytes() if not s["photo"].startswith("http") else get(s["photo"])
        photo = Image.open(io.BytesIO(raw))
        bg, fg, (fx, fy, fw, fh), txt = story_layers(photo, headline(s["title"]), s["kicker"], k, len(stories))
        b, f, t = add(bg, STORY, f"b{k}"), add(fg, STORY, f"f{k}"), add(txt, STORY, f"t{k}")
        frames = round(STORY * fps)
        flt.append(f"[{b}:v]fps={fps},format=yuv420p[b{k}]")
        if k == 1:   # one rounded-corner mask, shared by every photo
            m = Image.new("L", (fw, fh), 0)
            ImageDraw.Draw(m).rounded_rectangle([0, 0, fw - 1, fh - 1], radius=22, fill=255)
            mask_idx = add(m, STORY, "mask")
        flt.append(f"[{f}:v]scale={round(fw * 1.25)}:{round(fh * 1.25)},zoompan=z='1+0.05*on/{frames}':x='iw/2-(iw/zoom/2)'"
                   f":y='ih/3-(ih/zoom/3)':d={frames}:s={fw}x{fh}:fps={fps}[z{k}]")
        flt.append(f"[{mask_idx}:v]fps={fps},format=gray,split[m{k}][mm{k}];[mm{k}]nullsink")
        flt.append(f"[z{k}][m{k}]alphamerge[f{k}]")
        flt.append(f"[b{k}][f{k}]overlay=x={fx}:y={fy}:shortest=1[bf{k}]")
        slides.append((text_in(f"bf{k}", t, STORY, k), STORY))
    # outro
    bg, txt = outro_layers()
    o_bg, o_txt = add(bg, OUTRO, "o_bg"), add(txt, OUTRO, "o_txt")
    flt.append(f"[{o_bg}:v]fps={fps},format=yuv420p[ob]")
    slides.append((text_in("ob", o_txt, OUTRO, 99, 0.15), OUTRO))
    # join: fade into the first story, slide between stories, fade to the outro
    last, t = slides[0][0], slides[0][1]
    for k in range(1, len(slides)):
        t -= FADE
        kind = "slideleft" if 1 < k < len(slides) - 1 else "fade"
        flt.append(f"[{last}][{slides[k][0]}]xfade=transition={kind}:duration={FADE}:offset={t:.3f}[x{k}]")
        last, t = f"x{k}", t + slides[k][1]
    out.mkdir(parents=True, exist_ok=True)
    music = MUSIC if MUSIC.exists() else None
    if music:   # the Reel's soundtrack: a 15-second excerpt, already faded and loudness-matched for social
        args += ["-i", str(music)]
    args += ["-filter_complex", ";".join(flt), "-map", f"[{last}]"]
    if music:
        total = INTRO + OUTRO + STORY * len(stories) - FADE * (len(stories) + 1)
        args += ["-map", f"{n_in}:a", "-af", f"atrim=0:{total:.2f},afade=out:st={total - 1.5:.2f}:d=1.5",
                 "-c:a", "aac", "-b:a", "160k", "-shortest"]
    args += ["-map_chapters", "-1", "-map_metadata", "-1", "-r", str(fps), "-c:v", "libx264", "-preset", "veryfast",
             "-crf", "21", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out / "this-week.mp4")]
    subprocess.run(args, check=True)
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", f"{INTRO + 0.8:.2f}", "-i", str(out / "this-week.mp4"),
                    "-frames:v", "1", "-q:v", "3", str(out / "this-week.jpg")], check=True)
    shutil.rmtree(tmp, ignore_errors=True)


def caption(stories):
    lines = ["This week's good news from Mansfield ☀️", ""]
    lines += [f"{i}. {headline(s['title'])}" for i, s in enumerate(stories, 1)]
    lines += ["", "Read every story 👉 https://sunnymansfield.com/?utm_source=facebook&utm_medium=social&utm_campaign=weekly_reel",
              "", "Know a neighbor doing great things? Tell us!", "", "#Mansfield #MansfieldOhio #RichlandCounty #GoodNews"]
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
    secs = round(INTRO + OUTRO + STORY * len(stories) - FADE * (len(stories) + 1))
    probe = subprocess.run([FFMPEG, "-hide_banner", "-i", str(site / "reel" / "this-week.mp4")], capture_output=True, text=True).stderr
    sound = "with sound" if re.search(r"Stream #\S+: Audio", probe) else "NO SOUND"
    print(f"Reel: made a {secs}-second reel ({sound}) of {len(stories)} stories -> {site / 'reel' / 'this-week.mp4'}")


if __name__ == "__main__":
    main()
