"""Post the site's ready-made Facebook posts straight to the Sunny Mansfield Page, instead of through Zapier.

Runs in the GitHub workflow right after the site is built and published (so every image is already live).
It reads the posting feeds build.py writes into site/ and posts each item whose time has come:

  social.xml, community.xml, quotes.xml, series.xml  -> photo post (item link = the framed image, description = caption)
  links.xml                                          -> link post  (item link = the page,  description = message)

No state file: before posting, it reads the Page's recent posts and skips anything already there (same opening
text), so a missed or repeated run never double-posts, and items Zapier already posted are skipped too.

Settings (GitHub > repo Settings > Secrets and variables > Actions):
  secret   FB_PAGE_ID     the Page's numeric ID
  secret   FB_PAGE_TOKEN  a Page access token with pages_manage_posts and pages_read_engagement
  variable FB_POSTING     "on" to really post. Anything else is a dry run that only prints what it would post.

Usage: python3 fb_post.py site
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

GRAPH = "https://graph.facebook.com"
FEEDS = [("social.xml", "photo"), ("community.xml", "photo"), ("quotes.xml", "photo"),
         ("series.xml", "photo"), ("links.xml", "link")]
WINDOW = timedelta(hours=3)     # post items that came due in the last 3 hours (GitHub's schedule can run late)
LOOKBACK = timedelta(days=4)    # how far back to look on the Page for an item that's already posted
MAX_PER_RUN = 4                 # never flood the Page, even if many items come due at once


def key(text):
    """The opening of a post, with spacing, links and punctuation flattened, to spot one that's already up."""
    t = re.sub(r"https?://\S+", "", text or "").lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()[:90]


def due_items(folder, now):
    items = []
    for name, kind in FEEDS:
        path = Path(folder) / name
        if not path.exists():
            continue
        try:
            root = ET.parse(path).getroot()
        except ET.ParseError as x:
            print(f"! {name}: can't read ({x})")
            continue
        for it in root.iter("item"):
            when = (it.findtext("pubDate") or "").strip()
            try:
                at = parsedate_to_datetime(when)
            except (TypeError, ValueError):
                continue
            if not (now - WINDOW <= at <= now):
                continue
            link, text = (it.findtext("link") or "").strip(), (it.findtext("description") or "").strip()
            if link and text:
                items.append({"feed": name, "kind": kind, "at": at, "link": link, "text": text,
                              "title": (it.findtext("title") or "").strip()})
    return sorted(items, key=lambda i: i["at"])


def graph(method, path, token, **params):
    params["access_token"] = token
    data = urllib.parse.urlencode(params).encode()
    url = f"{GRAPH}/{path}"
    req = (urllib.request.Request(url, data=data, method="POST") if method == "POST"
           else urllib.request.Request(url + "?" + data.decode()))
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as x:
        body = x.read().decode(errors="replace")
        try:
            msg = json.loads(body)["error"]["message"]
        except (ValueError, KeyError, TypeError):
            msg = body[:300]
        raise RuntimeError(f"Facebook said: {msg}") from None


def recent_keys(page, token, now):
    since = int((now - LOOKBACK).timestamp())
    got = graph("GET", f"{page}/posts", token, fields="message,created_time", limit=100, since=since)
    return {key(p.get("message", "")) for p in got.get("data", []) if p.get("message")}


def main(folder):
    now = datetime.now(timezone.utc)
    live = os.environ.get("FB_POSTING", "").strip().lower() == "on"
    page, token = os.environ.get("FB_PAGE_ID", "").strip(), os.environ.get("FB_PAGE_TOKEN", "").strip()
    items = due_items(folder, now)
    print(f"{len(items)} item(s) due in the last {WINDOW}. Mode: {'LIVE' if live else 'dry run (set FB_POSTING=on to post)'}")
    if not items:
        return 0
    if not (page and token):
        print("FB_PAGE_ID / FB_PAGE_TOKEN aren't set yet, so nothing can be checked or posted.")
        for i in items:
            print(f"  would post [{i['feed']}] {i['title'][:80]}")
        return 0
    seen = recent_keys(page, token, now)
    posted, failed = 0, 0
    for i in items:
        if key(i["text"]) in seen:
            print(f"  already on the Page: [{i['feed']}] {i['title'][:80]}")
            continue
        if posted >= MAX_PER_RUN:
            print(f"  later (limit {MAX_PER_RUN} a run): [{i['feed']}] {i['title'][:80]}")
            continue
        if not live:
            print(f"  would post ({i['kind']}) [{i['feed']}] {i['title'][:80]}")
            posted += 1
            continue
        try:
            if i["kind"] == "photo":
                r = graph("POST", f"{page}/photos", token, url=i["link"], caption=i["text"])
            else:
                r = graph("POST", f"{page}/feed", token, link=i["link"], message=i["text"])
            print(f"  posted [{i['feed']}] {i['title'][:80]} -> {r.get('post_id') or r.get('id')}")
            seen.add(key(i["text"]))
            posted += 1
            time.sleep(5)
        except Exception as x:  # keep going with the rest; the run fails at the end so GitHub emails the owner
            print(f"! couldn't post [{i['feed']}] {i['title'][:80]}: {x}")
            failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "site"))
