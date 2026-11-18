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

Up to four posts a day for the Facebook Page, each with a framed image and a ready caption:

| Time (Eastern) | Post | Where it comes from |
|---|---|---|
| 10 a.m. | Dog of the day | The Richland County Dog Shelter's adoptable dogs on Adopt-a-Pet (the list on the county's Adoptable Dogs page). A different dog every day until every dog has had a turn. |
| Noon | Home of the day | Homes people list on the site, price first. Starts once the first home is approved. |
| 2 p.m. | Comeback Buildings | News about old Richland County buildings being restored or reopened. |
| 6 p.m. | Race Night | Mansfield Speedway's own news and racing news. |

A new story posts the day after it arrives. When nothing is new, the series goes round its recent stories, each at most once a week, and skips the day if it has none. Comeback Buildings (Wednesdays), Race Night (Saturdays) and Happy Tails (Sundays) ask people to send something in once a week when they have nothing to share.

To post it: add a Zap like the daily post one. **Trigger:** RSS by Zapier → New Item in Feed → `https://sunnymansfield.com/series.xml`. **Action:** Facebook Pages → Create Page Photo, with the item's *Link* as the photo and its *Description* as the message. This adds up to about 120 posts a month, so it needs Zapier's paid Starter plan (or dlvr.it, IFTTT or Buffer with the same feed).
