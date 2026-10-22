#!/usr/bin/env python3
"""Write the Sunny Mansfield social media guide (HTML, printed to PDF by render_social.js --pdf).

  python3 tools/social_guide.py <out dir>     (after make_social.py and render_social.js)
"""
import html
import json
import sys
from pathlib import Path

OUT = Path(sys.argv[1]).resolve()
HERE = Path(__file__).resolve().parent.parent
assets = {a["name"]: a for a in json.loads((OUT / "assets.json").read_text())}
e = html.escape


def img(name, width="100%", border=True):
    a = assets[name]
    chk = "background:repeating-conic-gradient(#e8e0d0 0 25%,#fff 0 50%) 0 0/16px 16px;" if a["transparent"] else ""
    return (f'<img src="file://{a["png"]}" style="width:{width};display:block;{chk}'
            f'{"border:1px solid #D8C2A0;" if border else ""}">')


def fig(name, caption=None, width="100%"):
    a = assets[name]
    cap = caption or a["note"]
    return (f'<figure>{img(name, width)}<figcaption><b>{e(a["folder"])}/{e(name)}.png</b> · {a["w"]}×{a["h"]}<br>{e(cap)}</figcaption></figure>')


BIOS = [
    ("Facebook Page: Intro (101 characters max)",
     "Good news from Mansfield & Richland County, Ohio. Neighbors, shops, events and lives worth celebrating."),
    ("Facebook Page: About / description",
     "Sunny Mansfield is a free, uplifting local paper for Mansfield and Richland County, Ohio. We gather the good news in one "
     "place: neighbors helping neighbors, new businesses, students winning, celebrations of life, community events, Buy & Sell, "
     "and plain civic info like how to vote. No crime, no crashes, no arguing. Read it, share it, shop local, and send in your own "
     "good news at SunnyMansfield.com."),
    ("Instagram bio (150 characters max)",
     "☀️ Good news from Mansfield & Richland County, OH\n📰 Neighbors · Shops · Events · Celebrations\n👇 Read it free + send your news"),
    ("X bio (160 characters max)",
     "Good news for all seasons from Mansfield & Richland County, Ohio. Neighbors, local shops, events and lives worth celebrating. Send us your good news ☀️"),
    ("LinkedIn Page: Tagline (120 characters max)",
     "Uplifting local news for Mansfield & Richland County, Ohio, written with neighbors, students and local businesses."),
    ("LinkedIn Page: Overview",
     "Sunny Mansfield is a free digital magazine of good news for Mansfield and Richland County, Ohio. We link to the best local "
     "reporting from Richland Source and other outlets, publish stories sent in by neighbors, and give every local business a free "
     "spotlight page. We work with student reporters, business correspondents and mentors, and we partner with local businesses "
     "on sponsorships that keep the paper free. Industry: Online Audio and Video Media / Newspaper Publishing. "
     "Location: Mansfield, Ohio."),
]

CAPTIONS = [
    ("Launch post (Facebook and Instagram)", "launch-announcement",
     "Greetings from Mansfield! ☀️\n\nSunny Mansfield is here: a free paper for one thing, the good news. Neighbors helping "
     "neighbors, new shops on Main, students winning, lives worth celebrating, and the events that bring Richland County together.\n\n"
     "Read it, share it, and send us your own good news. Link in bio.\n\n#SunnyMansfield #MansfieldOhio #RichlandCounty #GoodNews"),
    ("Launch post (X)", "x-launch-1600x900",
     "Mansfield's good news finally has a home ☀️ Neighbors, local shops, events and celebrations from across Richland County, free "
     "at SunnyMansfield.com #SunnyMansfield #MansfieldOhio"),
    ("Launch post (LinkedIn)", "x-launch-1600x900",
     "Today we're launching Sunny Mansfield, a free digital magazine of good news for Mansfield and Richland County.\n\n"
     "We're looking for:\n• Student reporters who want real bylines\n• Business owners and marketers to tell local business stories\n"
     "• Mentors who want to help new writers grow\n• Local sponsors who want to be part of something positive\n\n"
     "Free business spotlights are open now: SunnyMansfield.com"),
    ("Everyday good news story", "good-news-example",
     "A line down the block on opening day! 🥐 [Business] opened on North Main this week, and the owners thanked the neighbors who "
     "helped fix up the storefront.\n\nRead more (link in bio) and give them a visit. 📸 [photo credit]\n\n#SunnyMansfield #ShopLocalMansfield #MansfieldOhio"),
    ("Ask for submissions", "got-good-news",
     "Hey neighbor! 👋 Know about a ribbon cutting, a 100th birthday, a team win or a kid doing something great? Send it in and "
     "we'll share it with the whole county. It takes two minutes: link in bio → Submit news.\n\n#SunnyMansfield #RichlandCounty"),
    ("Shop Local Tuesday", "shop-local-spotlight",
     "Shop Local Tuesday 🛍️ Three generations have run the counter at [Business] on Park Avenue, and this week they turn 75! "
     "Stop in and say congrats. @[business handle]\n\nWant a free spotlight for your business? Link in bio.\n\n#ShopLocalMansfield #MansfieldOhio"),
    ("Weekend events", "event-this-weekend",
     "This weekend in Mansfield 🍂 [Event], [day and time] at [place]. [One sentence on why it's fun.]\n\nSee every event and post "
     "your own flyer for free: link in bio. #SunnyMansfield #MansfieldOhio #ThingsToDoMansfield"),
    ("Friday roundup", "five-good-things",
     "5 good things this week in Richland County ☀️\n1. ...\n2. ...\n3. ...\n4. ...\n5. ...\n\nWhich one made you smile? Share this "
     "with someone who needs some good news today. #SunnyMansfield"),
    ("Celebration of life (only with the family's OK)", "celebration-of-life",
     "Celebrating the life of [Full Name], [years]. [Two warm sentences about who they were and what they loved.]\n\n[Service details.] "
     "Our love to the family. 🕊️"),
    ("Share ask", "one-share",
     "One share can make someone's day. 💛 Every story on Sunny Mansfield has a Share button, so pass the good news along to "
     "someone who needs it. #SunnyMansfield"),
]

WEEK = [
    ("Monday", "Neighbor news", "A community story: volunteers, kids, block parties, milestones."),
    ("Tuesday", "Shop Local Tuesday", "One business spotlight. Tag the business so they share it."),
    ("Wednesday", "What's happening", "Event flyers for the next 7 days, plus a story from the site."),
    ("Thursday", "Throwback Thursday", "A vintage Mansfield photo or postcard. These get the most shares."),
    ("Friday", "5 good things + weekend", "The roundup post, then weekend events in stories."),
    ("Saturday", "Garage sales and Buy & Sell", "Best listings and garage sales, in stories."),
    ("Sunday", "Celebrations and gratitude", "Birthdays, anniversaries, celebrations of life, a thank-you note."),
]

LAUNCH = [
    ("Day 1", "Post the launch announcement on every platform. Pin it to the top of Facebook and X. Invite your Facebook friends to like the Page."),
    ("Day 2", "Got good news? post, plus a story with a link sticker to the submit page."),
    ("Day 3", "First Shop Local spotlight. Message the business first so they're ready to share it."),
    ("Day 4", "Throwback Thursday with a vintage Mansfield photo. Ask: \"Who remembers this?\""),
    ("Day 5", "5 good things roundup and weekend events."),
    ("Day 6", "Share ask (\"One share can make someone's day\"). Post in local Facebook groups where the rules allow it."),
    ("Day 7", "Thank-you post: how many neighbors joined this week and the most shared story."),
]

ELSE = [
    ("Google Business Profile", "Must have", "Shows up when people search \"Mansfield good news\" or the name on Google and Maps. Free at business.google.com. Choose \"Service area business\" with Richland County so no street address is shown."),
    ("Nextdoor Business Page", "Must have", "The most local network there is. Post events and good news to Mansfield neighborhoods for free."),
    ("Facebook Group: \"Sunny Mansfield Neighbors\"", "Must have", "Groups get far more reach than Pages. Neighbors post their own good news, and you share the best to the Page and site. Use the group cover in 02-facebook."),
    ("Weekly email newsletter", "Must have", "\"The Sunny Weekend\" on Beehiiv or Kit, free to start. You own the list, unlike followers. Header image in 07-email-and-web."),
    ("Your own email address", "Must have", "hello@sunnymansfield.com through Google Workspace (about $7 a month) or Zoho Mail (free). Looks trustworthy to sponsors."),
    ("TikTok and Instagram Reels", "Recommended", "15-second videos: walking through a new shop, a festival, a 100th birthday parade. Video gets the most reach on every platform today."),
    ("Threads", "Easy add", "Sign in with your Instagram account. Use the same bio and picture."),
    ("Canva (free) with a brand kit", "Recommended", "Upload the logo, the colors (#D9531E, #2E1A10, #FBF6EC) and the see-through frames so anyone on the team can make posts that match."),
    ("Meta Business Suite", "Recommended", "Free. Schedules Facebook and Instagram posts a week ahead from one screen."),
    ("Printed QR flyers and window stickers", "Recommended", "The flyer in 08-print goes in shop windows, libraries, churches and coffee shops. Stickers with \"Featured on Sunny Mansfield\" are a nice thank-you for spotlight businesses."),
    ("One-page sponsor sheet", "Later", "A one-page PDF with your reach numbers and ad packages. Make it after 1 to 2 months of numbers."),
    ("YouTube and Pinterest", "Later", "Optional. YouTube for longer event videos; Pinterest for vintage Mansfield photos."),
]

SETUP = {
    "Facebook Page": [
        "Create the Page at facebook.com/pages/create. Name: Sunny Mansfield. Category: Media/News Company.",
        "Profile picture: profile-orange-1080.png. Cover: facebook-cover-1640x624.png.",
        "Username: @SunnyMansfield. Add the Intro and About text below, the website, and the email.",
        "Add a button: \"Learn more\" linking to the site, or \"Send message\".",
        "Turn on Professional dashboard, and set up Meta Business Suite to schedule posts.",
    ],
    "Instagram": [
        "Create @sunnymansfield and switch to a Professional account (Creator or Business, category: News & Media Website).",
        "Connect it to the Facebook Page in Meta Business Suite so you can post to both at once.",
        "Profile picture: profile-orange-1080.png. Bio from below. Link: the site.",
        "Make 6 story highlights (Good news, Events, Buy & Sell, Shop local, Submit, About) using the highlight covers.",
        "Post the launch post, then 2 to 3 more, before inviting people, so the profile doesn't look empty.",
    ],
    "X (Twitter)": [
        "Handle: @SunnyMansfield (or @SunnyMansfieldOH if it's taken).",
        "Profile picture: profile-orange-400.png. Header: x-header-1500x500.png.",
        "Bio from below, location \"Mansfield, Ohio\", and the website.",
        "Pin the launch post. Follow local outlets, schools, the city, the county and businesses.",
    ],
    "LinkedIn": [
        "Create a Company Page at linkedin.com/company/setup. Name: Sunny Mansfield. Industry: Online Audio and Video Media (or Newspaper Publishing). Size: 2-10.",
        "Logo: profile-orange-400.png. Cover: linkedin-page-cover-1128x191.png.",
        "Add the tagline and overview below.",
        "On your own profile, use linkedin-personal-banner-1584x396.png and add Sunny Mansfield as your current job.",
        "LinkedIn is for sponsors and partners: post business spotlights, launch news and calls for student reporters.",
    ],
}

HANDLES = [("Main handle everywhere", "@SunnyMansfield"), ("If taken", "@SunnyMansfieldOH  or  @SunnyMansfieldNews"),
           ("Branded hashtag", "#SunnyMansfield"),
           ("Always add 2 to 4 of", "#MansfieldOhio  #RichlandCounty  #MansfieldOH  #RichlandCountyOhio"),
           ("By topic", "#ShopLocalMansfield  #ThingsToDoMansfield  #OhioGoodNews  #GoodNews"),
           ("By town", "#OntarioOhio  #LexingtonOhio  #ShelbyOhio  #BellvilleOhio  #MadisonOhio")]

SIZES = [
    ("Facebook", "Profile picture", "profile-orange-1080", "Shows as a circle; 1080 keeps it sharp"),
    ("Facebook", "Page cover", "facebook-cover-1640x624", "Phones crop the sides; the logo stays in the safe middle"),
    ("Facebook", "Group cover", "facebook-group-cover-1640x856", ""),
    ("Facebook", "Event cover", "facebook-event-cover-1920x1005", ""),
    ("Instagram", "Profile picture", "profile-orange-1080", "Shows as a small circle"),
    ("Instagram", "Feed post", "good-news-example", "1080×1350 (4:5) gets the most screen space"),
    ("Instagram", "Story and highlight covers", "story-got-good-news", "1080×1920"),
    ("X", "Profile picture", "profile-orange-400", ""),
    ("X", "Header", "x-header-1500x500", "Top and bottom edges can be cropped"),
    ("X", "Landscape post", "x-launch-1600x900", "4:5 posts also work on X"),
    ("LinkedIn", "Page logo", "profile-orange-400", ""),
    ("LinkedIn", "Page cover", "linkedin-page-cover-1128x191", ""),
    ("LinkedIn", "Personal banner", "linkedin-personal-banner-1584x396", ""),
    ("Everywhere", "Link preview", "link-preview-1200x630", "Used when the site link is shared"),
]

css = (HERE / "tools" / "social_guide.css").read_text() if (HERE / "tools" / "social_guide.css").exists() else ""
parts = []
parts.append(f"""<section class="cover"><div class="stripes"></div>
<div class="covin"><img src="file://{HERE / 'brand' / 'logo-retro.svg'}" style="width:5.4in">
<h1>Social media kit</h1><p class="lead">Profile pictures, covers, post templates, story frames, bios, captions and a posting plan for
Facebook, Instagram, X and LinkedIn, plus what else to set up.</p>
<p class="small">Every image is in the ZIP, sorted into folders by platform. October 2026.</p><p class="small">Made in Mansfield by Ofgeneration LLC. &copy; 2026 Ofgeneration LLC. All rights reserved.</p></div><div class="stripes bottom"></div></section>""")

parts.append("""<section><h2>Before you post</h2><ol class="big">
<li><b>Buy SunnyMansfield.com first.</b> The templates and bios say SunnyMansfield.com. Until it's connected, the site lives at
bashfromohio.github.io/sunny-mansfield. The QR flyer already points there and should be remade once the domain is live.</li>
<li><b>Claim the same handle everywhere on the same day:</b> @SunnyMansfield, so nobody else takes it.</li>
<li><b>Photos:</b> the example posts use the vintage collage and sample photos. For real posts, use your own photos, ones people send in with
permission, or outlet photos only as a link preview. The collage's original source isn't confirmed, so check it before using it in paid ads.</li>
<li><b>Paid posts:</b> when a business pays, use the platform's \"Paid partnership\" label or write #ad.</li></ol></section>""")

rows = "".join(f"<tr><td>{p}</td><td>{w}</td><td>{e(assets[n]['folder'])}/{e(n)}.png</td><td>{assets[n]['w']}×{assets[n]['h']}</td><td>{e(note)}</td></tr>"
               for p, w, n, note in SIZES)
parts.append(f'<section><h2>What goes where</h2><table><tr><th>Platform</th><th>Spot</th><th>File</th><th>Size (px)</th><th>Note</th></tr>{rows}</table></section>')

parts.append('<section><h2>Profile pictures</h2><p>One picture everywhere so people recognize you: the arch and sunrise from the logo, on orange.</p>'
             f'<div class="g3">{fig("profile-orange-1080")}{fig("profile-cream-1080")}{fig("profile-orange-400")}</div></section>')
parts.append('<section><h2>Covers and banners</h2>'
             + "".join(fig(n) for n in ["facebook-cover-1640x624", "facebook-group-cover-1640x856", "x-header-1500x500",
                                        "linkedin-page-cover-1128x191", "linkedin-personal-banner-1584x396"]) + "</section>")
parts.append('<section><h2>More banners</h2><div class="g2">'
             + "".join(fig(n) for n in ["facebook-event-cover-1920x1005", "link-preview-1200x630", "newsletter-header-1200x400",
                                        "email-signature-600x150"]) + "</div></section>")
parts.append('<section><h2>Post templates</h2><p>Each one is 1080×1350, which fits Facebook, Instagram and LinkedIn. The two see-through frames '
             '(checkered areas) go on top of your own photo in Canva or a phone editor. To have Claude make a ready-to-post version with real '
             'photos and text, just ask.</p><div class="g4">'
             + "".join(fig(n) for n in ["launch-announcement", "good-news-example", "good-news-frame-blank", "got-good-news",
                                        "shop-local-spotlight", "event-this-weekend", "five-good-things", "celebration-of-life",
                                        "one-share", "x-launch-1600x900"]) + "</div></section>")
parts.append('<section><h2>Stories and highlights</h2><div class="g4">'
             + "".join(fig(n) for n in ["story-got-good-news", "story-new-post-frame", "highlight-good", "highlight-events",
                                        "highlight-sales", "highlight-biz", "highlight-submit", "highlight-about"])
             + '</div></section>')
parts.append(f'<section><h2>Print</h2><div class="g2">{fig("qr-flyer-letter")}<div><p>Print on letter paper (8.5×11). Put it up in shop windows, '
             'libraries, churches, coffee shops and school offices. Businesses with a spotlight are usually happy to put one up.</p>'
             '<p>The QR code opens the site at its current address. Ask Claude to remake it once SunnyMansfield.com is connected.</p></div></div></section>')

bios = "".join(f'<div class="copy"><h3>{e(t)} <span class="count">{len(b)} characters</span></h3><pre>{e(b)}</pre></div>' for t, b in BIOS)
handles = "".join(f"<tr><td>{e(a)}</td><td><b>{e(b)}</b></td></tr>" for a, b in HANDLES)
parts.append(f'<section><h2>Handles, bios and hashtags</h2><table class="kv">{handles}</table>{bios}</section>')

for platform, steps in SETUP.items():
    pass
setup = "".join(f'<div class="copy"><h3>{e(p)}</h3><ol>{"".join(f"<li>{e(s)}</li>" for s in steps)}</ol></div>' for p, steps in SETUP.items())
parts.append(f'<section><h2>Setting up each account</h2>{setup}</section>')

caps = "".join(f'<div class="cap">{img(n, "1.35in")}<div><h3>{e(t)}</h3><pre>{e(c)}</pre></div></div>' for t, n, c in CAPTIONS)
parts.append(f'<section><h2>Ready-to-use captions</h2><p>Swap the parts in [brackets]. The same text is in captions.txt in the ZIP for easy copying.</p>{caps}</section>')

week = "".join(f"<tr><td><b>{d}</b></td><td>{t}</td><td>{x}</td></tr>" for d, t, x in WEEK)
launch = "".join(f"<tr><td><b>{d}</b></td><td>{x}</td></tr>" for d, x in LAUNCH)
parts.append(f"""<section><h2>When to post</h2>
<p><b>How often:</b> Facebook 1 to 2 posts a day. Instagram 4 to 5 feed posts a week plus daily stories. X 1 to 3 a day (links to stories).
LinkedIn 2 a week (business spotlights, partner news, calls for student reporters). The best times locally are 7 to 9 a.m., noon, and 7 to 9 p.m.</p>
<h3>A simple weekly rhythm</h3><table><tr><th>Day</th><th>Theme</th><th>What to post</th></tr>{week}</table>
<h3>Launch week</h3><table>{launch}</table></section>""")

els = "".join(f"<tr><td><b>{e(a)}</b></td><td><span class='tag {b.split()[0].lower()}'>{e(b)}</span></td><td>{e(c)}</td></tr>" for a, b, c in ELSE)
parts.append(f'<section><h2>What else you should have</h2><table><tr><th>Channel or tool</th><th>Priority</th><th>Why</th></tr>{els}</table></section>')

parts.append("""<section><h2>Brand quick reference</h2><div class="sw">
<div style="background:#D9531E;color:#FBF6EC">Orange<br>#D9531E</div><div style="background:#2E1A10;color:#FBF6EC">Brown ink<br>#2E1A10</div>
<div style="background:#FBF6EC;color:#2E1A10;border:1px solid #D8C2A0">Cream<br>#FBF6EC</div><div style="background:#F3E6CF;color:#2E1A10">Tan<br>#F3E6CF</div></div>
<table class="kv"><tr><td>Headlines</td><td><b>Libre Caslon Text</b> Bold (free on Google Fonts and in Canva)</td></tr>
<tr><td>Big retro titles</td><td><b>Josefin Sans</b> Bold, all caps, with a brown offset shadow</td></tr>
<tr><td>Script accents</td><td><b>Yellowtail</b>, one short line only (\"Hey neighbor!\")</td></tr>
<tr><td>Body text</td><td><b>Libre Franklin</b></td></tr>
<tr><td>Trim</td><td>Orange and cream candy stripes, at the edges only</td></tr>
<tr><td>Voice</td><td>Warm, neighborly, specific. Celebrate people by name, never mock or argue. One emoji per post is plenty.</td></tr></table></section>""")

page = f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Libre+Caslon+Text:wght@700&family=Libre+Franklin:wght@400;600;700&family=Josefin+Sans:wght@700&display=swap">
<style>{css}</style></head><body>{''.join(parts)}</body></html>"""
(OUT / "guide.html").write_text(page)
(OUT / "captions.txt").write_text("SUNNY MANSFIELD: BIOS AND CAPTIONS\n\n" + "\n\n".join(f"== {t} ==\n{b}" for t, b in BIOS) + "\n\n"
                                  + "\n\n".join(f"== {t} (image: {n}.png) ==\n{c}" for t, n, c in CAPTIONS) + "\n")
print("guide written")
