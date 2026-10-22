#!/usr/bin/env python3
"""Build the Sunny Mansfield social media kit: HTML pages for every image, rendered to PNG by render_social.js.

  python3 tools/make_social.py <fonts dir> <out dir>
  NODE_PATH=$(npm root -g) node tools/render_social.js <out dir>
"""
import html
import io
import json
import re
import sys
from pathlib import Path

import qrcode
import qrcode.image.svg

HERE = Path(__file__).resolve().parent.parent
FONTS, OUT = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve()
SITE = "SunnyMansfield.com"
QR_URL = "https://bashfromohio.github.io/sunny-mansfield/"  # switch to https://sunnymansfield.com/ once the domain is live

INK, ORANGE, CREAM, SURFACE = "#2E1A10", "#D9531E", "#FBF6EC", "#F3E6CF"


def svg(name):
    text = (HERE / "brand" / name).read_text()
    return re.sub(r"<metadata>.*?</metadata>", "", text, flags=re.S)


def uri(path):
    return (HERE / path).as_uri()


ART = {
    "good": '<circle cx="50" cy="50" r="16"/><path d="M50 18v-8M50 90v-8M18 50h-8M90 50h-8M27 27l-6-6M79 79l-6-6M27 73l-6 6M79 21l-6 6"/>',
    "events": '<rect x="16" y="22" width="68" height="62"/><path d="M16 40h68M32 14v16M68 14v16M30 56h10M45 56h10M60 56h10M30 70h10M45 70h10"/>',
    "sales": '<path d="M52 12h34v34L46 86 12 52z"/><circle cx="72" cy="26" r="5"/>',
    "biz": '<path d="M12 86h76M18 86V44h64v42M12 44l8-24h60l8 24M12 44c0 6 6 10 12 10s12-4 12-10c0 6 6 10 14 10s14-4 14-10c0 6 6 10 12 10s12-4 12-10M42 86V64h16v22"/>',
    "submit": '<path d="M14 50l72-34-22 70-14-28z"/><path d="M50 58l36-42"/>',
    "about": '<circle cx="50" cy="50" r="36"/><path d="M50 46v24M50 32v2"/>',
    "life": '<path d="M50 88V48M50 48c-10-2-20-12-20-26 12 0 20 10 20 26zM50 48c10-2 20-12 20-26-12 0-20 10-20 26zM50 66c-8 0-16-6-18-16 10 0 16 6 18 16zM50 66c8 0 16-6 18-16-10 0-16 6-18 16z"/>',
    "heart": '<path d="M50 84S16 64 16 38c0-12 9-20 19-20 7 0 12 4 15 9 3-5 8-9 15-9 10 0 19 8 19 20 0 26-34 46-34 46z"/>',
}


def icon(k, color=CREAM, width=4):
    return (f'<svg viewBox="0 0 100 100" fill="none" stroke="{color}" stroke-width="{width}" stroke-linecap="round" '
            f'stroke-linejoin="round" aria-hidden="true">{ART[k]}</svg>')


BASE_CSS = f"""
@font-face{{font-family:Yellowtail;src:url({(FONTS / 'Yellowtail.ttf').as_uri()})}}
@font-face{{font-family:Josefin;font-weight:700;src:url({(FONTS / 'Josefin-700.ttf').as_uri()})}}
@font-face{{font-family:Caslon;font-weight:700;src:url({(FONTS / 'Caslon-700.ttf').as_uri()})}}
*{{box-sizing:border-box;margin:0;padding:0}}
html,body{{width:var(--w);height:var(--h);overflow:hidden}}
body{{font-family:"Libre Franklin",Helvetica,sans-serif;color:{INK};background:{CREAM};position:relative}}
.caps{{font-family:Josefin;font-weight:700;text-transform:uppercase;letter-spacing:.12em}}
.script{{font-family:Yellowtail;color:{ORANGE}}}
.serif{{font-family:Caslon;font-weight:700;letter-spacing:-.01em}}
.retro{{font-family:Josefin;font-weight:700;text-transform:uppercase;color:{ORANGE};letter-spacing:.04em;line-height:1;
  text-shadow:.03em .03em 0 {CREAM},.07em .07em 0 {INK}}}
.collage{{position:absolute;inset:0;background:linear-gradient(rgba(243,230,207,.78),rgba(243,230,207,.78)),url({uri('assets/collage.jpg')}) center/cover}}
.stripes{{position:absolute;left:0;right:0;height:var(--s,18px);background:repeating-linear-gradient(-55deg,{ORANGE} 0 calc(var(--s,18px)*.64),{CREAM} calc(var(--s,18px)*.64) calc(var(--s,18px)*1.28))}}
.candy{{position:absolute;inset:0;background:repeating-linear-gradient(-55deg,{ORANGE} 0 18px,{CREAM} 18px 36px)}}
.photo{{background-size:cover;background-position:center;filter:sepia(.15)}}
.logo svg{{width:100%;height:auto;display:block}}
"""

FONT_LINK = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Libre+Franklin:wght@400;500;600;700'
             '&display=swap">')

ASSETS = []  # (folder, file, width, height, html body, transparent)


def add(folder, name, w, h, body, css="", transparent=False, note=""):
    page = (f'<!doctype html><html><head><meta charset="utf-8">{FONT_LINK}<style>:root{{--w:{w}px;--h:{h}px}}{BASE_CSS}{css}'
            f'{"html,body{background:transparent!important}" if transparent else ""}</style></head><body>{body}</body></html>')
    path = OUT / "html" / folder / f"{name}.html"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(page)
    ASSETS.append({"folder": folder, "name": name, "w": w, "h": h, "html": str(path), "transparent": transparent, "note": note})


# ---------- profile pictures ----------
def avatar(size, bg):
    inner = (f'<div style="position:absolute;inset:0;background:{bg}"></div>'
             f'<div style="position:absolute;left:50%;top:50%;width:{int(size*.74)}px;height:{int(size*.74)}px;transform:translate(-50%,-50%);'
             f'border-radius:50%;background:{CREAM};border:{max(4,size//90)}px solid {INK};display:grid;place-items:center">'
             f'<div class="logo" style="width:{int(size*.42)}px;margin-top:-{int(size*.02)}px">{svg("mark.svg")}</div></div>')
    return inner


for folder, name, size, bg, note in [
    ("01-profile-pictures", "profile-orange-1080", 1080, ORANGE, "Main profile picture for every platform. Shows as a circle."),
    ("01-profile-pictures", "profile-cream-1080", 1080, SURFACE, "Alternate profile picture on cream."),
    ("01-profile-pictures", "profile-orange-400", 400, ORANGE, "Small version for X and LinkedIn."),
]:
    add(folder, name, size, size, avatar(size, bg), note=note)

# ---------- cover banners ----------
def cover(w, h, logo_w, tag_size, stripe, offset_x=0, tag=True, extra=""):
    tagline = (f'<div class="caps" style="font-size:{tag_size}px;margin-top:{int(tag_size*1.4)}px;color:{INK}">'
               f'Good news for all seasons &nbsp;·&nbsp; Mansfield &amp; Richland County, Ohio</div>') if tag else ""
    return (f'<div class="collage"></div><div class="stripes" style="--s:{stripe}px;top:0"></div>'
            f'<div class="stripes" style="--s:{stripe}px;bottom:0"></div>'
            f'<div style="position:absolute;inset:0;display:grid;place-items:center;transform:translateX({offset_x}px)"><div style="text-align:center">'
            f'<div class="logo" style="width:{logo_w}px;margin:0 auto">{svg("logo-retro.svg")}</div>{tagline}{extra}</div></div>')


add("02-facebook", "facebook-cover-1640x624", 1640, 624, cover(1640, 624, 760, 22, 20),
    note="Facebook Page cover. Logo stays inside the area phones show.")
add("02-facebook", "facebook-group-cover-1640x856", 1640, 856, cover(1640, 856, 820, 24, 22,
    extra=f'<div class="script" style="font-size:54px;margin-top:26px">Neighbors sharing good news</div>'),
    note="Cover for a Sunny Mansfield Facebook Group.")
add("02-facebook", "facebook-event-cover-1920x1005", 1920, 1005, cover(1920, 1005, 900, 26, 24,
    extra=f'<div class="retro" style="font-size:96px;margin-top:40px">Event title here</div>'),
    note="Event cover example. Change the title in Canva, or ask Claude to make one per event.")
add("03-x-twitter", "x-header-1500x500", 1500, 500, cover(1500, 500, 640, 19, 16, offset_x=60),
    note="X header. Nudged right so the profile picture doesn't cover it.")
add("04-linkedin", "linkedin-page-cover-1128x191", 1128, 191, cover(1128, 191, 360, 0, 8, offset_x=160, tag=False),
    note="LinkedIn Company Page cover.")
add("04-linkedin", "linkedin-personal-banner-1584x396", 1584, 396, cover(1584, 396, 560, 17, 14, offset_x=200),
    note="Banner for your personal LinkedIn profile.")
add("07-email-and-web", "newsletter-header-1200x400", 1200, 400, cover(1200, 400, 560, 17, 14,
    extra=f'<div class="script" style="font-size:40px;margin-top:10px">The Sunny Weekend</div>'),
    note="Top of the weekly email newsletter.")
add("07-email-and-web", "link-preview-1200x630", 1200, 630, cover(1200, 630, 620, 18, 16,
    extra=f'<div class="caps" style="font-size:22px;margin-top:22px;color:{ORANGE}">{SITE}</div>'),
    note="Image that appears when the site link is shared.")

# email signature
add("07-email-and-web", "email-signature-600x150", 600, 150, f"""
<div style="position:absolute;inset:0;background:{CREAM}"></div><div class="stripes" style="--s:8px;top:0"></div>
<div style="position:absolute;left:24px;top:28px;width:96px;height:96px;border-radius:50%;background:{ORANGE};display:grid;place-items:center">
<div style="width:70px;height:70px;border-radius:50%;background:{CREAM};display:grid;place-items:center"><div class="logo" style="width:38px">{svg("mark.svg")}</div></div></div>
<div style="position:absolute;left:140px;top:30px;right:16px"><div class="logo" style="width:250px">{svg("logo-retro.svg")}</div>
<div class="caps" style="font-size:11px;margin-top:14px">Good news for all seasons · {SITE}</div></div>""",
    note="Put under your email signature, linked to the site.")

# ---------- post templates (4:5, 1080x1350) ----------
def photo_box(src, x, y, w, h, border=8):
    if src is None:  # transparent hole for your own photo
        return f'<div style="position:absolute;left:{x}px;top:{y}px;width:{w}px;height:{h}px;outline:{border}px solid {INK}"></div>'
    return (f'<div class="photo" style="position:absolute;left:{x}px;top:{y}px;width:{w}px;height:{h}px;'
            f'background-image:url({uri(src)});outline:{border}px solid {INK}"></div>')


def frame_bg(hole=None):
    """Cream page with stripes. hole = (x, y, w, h) left see-through so a photo can sit underneath."""
    if not hole:
        return (f'<div style="position:absolute;inset:0;background:{CREAM}"></div>'
                f'<div class="stripes" style="--s:24px;top:0"></div><div class="stripes" style="--s:24px;bottom:0"></div>')
    x, y, w, h = hole
    mask = (f"linear-gradient(#000,#000),linear-gradient(#000,#000)")
    return (f'<div style="position:absolute;inset:0;background:{CREAM};-webkit-mask:{mask};-webkit-mask-composite:xor;mask-composite:exclude;'
            f'-webkit-mask-size:100% 100%,{w}px {h}px;-webkit-mask-position:0 0,{x}px {y}px;-webkit-mask-repeat:no-repeat"></div>'
            f'<div class="stripes" style="--s:24px;top:0"></div><div class="stripes" style="--s:24px;bottom:0"></div>')


def footer_bar(w, h):
    return (f'<div style="position:absolute;left:60px;right:60px;bottom:60px;display:flex;align-items:center;justify-content:space-between">'
            f'<div class="logo" style="width:290px">{svg("logo-retro.svg")}</div>'
            f'<div class="caps" style="font-size:20px;text-align:right">Read more at<br><span style="color:{ORANGE}">{SITE}</span></div></div>')


def good_news(photo, kicker, headline, hole=False):
    box = (60, 84, 960, 760)
    pic = photo_box(None if hole else photo, *box)
    return (frame_bg(box if hole else None) + pic +
            f'<div class="caps" style="position:absolute;left:60px;top:892px;font-size:24px;color:{ORANGE}">{kicker}</div>'
            f'<div class="serif" style="position:absolute;left:60px;right:60px;top:936px;font-size:58px;line-height:1.08">{headline}</div>'
            + footer_bar(1080, 1350))


add("05-post-templates", "good-news-example", 1080, 1350,
    good_news("assets/sample/4.jpg", "Good News", "Downtown bakery celebrates grand opening on North Main"),
    note="Everyday story post. Swap the photo and headline.")
add("05-post-templates", "good-news-frame-blank", 1080, 1350,
    good_news(None, "Good News", "Your headline goes here", hole=True), transparent=True,
    note="See-through frame: lay it over your photo in Canva, then edit the headline.")


def candy_post(script, title, body, extra="", icon_key=None):
    ic = (f'<div style="width:120px;height:120px;margin:0 auto 26px">{icon(icon_key, ORANGE, 5)}</div>' if icon_key else "")
    return (f'<div class="candy"></div><div style="position:absolute;inset:42px;background:{CREAM};border:6px solid {ORANGE};'
            f'display:grid;place-items:center;text-align:center;padding:70px"><div>{ic}'
            f'<div class="script" style="font-size:92px;line-height:1">{script}</div>'
            f'<div class="retro" style="font-size:118px;margin-top:18px">{title}</div>'
            f'<div style="font-size:36px;line-height:1.45;margin-top:40px;color:#4a3426">{body}</div>{extra}'
            f'<div class="logo" style="width:330px;margin:56px auto 0">{svg("logo-retro.svg")}</div></div></div>')


add("05-post-templates", "launch-announcement", 1080, 1350,
    candy_post("Greetings from", "Mansfield", "Our town's good news, all in one place. Neighbors, new shops, students, "
               "events and lives worth celebrating.",
               f'<div class="caps" style="font-size:28px;margin-top:34px;color:{ORANGE}">Read it free at {SITE}</div>'),
    note="First post on every platform.")
add("05-post-templates", "got-good-news", 1080, 1350,
    candy_post("Hey neighbor!", "Got good news?", "Ribbon cuttings, 100th birthdays, team wins, garage sales, "
               "kids doing great things. Send it in and we'll share it.",
               f'<div class="caps" style="font-size:28px;margin-top:34px;color:{ORANGE}">{SITE} · Submit news</div>'),
    note="Ask people to send stories. Post every week or two.")
add("05-post-templates", "shop-local-spotlight", 1080, 1350,
    frame_bg() + photo_box("assets/sample/3.jpg", 60, 230, 960, 640) +
    f'<div style="position:absolute;left:0;right:0;top:66px;text-align:center"><div class="script" style="font-size:78px;line-height:1">Shop local!</div>'
    f'<div class="retro" style="font-size:76px;margin-top:6px">Business spotlight</div></div>'
    f'<div class="serif" style="position:absolute;left:60px;right:60px;top:912px;font-size:56px;line-height:1.08">Family hardware store celebrates 75 years</div>'
    f'<div class="caps" style="position:absolute;left:60px;top:1060px;font-size:22px;color:{ORANGE}">Park Avenue · Mansfield</div>' + footer_bar(1080, 1350),
    note="Business spotlight. Tag the business in the caption.")
add("05-post-templates", "event-this-weekend", 1080, 1350,
    f'<div class="collage"></div><div class="stripes" style="--s:24px;top:0"></div><div class="stripes" style="--s:24px;bottom:0"></div>'
    f'<div style="position:absolute;left:0;right:0;top:70px;text-align:center"><div class="script" style="font-size:80px;line-height:1">This weekend in</div>'
    f'<div class="retro" style="font-size:112px">Mansfield</div></div>'
    + photo_box("assets/sample/sign.jpg", 190, 330, 700, 760) +
    f'<div style="position:absolute;left:0;right:0;top:1140px;text-align:center"><div class="caps" style="font-size:30px">Kingwood Mum Festival · Sat, Oct 31</div>'
    f'<div class="caps" style="font-size:22px;margin-top:14px;color:{ORANGE}">More events at {SITE}</div></div>',
    note="Share an event flyer. Swap in the flyer image and details.")
add("05-post-templates", "five-good-things", 1080, 1350,
    frame_bg() + f'<div style="position:absolute;left:70px;right:70px;top:80px"><div class="script" style="font-size:80px;line-height:1">This week in Mansfield</div>'
    f'<div class="retro" style="font-size:104px;margin-top:8px">5 good things</div><ol style="list-style:none;margin-top:48px">'
    + "".join(f'<li style="display:grid;grid-template-columns:76px 1fr;gap:18px;padding:22px 0;border-bottom:3px solid #E6D6BD">'
              f'<span class="serif" style="font-size:60px;color:{ORANGE};line-height:1">{i}</span>'
              f'<span class="serif" style="font-size:40px;line-height:1.18">{t}</span></li>'
              for i, t in enumerate(["Ocie Hill kids paint a sunrise mural", "Madison band raises $3,000 at a car wash",
                                     "Hardware store on Park Ave turns 75", "Senior center starts free tech help",
                                     "Volunteers plant 200 shade trees"], 1))
    + "</ol></div>" + footer_bar(1080, 1350),
    note="Weekly roundup for Friday or Sunday.")
add("05-post-templates", "celebration-of-life", 1080, 1350,
    f'<div style="position:absolute;inset:0;background:{CREAM}"></div><div style="position:absolute;inset:40px;border:3px solid {INK}"></div>'
    f'<div style="position:absolute;inset:52px;border:1px solid {INK}"></div>'
    f'<div style="position:absolute;left:0;right:0;top:140px;text-align:center"><div style="width:110px;height:110px;margin:0 auto">{icon("life", INK, 4)}</div>'
    f'<div class="caps" style="font-size:26px;margin-top:30px;color:#6F5443">Celebrating the life of</div>'
    f'<div class="serif" style="font-size:96px;margin-top:22px;line-height:1.05">Full Name</div>'
    f'<div class="caps" style="font-size:30px;margin-top:24px">1941 – 2026</div>'
    + photo_box("assets/sample/9.jpg", 330, 0, 420, 420, 4).replace("position:absolute;left:330px;top:0px", "position:relative;margin:50px auto 0") +
    f'<div style="font-size:32px;line-height:1.5;margin:46px auto 0;max-width:760px;color:#4a3426">A few words about who they were and what they loved. Service details below.</div>'
    f'<div class="logo" style="width:260px;margin:50px auto 0">{svg("logo-retro.svg")}</div></div>',
    note="Gentle style with no stripes or bright colors. Ask the family before posting.")
add("05-post-templates", "one-share", 1080, 1350,
    f'<div style="position:absolute;inset:0;background:{ORANGE}"></div><div class="stripes" style="--s:24px;top:0"></div><div class="stripes" style="--s:24px;bottom:0"></div>'
    f'<div style="position:absolute;inset:0;display:grid;place-items:center;text-align:center;padding:110px;color:{CREAM}"><div>'
    f'<div style="width:130px;height:130px;margin:0 auto 30px">{icon("heart", CREAM, 5)}</div>'
    f'<div class="serif" style="font-size:92px;line-height:1.08">One share can make someone\'s day.</div>'
    f'<div style="font-size:36px;line-height:1.45;margin-top:40px">Every story on Sunny Mansfield has a Share button. Pass the good news along.</div>'
    f'<div class="caps" style="font-size:28px;margin-top:50px">{SITE}</div></div></div>',
    note="Quote card that asks people to share.")
add("05-post-templates", "x-launch-1600x900", 1600, 900,
    cover(1600, 900, 820, 22, 20, extra=f'<div class="script" style="font-size:62px;margin-top:30px">Our town\'s good news is here</div>'
          f'<div class="caps" style="font-size:26px;margin-top:20px;color:{ORANGE}">Read it free at {SITE}</div>'),
    note="Landscape launch post for X and LinkedIn.")

# ---------- stories (1080x1920) ----------
add("06-instagram-stories", "story-got-good-news", 1080, 1920,
    candy_post("Hey neighbor!", "Got good news?", "Tap the link to send in a story, an event flyer or something for sale.",
               f'<div style="margin:60px auto 0;width:520px;padding:26px;background:{INK};color:{CREAM}" class="caps"><span style="font-size:30px">Link sticker here</span></div>'),
    note="Story asking for submissions. Put a link sticker on the dark box.")
add("06-instagram-stories", "story-new-post-frame", 1080, 1920,
    frame_bg((90, 300, 900, 1125)) + f'<div style="position:absolute;left:90px;top:300px;width:900px;height:1125px;outline:8px solid {INK}"></div>'
    f'<div style="position:absolute;left:0;right:0;top:110px;text-align:center"><div class="script" style="font-size:92px;line-height:1">New good news!</div></div>'
    f'<div style="position:absolute;left:90px;right:90px;top:1490px;text-align:center"><div class="serif" style="font-size:62px;line-height:1.1">Headline goes here</div>'
    f'<div class="caps" style="font-size:26px;margin-top:30px;color:{ORANGE}">Tap the link to read</div></div>'
    f'<div class="logo" style="position:absolute;left:50%;bottom:90px;width:340px;transform:translateX(-50%)">{svg("logo-retro.svg")}</div>',
    transparent=True, note="See-through story frame. Put your photo under it, then add a link sticker.")
for key, label in [("good", "Good news"), ("events", "Events"), ("sales", "Buy & Sell"), ("biz", "Shop local"),
                   ("submit", "Submit"), ("about", "About")]:
    add("06-instagram-stories", f"highlight-{key}", 1080, 1920,
        f'<div style="position:absolute;inset:0;background:{ORANGE}"></div>'
        f'<div style="position:absolute;left:50%;top:50%;width:620px;height:620px;transform:translate(-50%,-50%);border-radius:50%;background:{CREAM};display:grid;place-items:center">'
        f'<div style="width:300px;height:300px">{icon(key, ORANGE, 6)}</div></div>',
        note=f'Highlight cover: "{label}".')

# ---------- print ----------
qr = qrcode.make(QR_URL, image_factory=qrcode.image.svg.SvgPathImage, box_size=20, border=1)
buf = io.BytesIO()
qr.save(buf)
qr_svg = buf.getvalue().decode()
qr_svg = re.sub(r"<\?xml[^>]*>", "", qr_svg)
add("08-print", "qr-flyer-letter", 1275, 1650,
    f'<div class="candy"></div><div style="position:absolute;inset:50px;background:{CREAM};border:6px solid {ORANGE};text-align:center;padding:70px 80px">'
    f'<div class="logo" style="width:760px;margin:0 auto">{svg("logo-retro.svg")}</div>'
    f'<div class="caps" style="font-size:26px;margin-top:30px">Good news for all seasons · Mansfield &amp; Richland County</div>'
    f'<div class="serif" style="font-size:74px;line-height:1.1;margin-top:60px">Scan for good news from your neighbors</div>'
    f'<div style="width:520px;height:520px;margin:50px auto 0;padding:24px;background:#fff;border:6px solid {INK}">{qr_svg}</div>'
    f'<div style="font-size:32px;line-height:1.45;margin-top:44px;color:#4a3426">Local stories, events, shops and Buy &amp; Sell. Free, and updated all day.</div></div>',
    css="svg{width:100%;height:100%;display:block}.logo svg{height:auto}",
    note="Print on letter paper for shop windows and bulletin boards. QR opens the site.")

(OUT / "assets.json").write_text(json.dumps(ASSETS, indent=1))
print(f"{len(ASSETS)} assets")
