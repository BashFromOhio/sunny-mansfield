"""Reads the built site's health.json and fails (so GitHub emails the repo owner) when something needs a person:
the news has gone stale, too few stories built, or several feeds were skipped. Usage: python3 tools/health_check.py site"""
import json
import sys
from pathlib import Path

h = json.loads((Path(sys.argv[1] if len(sys.argv) > 1 else "site") / "health.json").read_text())
problems = []
if h.get("newest_news_hours") is not None and h["newest_news_hours"] > 36:
    problems.append(f"newest news story is {h['newest_news_hours']:.0f} hours old (feeds may be broken)")
if h.get("stories", 0) < 8:
    problems.append(f"only {h.get('stories', 0)} stories built")
if len(h.get("feeds_skipped", [])) >= 3:
    problems.append(f"{len(h['feeds_skipped'])} feeds skipped: " + "; ".join(h["feeds_skipped"][:5]))
for p in problems:
    print(f"::error::Site health: {p}")
if not problems:
    print(f"Site health OK: {h.get('stories')} stories, newest news {h.get('newest_news_hours')} h old, "
          f"{h.get('community_30d')} originals in 30 days")
sys.exit(1 if problems else 0)
