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
4. **Action 1:** Facebook Pages → Create Page Photo. Photo URL: the item's *Enclosure URL*. Message: the item's *Description*.
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

`/top.xml` (the best new story each day) is also there if you upgrade later or want it instead.
