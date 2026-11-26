"""Smoke tests on a freshly built site. GitHub Actions runs these after the build and before deploying,
so a broken build never replaces the live site. Run locally: python3 build.py --sample --out site && pytest -q tests"""
import json
import os
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

SITE = Path(os.environ.get("SITE_DIR", Path(__file__).resolve().parent.parent / "site"))
PAGES = ["index.html", "admin.html", "submit.html", "about.html", "advertise.html", "events.html", "404.html"]


def test_site_was_built():
    assert SITE.is_dir(), f"no built site at {SITE}; run build.py first"


@pytest.mark.parametrize("page", PAGES)
def test_key_pages_exist_and_look_whole(page):
    html = (SITE / page).read_text()
    assert len(html) > 2000, f"{page} is suspiciously small"
    assert "</html>" in html and "<title>" in html, f"{page} looks cut off"


def test_front_page_has_stories():
    html = (SITE / "index.html").read_text()
    assert html.count("/s/") >= 3, "front page links to fewer than 3 stories"


def test_feed_is_valid_xml():
    root = ET.parse(SITE / "feed.xml").getroot()
    assert len(root.findall(".//item")) >= 3


def test_health_report():
    h = json.loads((SITE / "health.json").read_text())
    assert h["stories"] >= 3


@pytest.mark.skipif(not shutil.which("node"), reason="node not installed")
@pytest.mark.parametrize("page", ["index.html", "admin.html", "submit.html"])
def test_inline_scripts_parse(page, tmp_path):
    """A typo in build.py's JavaScript breaks buttons without breaking the build. Catch it here."""
    html = (SITE / page).read_text()
    for i, js in enumerate(re.findall(r"<script>(.*?)</script>", html, re.S)):
        f = tmp_path / f"{i}.js"
        f.write_text(js)
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, f"{page} script {i}: {r.stderr[:300]}"


def test_live_style_rows_build(monkeypatch):
    """The sample build skips old listings, so feed load_submissions a fresh live-style row of each common kind."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import build as b
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    base = {"id": "00000000-0000-0000-0000-000000000001", "when": now, "title": "Test post", "summary": "Line one.\nLine two.",
            "photo": "https://example.com/a.jpg https://example.com/b.jpg", "link": "", "name": "Tester", "role": "",
            "business": "", "town": "Mansfield", "event_date": "", "price": "", "given_at": ""}
    rows = [dict(base, section=s) for s in ("Good News", "Free Stuff", "Event Flyer", "Business Spotlight")]
    rows.append(dict(base, section="Free Stuff", id="00000000-0000-0000-0000-000000000002", given_at=now))
    monkeypatch.setattr(b, "fetch_published", lambda cfg: rows)
    cfg = json.loads((Path(b.HERE) / "config.json").read_text())
    got = b.load_submissions(cfg, False)
    assert len(got["listings"]) == 2 and got["listings"][-1]["given"]
    assert got["stories"]
