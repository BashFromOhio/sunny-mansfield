# Setting up submissions (about 15 minutes, free)

Sunny Mansfield has no server, so posts come in through two Google Forms. The site reads the answers every 2 hours.

- **Verified people** (emails on your Verified list) publish on their own.
- **Everyone else** waits until an editor types `yes` next to their post.
- Email addresses are never published.

## 1. The submission form

Create a Google Form called **Submit to Sunny Mansfield**. In **Settings → Responses**, set **Collect email addresses** to **Verified**. This makes people sign in with Google, so you know who sent each post.

Add these questions **in this order**:

| # | Question | Type |
|---|----------|------|
| 1 | Section | Dropdown: Good News, Neighborhoods, Local Business, Garage Sales, Seniors, Celebrations of Life, Voting, Cool Research, Business Spotlight, Event Flyer, Buy & Sell |
| 2 | Headline | Short answer, required |
| 3 | Tell us what happened (2 to 4 sentences) | Paragraph, required |
| 4 | Photo or flyer | File upload, 1 image (see the note below) |
| 5 | Link for more (website, event page) | Short answer |
| 6 | Your name as it should appear | Short answer, required |
| 7 | You are a... | Dropdown: Neighbor, Student reporter, Business, Mentor, Organization |
| 8 | Business name (spotlights only) | Short answer |
| 9 | Town or neighborhood | Short answer |
| 10 | Event date (flyers only) | Date |
| 11 | Price (Buy & Sell only) | Short answer |
| 12 | How buyers reach you (Buy & Sell only: phone, email or link; this is shown publicly) | Short answer |
| 13 | I agree to the Sunny rules | Checkbox, required |

**Photo note:** uploaded photos go into a folder in your Google Drive. Open that folder, choose **Share**, and set it to **Anyone with the link can view**. Otherwise photos won't show on the site.

## 2. The Google Sheet

In the form's **Responses** tab, click **Link to Sheets**. Then, in that sheet:

1. In column **P** of *Form Responses 1*, type the header `Approved`. Editors type `yes` there to publish a post.
2. Add a tab named **Verified**. Put one verified person's email per row in column A. This tab stays private.
3. Add a tab named **Publish**. In cell A1, paste:

```
=QUERY({'Form Responses 1'!A2:A,'Form Responses 1'!C2:N,ARRAYFORMULA(IF((LOWER('Form Responses 1'!P2:P)="yes")+(COUNTIF(Verified!A:A,'Form Responses 1'!B2:B)>0),"yes",""))},"select Col1,Col2,Col3,Col4,Col5,Col6,Col7,Col8,Col9,Col10,Col11,Col12,Col13 where Col14='yes'",0)
```

4. Go to **File → Share → Publish to web**. Choose the **Publish** tab and **Comma-separated values (.csv)**, then click Publish. Copy the link.

To give an editor access, share the sheet with them (Editor access). They can then type `yes` on posts and add people to the Verified tab.

## 3. The "Join the team" form

Create a second form, **Join the Sunny Mansfield team**, with email collection turned on. Ask:

- Name
- Town
- Which role: Community editor, Student reporter, Business correspondent, or Mentor and motivator
- What you'd like to cover
- Age 18 or older? (If not, ask for a parent or guardian's name and email.)

When you approve someone, add their email to the **Verified** tab.

## 4. Connect them to the site

Put the three links in `config.json`:

```json
"submit_form": "https://forms.gle/...",
"join_form": "https://forms.gle/...",
"submissions_csv": "https://docs.google.com/spreadsheets/d/e/.../pub?gid=...&single=true&output=csv"
```

Event flyers ride the front page carousel until the day after the event date. Buy & Sell listings stay up for 30 days.

To show a paid ad in the carousels and on the Buy & Sell page, add it to `config.json`:

```json
"ads": [{"name": "Sample Bank", "text": "Proud to keep Mansfield sunny", "image": "https://.../ad.jpg", "link": "https://..."}]
```

With no ads listed, those spots say "Your business here" and link to the Advertise page. You can also add `"advertise_form"` with a form link for ad inquiries.

The next refresh, within 2 hours, publishes approved and verified posts. Business Spotlights each get their own page under `/b/`.
