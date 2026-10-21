import re, sys, json, urllib.request, urllib.parse
sys.path.insert(0, ".")
import build
def n(m): print("::notice::" + m[:900])
cands = ["https://www.bing.com/news/search?q=Mansfield+Ohio&format=rss"]
for u in cands:
    try:
        final, page = build.fetch(u, limit=200000)
        n(f"OK {u} items={page.count('<item')} sample={page[page.find('<item'):page.find('<item')+700]!r}")
    except Exception as e:
        n(f"FAIL {u}: {e}")
_, wp = build.fetch("https://www.richlandsource.com/feed/")
i = wp.find("<item"); n("WP " + repr(wp[i:i+800]))
_, rss = build.fetch("https://news.google.com/rss/search?q=%22Mansfield%22+Ohio+when:7d&hl=en-US&gl=US&ceid=US:en")
link = re.search(r"<link>(https://news.google.com/[^<]+)</link>", rss).group(1)
gid = urllib.parse.urlparse(link).path.split("/")[-1]
n("link " + link)
for path in ("rss/articles", "articles"):
    try:
        final, page = build.fetch(f"https://news.google.com/{path}/{gid}")
        n(f"{path}: final={final[:150]} len={len(page)} sg={'data-n-a-sg' in page} attrs={re.findall(r'data-n-a-[a-z]+', page)[:6]} title={re.search(r'<title>(.*?)</title>', page, re.S).group(1)[:80] if '<title>' in page else ''}")
        for m in re.findall(r'<c-wiz[^>]{0,400}', page)[:2]: n("cwiz " + m)
    except Exception as e:
        n(f"{path} FAIL {e}")
try:
    n("decoded " + str(build.google_news_url(link)))
except Exception as e:
    n(f"decode FAIL {type(e).__name__} {e}")
