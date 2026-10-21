import re, sys, json, urllib.request, urllib.parse
sys.path.insert(0, ".")
import build
def n(m): print("::notice::" + m[:900])
_, rss = build.fetch("https://news.google.com/rss/search?q=%22Mansfield%22+Ohio+when:7d&hl=en-US&gl=US&ceid=US:en")
link = re.search(r"<item>.*?<link>(https://news.google.com/[^<]+)</link>", rss, re.S).group(1)
gid = urllib.parse.urlparse(link).path.split("/")[-1]
n("link " + link)
for path in ("rss/articles", "articles"):
    try:
        final, page = build.fetch(f"https://news.google.com/{path}/{gid}", limit=5_000_000)
        n(f"{path}: final={final[:150]} len={len(page)} sg={'data-n-a-sg' in page} attrs={re.findall(r'data-n-a-[a-z]+', page)[:6]} title={re.search(r'<title>(.*?)</title>', page, re.S).group(1)[:80] if '<title>' in page else ''}")
        for m in re.findall(r'<c-wiz[^>]{0,400}', page)[:2]: n("cwiz " + m)
    except Exception as e:
        n(f"{path} FAIL {e}")
try:
    n("decoded " + str(build.google_news_url(link)))
except Exception as e:
    n(f"decode FAIL {type(e).__name__} {e}")

for u in ["https://www.richlandsource.com/feed/?paged=2", "https://www.richlandsource.com/feed/?paged=4"]:
    try:
        _, pg = build.fetch(u); n(f"OK {u} items={pg.count('<item')}")
    except Exception as e: n(f"FAIL {u} {e}")
