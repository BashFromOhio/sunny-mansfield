"""Make the "Featured on Sunny Mansfield" link badges in brand/ (run: python3 tools/make_badges.py).

Two badges, each 600x200 (shown at 300x100 so they stay sharp on phones):
  badge-featured.png  for businesses and causes we spotlight ("Featured on")
  badge-friend.png    for any local site that wants to point readers to good news ("Find good news on")
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BRAND = Path(__file__).resolve().parent.parent / "brand"
FONTS = BRAND / "fonts"
INK, ORANGE, CREAM, SURFACE = (46, 26, 16), (217, 83, 30), (251, 246, 236), (243, 230, 207)


def badge(kicker, out):
    w, h = 600, 200
    im = Image.new("RGB", (w, h), CREAM)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, w - 1, h - 1], outline=INK, width=6)
    for x in range(-h, w, 28):  # thin candy-stripe trim along the bottom, the brand's one decoration
        d.polygon([(x, h - 6), (x + 14, h - 6), (x + 26, h - 20), (x + 12, h - 20)], fill=ORANGE)
    d.rectangle([6, h - 26, w - 7, h - 21], fill=INK)
    mark = Image.open(BRAND / "icon-512.png").convert("RGBA").resize((128, 128), Image.LANCZOS)
    im.paste(mark, (26, 26), mark)
    small = ImageFont.truetype(str(FONTS / "Josefin-700.ttf"), 26)
    script = ImageFont.truetype(str(FONTS / "Yellowtail.ttf"), 64)
    tail = ImageFont.truetype(str(FONTS / "Josefin-700.ttf"), 22)
    d.text((172, 30), kicker.upper(), font=small, fill=ORANGE)
    d.text((168, 58), "Sunny Mansfield", font=script, fill=INK)
    d.text((174, 136), "SUNNYMANSFIELD.COM", font=tail, fill=INK)
    im.save(BRAND / out, optimize=True)


if __name__ == "__main__":
    badge("Featured on", "badge-featured.png")
    badge("Find good news on", "badge-friend.png")
    print("Wrote brand/badge-featured.png and brand/badge-friend.png")
