# Sunny Mansfield (SunnyMansfield.com)

A positive-news site for Mansfield and Richland County, Ohio. It pulls headlines from local news
RSS feeds, keeps only uplifting stories, sorts them into sections, and makes each one easy to share
on Facebook. It republishes only a headline, a short summary, and a link back to the original article.

## Files
- `config.json`: site name, tagline, web address, Facebook page, and the news feeds to read.
- `businesses.json`: local business spotlights (edit by hand; the current entries are examples).
- `build.py`: fetches feeds, filters, and writes the site to `site/` (index, one share page per story, `feed.xml`).
- `sample_stories.json`: example stories for previews (`python3 build.py --sample`).
- `.github/workflows/update.yml`: rebuilds and publishes the site every 2 hours on GitHub Pages.

## Photos
Each story shows the photo from its feed item, or the share image (og:image) from the original article page. Photos stay hosted by the source and link back to it. Stories without a photo get a line drawing for their section. To turn photos off for a source, add `"photos": false` to that feed in `config.json`.

## How the "positive only" filter works
A story is dropped if it mentions crime, crashes, deaths, lawsuits, layoffs and similar words, and it
needs at least one upbeat word (award, opens, volunteer, celebrates...). Voting and garage-sale items
only need to be on topic. Obituaries and memorials are always left out. Tune the word lists at the top
of `build.py`.

## Going live
1. Push these files to a public GitHub repository (for example `sunny-mansfield`).
2. In the repository: Settings > Pages > Build and deployment > Source: **GitHub Actions**.
3. Actions tab > "Refresh good news" > Run workflow. After that it runs every 2 hours by itself.
4. The site appears at `https://<username>.github.io/sunny-mansfield/`. The workflow passes that address to
   `build.py`, so share links and Facebook previews point to the right place.
5. Custom domain later: buy SunnyMansfield.com, add it under Settings > Pages > Custom domain, and point
   the domain's DNS at GitHub Pages. Nothing in the code needs to change.

## Vintage postcards
The "Greetings from Mansfield" collage shows drawn sepia scenes until real postcards are added.
To use real ones, put image files in a `postcards/` folder and list them in `config.json`, for example
`"postcards": [{"image": "postcards/main-street.jpg", "caption": "North Main Street, about 1910"}]`.
Use postcards published before 1929 (public domain in the U.S.), ones you own the rights to, or library
images that allow reuse. Avoid images from eBay sellers, Alamy and similar sites, which are sold or licensed.

## Logo
`brand/` holds the logo with all lettering converted to outlines, so it looks the same everywhere:
horizontal, stacked, one-color, reversed (for dark backgrounds), the mark on its own (favicon), a
Facebook profile picture and the 1200x630 share image (`og.png`). The mark is an arch like Mansfield's
welcome sign with the sun rising at the end of Main Street. Lettering: Ultra and Josefin Sans (both
SIL Open Font License). Regenerate with `python3 tools/make_logo.py <fonts folder> brand`.

## UX laws applied
- Aesthetic-usability: one consistent vintage look (orange, cream, brown, one green accent).
- Hick's law and Occam's razor: the section filter only lists sections that have stories this week; four top links.
- Fitts's law: share and copy buttons are at least 44px tall, and Share sits in the same spot on every card.
- Jakob's law: the logo goes home, the nav sits at the top, and headlines link to the story like any news site.
- Miller's law and progressive disclosure: 9 stories at a time with a "Show more stories" button; short summaries only.
- Doherty threshold: static pages, lazy-loaded photos, and filters that update instantly with no page load.
- Proximity, similarity and common region: each story is one card (photo, source, headline, summary, share) and all cards match.
- Visual hierarchy: a large lead story, then the grid; one primary action per card.
- Recognition over recall: the filter bar sticks to the top and shows each section's story count and the active section.
- Von Restorff effect: Facebook blue is used only on Share buttons, so they stand out.
- Peak-end rule and feedback loops: a thank-you message after every share and copy, and a footer that thanks the reader.
- Goal-gradient: "1 of 3 shares this week" progress toward becoming a Sunny Spreader (kept only in the reader's browser).
- Serial position: the lead story comes first and the "Got good news?" call to action comes last.
- Tesler's law: the site does the work (auto-loading, filtering, prefilled Facebook posts) so readers just tap Share.
- Postel's law: feed parsing tolerates messy feeds and missing fields; links like #Seniors or #seniors both work.

## Community posts, review queue and admin

People post from forms on the site itself: `submit.html` (stories, event flyers, Free Stuff, jobs, business spotlights),
`rules.html#apply` (join the team) and `advertise.html#ask` (ad inquiries). Everything lands in a free Supabase
database in **review mode**. Editors approve it at `admin.html`, and the site publishes approved posts on its next
refresh (every 15 minutes). Verified contributors who sign in skip the queue. Setup: [BACKEND.md](BACKEND.md).

## Birthday shout-outs

`birthdays.html` has a short form: the birthday person's name and their birthday (month and day, never the year), plus an
optional special message and who it's from, the sender's email and a required opt-in box. The sender confirms their email
with a 6-digit code (the `verify` function, BACKEND.md step 8), and only then does it reach the admin review queue, where every
shout-out waits for an editor, even from verified people. An orange banner under the menu on every page links to the form.
Once approved, it shows on the front page ("Mansfield birthdays", only on days someone has one) and on `birthdays.html`
on the person's next birthday, once. The same day at 9 a.m. it posts to Facebook through `quotes.xml`, which also posts an
invite to send a shout-out every other day at noon. To sponsor the birthday box, add an ad with `"spot": "A3", "section": "birthday"`.

## Quote of the day

`quotes.csv` feeds the Facebook quote of the day only (it is not shown on the site): the `quote.jpg` image and `quotes.xml`, which posts a framed quote image to Facebook twice a day. Columns are `quote`,
`author`, an optional `date` and optional `Suggested Hashtags`. The quote changes at 8 a.m. and 8 p.m. Eastern,
walking down the list in date order (file order if there are no dates), and starts over at the end.

## Watch Mansfield (local videos)
The top-right spot of the front page hero is "Watch Mansfield": local videos that rotate every 10 seconds (an orange
bar along the bottom is the timer; it pauses while a reader hovers, and stops while a video plays). Each video shows
its YouTube preview picture and its title as the headline. Videos load only when a reader taps Play, and YouTube uses
its privacy-friendly player. With no videos, the spot shows a regular story.

**To add a video**, edit [`watch.json`](watch.json) on GitHub (pencil icon) and paste the YouTube link into `videos`:

```json
"videos": [
  "https://www.youtube.com/watch?v=VIDEO_ID",
  {"url": "https://youtu.be/OTHER_ID", "title": "Your own headline"}
],
```

A plain link is enough: the headline and channel name come from YouTube when the site builds. Use the `{...}` form to
write your own headline. Vimeo and Facebook video links work too (give those a `title`, and an `image` for the preview).
Your picks show first, then the newest `from_channel` videos from the Videos tab of the YouTube channel in `channel_id` (no Shorts) fill in
(currently the 3 newest from The Lindsay's, @thelindsays7871). Set `from_channel` to `0` to show only your picks.

**Watch Mansfield page:** `watch.html` (in the menu) plays every video in one big carousel, then lists them all.

**Sharing:** every video gets its own page at `watch/<video id>.html` with the video, its writeup (the YouTube
description, or a `writeup` you add in `watch.json`) and a Share on Facebook button. The front-page spot has a Share
button too. When shared, Facebook shows `watch/<video id>.jpg`: the video's preview inside the Watch Mansfield frame
(orange tag, title, Sunny Mansfield name), with the writeup as the post's description.
