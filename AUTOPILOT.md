# Social autopilot

The site makes two feeds that a free posting service can watch and post for you.

| Feed | What's in it | Use it for |
|---|---|---|
| `/social.xml` | One ready-made post a day (image, caption, hashtags, link). It switches over at 8 a.m. Eastern. | Your daily Facebook and Instagram post |
| `/feed.xml` | Every new story, each with a framed 1080×1350 image | Optional: posting every story as it arrives |

The daily post follows the weekly rhythm from the social media kit:

| Day | Post |
|---|---|
| Monday | Neighbor news |
| Tuesday | Shop Local Tuesday (newest business spotlight) |
| Wednesday | What's happening (next event flyer) |
| Thursday | Game day (athlete spotlight or sports story) |
| Friday | 5 good things roundup |
| Saturday | Neighbors helping neighbors (newest fundraiser) |
| Sunday | Story of the week |

If a day has nothing to post, for example no event flyers, it posts the best good news story instead.

## Set it up with Zapier (about 15 minutes, once)

1. Make the Facebook Page and switch Instagram to a Professional account linked to that Page.
2. Sign up at zapier.com. The free plan is enough for one daily post.
3. Create a Zap. **Trigger:** RSS by Zapier → New Item in Feed → feed URL `https://<your site>/social.xml`.
4. **Action 1:** Facebook Pages → Create Page Photo. Photo URL: the item's *Link* (in these feeds the link is the image; the story link is in the caption). Message: the item's *Description*.
5. **Action 2:** Instagram for Business → Publish Photo. Same photo and caption.
6. Test it and turn it on.

Instagram doesn't make links clickable in captions, so add the site link to your Instagram bio.

dlvr.it, IFTTT and Buffer can do the same job with the same feed.

## Things that can't run on autopilot

Creating the accounts themselves, Google Business Profile and Nextdoor verification, replying to comments and messages, and joining Facebook groups all need a person. Everything else in the posting plan is covered above.

## Staying on Zapier's free plan

The free plan allows 100 posts a month, and each Zap can have only one action. These three Zaps fit in it while community posts stay under about 40 a month:

| Zap | Feed | Posts to | Per month |
|---|---|---|---|
| Daily post | `/social.xml` | Facebook Page (Create Page Photo) | ~30 |
| Daily post | `/social.xml` | Instagram (Publish Photo) | ~30 |
| Posted by neighbors | `/community.xml` (every story, athlete spotlight, event flyer, fundraiser and business spotlight sent in through the site, once it's approved) | Facebook Page (Create Page Photo) | usually under 40 |

| Quote of the day + birthdays | `/quotes.xml` (a framed quote image at 8 a.m. and 8 p.m. Eastern, from `quotes.csv`, plus birthday shout-outs and the every-other-day birthday invite) | Facebook Page (Create Page Photo) | ~75 + birthdays |

Facebook only (no Instagram Zap yet): daily post ~30 + quotes ~60 + birthday invites ~15 = ~105, which is just over the free plan before any birthdays.
If you stay on the free plan, drop the quote to once a day or turn the daily post Zap off; otherwise Zapier's Starter plan covers it.

The quote feed also carries the birthday posts, so the quote Zap posts them with no extra setup:

- **Each approved birthday shout-out** at 9 a.m. Eastern on the birthday: a framed "Happy Birthday!" image with the name, the optional message and who it's from.
- **An invite to send a shout-out** every other day at noon Eastern: how it works (name, birthday, optional message and from) and the link to `sunnymansfield.com/birthdays.html`. That's about 15 posts a month, plus one per birthday.

Set up the quote Zap like the daily post one: RSS by Zapier, New Item in Feed, feed URL `https://sunnymansfield.com/quotes.xml`,
then Facebook Pages, Create Page Photo, with Photo = the item's Link and Message = the item's Description.

`/top.xml` (the best new story each day) is also there if you upgrade later or want it instead.

## Daily series feed (`/series.xml`)

Up to two posts a day for the Facebook Page, each with a framed image and a ready caption:

| Time (Eastern) | Post | Where it comes from |
|---|---|---|
| 10 a.m. | Dog of the day | The Richland County Dog Shelter's adoptable dogs on Adopt-a-Pet (the list on the county's Adoptable Dogs page). A different dog every day until every dog has had a turn. |
| Noon | Home of the day | Homes people list on the site, price first. Starts once the first home is approved. |

The dog of the day posts with the dog's own photo from the shelter listing, credited to the shelter and linked to its adoption page. A new home posts the day after it's approved; when nothing is new, the homes go round recent listings, each at most once a week, and the slot skips the day if there are none.

To post it: add a Zap like the daily post one. **Trigger:** RSS by Zapier → New Item in Feed → `https://sunnymansfield.com/series.xml`. **Action:** Facebook Pages → Create Page Photo, with the item's *Link* as the photo and its *Description* as the message. This adds up to about 30 to 60 posts a month. Together with the other Zaps that's over the free plan's 100, so it needs Zapier's paid Starter plan (or dlvr.it, IFTTT or Buffer with the same feed).

## Posts powered by our page counter

The site's own page counter (the admin Reach tab) picks some posts. They ride on the feeds above, so they cost no extra Zapier tasks except milestones:

| When | Post | Feed |
|---|---|---|
| Friday 8 a.m. | **Most read this week**: the stories Mansfield read most (replaces "5 good things" once at least 3 stories have views) | `/social.xml` |
| Monday 8 a.m. | **Neighbor shout-out**: the most-read story a neighbor posted this week, thanking them by name (otherwise regular neighbor news) | `/social.xml` |
| 6 p.m. | **Trending now**: the story with the most views today (at least 5), otherwise the day's top story | `/links.xml` |
| When it happens | **Thank-you milestone** at 1,000, 2,500, 5,000, 10,000 ... page views in 30 days, each posted only once | `/social.xml` |

## Posting without Zapier (fb_post.py)

The site can post to the Facebook Page itself. After every scheduled build, `fb_post.py` reads `social.xml`,
`community.xml`, `quotes.xml`, `series.xml` (photo posts) and `links.xml` (link posts) and posts each item that came due
in the last 3 hours. It checks the Page's recent posts first and skips anything already there, so it never double-posts.

To turn it on (repo Settings > Secrets and variables > Actions):

1. Secret `FB_PAGE_ID`: the Page's numeric ID.
2. Secret `FB_PAGE_TOKEN`: a long-lived Page access token from your own Meta developer app, with `pages_manage_posts`
   and `pages_read_engagement`. Posting to a Page you admin doesn't need Meta's app review.
3. Leave the variable `FB_POSTING` unset for a few days and read the "Post to Facebook" step in the Actions log:
   it lists what it would post. Then turn off the RSS-to-Facebook Zaps and set `FB_POSTING` to `on`.

If a post fails, the run fails and GitHub emails you.
