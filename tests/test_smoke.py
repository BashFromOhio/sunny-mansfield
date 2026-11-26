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
