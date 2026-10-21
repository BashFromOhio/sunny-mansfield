"""Generate the Sunny Mansfield logo files with all lettering converted to outlines."""
import sys
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.transformPen import TransformPen

FONTS = Path(sys.argv[1])
OUT = Path(sys.argv[2])
BROWN, ORANGE, CREAM = "#3b2014", "#d9531e", "#fbf3e4"


def num(v):
    return f"{v:.2f}".rstrip("0").rstrip(".")


class Face:
    def __init__(self, path):
        self.f = TTFont(path)
        self.gs = self.f.getGlyphSet()
        self.cmap = self.f.getBestCmap()
        self.upm = self.f["head"].unitsPerEm
        self.cap = getattr(self.f["OS/2"], "sCapHeight", 0.7 * self.upm) / self.upm
        self.kern = {}
        if "kern" in self.f:
            for t in self.f["kern"].kernTables:
                self.kern.update(getattr(t, "kernTable", {}) or {})

    def run(self, text, size, x, baseline, tracking=0.0):
        """Outline `text`; returns (path data, advance width without trailing tracking)."""
        s = size / self.upm
        pen = SVGPathPen(self.gs, ntos=num)
        cx, prev = x, None
        for ch in text:
            if ch == " ":
                cx += self.gs[self.cmap[32]].width * s + tracking * size
                prev = None
                continue
            g = self.cmap[ord(ch)]
            if prev:
                cx += self.kern.get((prev, g), 0) * s
            self.gs[g].draw(TransformPen(pen, (s, 0, 0, -s, cx, baseline)))
            cx += self.gs[g].width * s + tracking * size
            prev = g
        return pen.getCommands(), cx - x - tracking * size

    def width(self, text, size, tracking=0.0):
        return self.run(text, size, 0, 0, tracking)[1]


ULTRA = Face(FONTS / "Ultra.ttf")
JOSEFIN = Face(FONTS / "Josefin-700.ttf")


def mark(frame=BROWN, sun=ORANGE, window=CREAM, uid="m"):
    """Arched window (after the Mansfield welcome sign) with the sun rising at the end of Main Street.
    Drawn in a 100x100 space; the shape spans x 12-88, y 6-96."""
    import math
    rays = "".join(
        f'<line x1="{num(50 + 20 * math.cos(math.radians(a)))}" y1="{num(68 + 20 * math.sin(math.radians(a)))}" '
        f'x2="{num(50 + 30 * math.cos(math.radians(a)))}" y2="{num(68 + 30 * math.sin(math.radians(a)))}"/>'
        for a in (200, 235, 270, 305, 340))
    return f"""<clipPath id="win{uid}"><path d="M19 89V44a31 31 0 0 1 62 0v45z"/></clipPath>
<path d="M12 96V44a38 38 0 0 1 76 0v52z" fill="{frame}"/>
<g clip-path="url(#win{uid})"><rect x="0" y="0" width="100" height="68" fill="{window}"/>
<path d="M48.6 68h2.8l11 22h-24.8z" fill="{window}"/>
<path d="M35 68a15 15 0 0 1 30 0z" fill="{sun}"/>
<g stroke="{sun}" stroke-width="4.6" stroke-linecap="round">{rays}</g></g>"""


def wordmark(x0, top, main_size, ink=BROWN, accent=ORANGE):
    """SUNNY between two rules over MANSFIELD. Returns (svg, width, height)."""
    main_w = ULTRA.width("MANSFIELD", main_size, 0.02)
    main_cap = ULTRA.cap * main_size
    sub_size = main_size * 0.31
    sub_cap = JOSEFIN.cap * sub_size
    sub_track = 0.42
    sub_w = JOSEFIN.width("SUNNY", sub_size, sub_track)
    gap = main_size * 0.15
    sub_base = top + sub_cap
    main_base = sub_base + gap + main_cap
    sub_d, _ = JOSEFIN.run("SUNNY", sub_size, x0 + (main_w - sub_w) / 2, sub_base, sub_track)
    main_d, _ = ULTRA.run("MANSFIELD", main_size, x0, main_base, 0.02)
    pad = main_size * 0.16
    rule_y = sub_base - sub_cap / 2
    rule_w = main_size * 0.028
    left_end = x0 + (main_w - sub_w) / 2 - pad
    right_start = x0 + (main_w + sub_w) / 2 + pad
    svg = (f'<path d="{sub_d}" fill="{accent}"/>'
           f'<path d="M{num(x0)} {num(rule_y)}H{num(left_end)}M{num(right_start)} {num(rule_y)}H{num(x0 + main_w)}" '
           f'stroke="{accent}" stroke-width="{num(rule_w)}"/>'
           f'<path d="{main_d}" fill="{ink}"/>')
    return svg, main_w, main_base - top


def horizontal(ink=BROWN, accent=ORANGE, frame=BROWN, window=CREAM, uid="h"):
    main_size = 100
    _, w, h = wordmark(0, 0, main_size)
    scale = h / 90  # mark height matches the lettering block
    mark_w = 76 * scale
    gap = h * 0.24
    words, w, h = wordmark(mark_w + gap, 0, main_size, ink, accent)
    total_w = mark_w + gap + w
    m = f'<g transform="translate({num(-12 * scale)} {num(-6 * scale)}) scale({num(scale)})">{mark(frame, accent, window, uid)}</g>'
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 {num(total_w + 4)} {num(h + 4)}" role="img" '
            f'aria-label="Sunny Mansfield">{m}{words}</svg>')


def stacked(ink=BROWN, accent=ORANGE, frame=BROWN, window=CREAM, tagline="GOOD NEWS FOR ALL SEASONS", uid="s"):
    main_size = 100
    _, w, h = wordmark(0, 0, main_size)
    mark_h = h * 1.15
    scale = mark_h / 90
    mark_w = 76 * scale
    top = mark_h + h * 0.22
    words, w, h2 = wordmark(0, top, main_size, ink, accent)
    tag_size = main_size * 0.155
    tag_track = 0.28
    tag_w = JOSEFIN.width(tagline, tag_size, tag_track)
    tag_base = top + h2 + main_size * 0.42
    tag_d, _ = JOSEFIN.run(tagline, tag_size, (w - tag_w) / 2, tag_base, tag_track)
    m = (f'<g transform="translate({num((w - mark_w) / 2 - 12 * scale)} {num(-6 * scale)}) scale({num(scale)})">'
         f'{mark(frame, accent, window, uid)}</g>')
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-2 -2 {num(w + 4)} {num(tag_base + 4)}" role="img" '
            f'aria-label="Sunny Mansfield, good news for all seasons">{m}{words}<path d="{tag_d}" fill="{ink}"/></svg>')


def mark_file(frame=BROWN, sun=ORANGE, window=CREAM, uid="f"):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="10 4 80 94" role="img" aria-label="Sunny Mansfield">'
            f'{mark(frame, sun, window, uid)}</svg>')


OUT.mkdir(parents=True, exist_ok=True)
files = {
    "logo-horizontal.svg": horizontal(),
    "logo-horizontal-reverse.svg": horizontal(ink=CREAM, accent="#f2a03d", frame=CREAM, window=BROWN, uid="hr"),
    "logo-stacked.svg": stacked(),
    "logo-one-color.svg": horizontal(ink=BROWN, accent=BROWN, frame=BROWN, window=CREAM, uid="oc"),
    "mark.svg": mark_file(),
}
for name, svg in files.items():
    (OUT / name).write_text(svg)
    print(name, len(svg))


YELLOWTAIL = Face(FONTS / "Yellowtail.ttf")


def retro(fill=ORANGE, shadow=BROWN, gap=CREAM, shadow_on=True):
    """'Greetings from'-style lockup: script Sunny over MANSFIELD with a cream gap and offset drop shadow."""
    main_size, track = 100, 0.05
    main_w = JOSEFIN.width("MANSFIELD", main_size, track)
    cap = JOSEFIN.cap * main_size
    top = 70
    base = top + cap
    main_d, _ = JOSEFIN.run("MANSFIELD", main_size, 0, base, track)
    s_size = 96
    s_w = YELLOWTAIL.width("Sunny", s_size)
    s_base = top + 8
    s_d, _ = YELLOWTAIL.run("Sunny", s_size, (main_w - s_w) / 2, s_base)
    cx, cy = main_w / 2, s_base - 20
    off = 6
    shadow_layer = f'<path d="{main_d}" transform="translate({off} {off})" fill="{shadow}"/>' if shadow_on else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="-6 -2 {num(main_w + 18)} {num(base + off + 8)}" role="img" aria-label="Sunny Mansfield">'
            f'{shadow_layer}'
            f'<path d="{main_d}" fill="{fill}" stroke="{gap}" stroke-width="5" stroke-linejoin="round" paint-order="stroke"/>'
            f'<g transform="rotate(-6 {num(cx)} {num(cy)})"><path d="{s_d}" fill="{fill}" stroke="{gap}" stroke-width="9" stroke-linejoin="round" paint-order="stroke"/></g></svg>')


for name, svg in {
    "logo-retro.svg": retro(),
    "logo-retro-one-color.svg": retro(shadow_on=False),
    "logo-retro-reverse.svg": retro(fill=CREAM, shadow=ORANGE, gap=BROWN),
}.items():
    (OUT / name).write_text(svg)
    print(name, len(svg))
