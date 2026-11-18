# Google: search results and ads

What people see when they Google "Sunny Mansfield" or "Mansfield Ohio good news" comes from the site itself:

| In Google | Comes from |
|---|---|
| Site name above the link | "Sunny Mansfield" (structured data on the front page, with SunnyMansfield and SunnyMansfield.com as other names) |
| Little icon | the sunrise arch (`brand/icon-192.png`, `favicon.ico`) |
| Blue headline | "Sunny Mansfield \| Good News & Things to Do in Mansfield, Ohio" |
| Gray description | "Good news from Mansfield, Ohio and Richland County: events and things to do, high school sports, shop-local spotlights, free stuff and jobs. Updated daily." |
| Links under the result | the menu at the top of every page (Events, Watch Mansfield, Sports, Donate...) |
| Logo in the side panel | `brand/icon-512.png`, once Google trusts the site (a Business Profile helps) |

Google picks these up on its own schedule, usually within a few days to a few weeks after step 1.
A plain search for "Mansfield" is crowded (Mansfield, Texas, Massachusetts, England and the city government), so the
realistic first wins are searches like "Sunny Mansfield", "things to do in Mansfield Ohio" and "Mansfield Ohio good news".

## 1. Google Search Console (free, 10 minutes)

1. Go to search.google.com/search-console and choose **Add property > URL prefix**, then type `https://sunnymansfield.com/`.
2. Pick **HTML tag**. Copy only the code inside `content="..."`.
3. On GitHub, open `config.json`, press the pencil, paste the code into `"google_site_verification": ""` and commit.
4. Wait about two minutes for the site to rebuild, then press **Verify** in Search Console.
5. Open **Sitemaps** and submit `sitemap.xml`, then `news-sitemap.xml`.
6. Open **URL inspection**, type `https://sunnymansfield.com/` and press **Request indexing**.

## 2. Google Business Profile (free, needs a person to verify)

1. Go to business.google.com and add **Sunny Mansfield**.
2. Category: **News service** (or **Media company**). Choose that you serve customers in an area, not at an address, and
   list Mansfield, Ontario, Lexington, Shelby, Bellville and Richland County.
3. Website: `https://sunnymansfield.com`. Logo: `brand/icon-512.png`. Cover photo: `brand/og.png`.
4. Finish Google's verification (usually a video or a code).

## 3. Google Ads (paid, optional)

1. At ads.google.com, create the account and switch to **Expert mode** so you can pick a Search campaign.
2. Get your tag: **Tools > Data manager > Google tag**. Copy the ID that starts with `AW-`.
   For Google Analytics too, add its `G-` ID after a comma, like `"G-ABC123, AW-123456"`.
3. On GitHub, paste it into `"google_tag": ""` in `config.json` and commit. Every page gets the tag on the next rebuild,
   and the privacy page switches to wording that explains the Google tag. Leave it blank and there is no tag and no cookies.
4. A simple first campaign: **Search**, location **Richland County, Ohio** plus about 20 miles, $5 to $10 a day.
   Keywords: "things to do in mansfield ohio", "mansfield ohio events", "events this weekend mansfield",
   "mansfield ohio news", "sunny mansfield". Headlines: "Good News From Mansfield", "Things To Do This Weekend",
   "Free Local Events & Shout-Outs". Send people to `https://sunnymansfield.com/events.html` or the front page.
5. For a goal Google can count, use **page view** of `events.html` or a visit longer than one minute.

## AdSense (earning from ads on the site)

1. Sign up at adsense.google.com with the site `sunnymansfield.com`.
2. Put the publisher ID in `config.json` as `"adsense_client": "ca-pub-…"`.
3. The next build adds the AdSense Auto ads script to every page, writes `/ads.txt`, and adds an "Ads on this site" section to the privacy page. With the setting empty, none of that appears.
4. In AdSense, request a review of the site. Turn on a consent message under Privacy & messaging for visitors from Europe.
