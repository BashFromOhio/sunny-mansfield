# Sunny Mansfield

Warm, vintage, newspaper-style good news.

## Overview

Sunny Mansfield is a flat, high-contrast editorial design system for a positive local news site in Mansfield, Ohio. It takes its energy from the printed newspaper: a strong nameplate, big serif headlines, brown ink on cream paper, and one orange accent used like a highlighter. Candy stripes are the one decorative brand element, and they appear only as thin trim. Every element is flat and sharp, and borders do the structural work. A faint collage of old street photos sits behind the page and peeks through the slightly see-through paper; cards and story text always sit on solid cream.

## Colors

### Brand Palette

| Token     | Hex       | Role                                               |
|-----------|-----------|----------------------------------------------------|
| Primary   | `#2E1A10` | Brown ink: headings, primary UI, dominant tone     |
| Secondary | `#D9531E` | Orange: accent, active states, stripes, hover      |
| Tertiary  | `#FBF6EC` | Cream paper: backgrounds, inverse text             |

### Surface Palette

| Token          | Hex       | Role                                          |
|----------------|-----------|-----------------------------------------------|
| Background     | `#FBF6EC` | The paper sheet                                |
| Surface        | `#F3E6CF` | Bands (memories, submit), list hover, page edge |
| Surface Raised | `#EAD8B8` | Pressed states, pull-quote blocks              |

### Content Palette

| Token          | Hex       | Role                                  |
|----------------|-----------|---------------------------------------|
| Text Primary   | `#2E1A10` | Headlines, body copy                  |
| Text Secondary | `#6F5443` | Summaries, sources, timestamps        |
| Text Tertiary  | `#A8927F` | Counts, placeholders, disabled text   |

### Border Palette

| Token         | Hex       |
|---------------|-----------|
| Border Subtle | `#E6D6BD` |
| Border Medium | `#D8C2A0` |
| Border Strong | `#2E1A10` |

### Semantic Colors

| Token   | Hex       |
|---------|-----------|
| Success | `#3F7D3A` |
| Warning | `#C08A12` |
| Error   | `#B8431A` |
| Info    | `#2E1A10` |

## Typography

### Font Stack

| Role             | Font                                                              |
|------------------|-------------------------------------------------------------------|
| Logo only        | Yellowtail and Josefin Sans, converted to outlines in `brand/*.svg` |
| Script accent    | Yellowtail, used only for one short line above a retro headline  |
| Display/Headings | Libre Caslon Text 700, Georgia, 'Times New Roman', serif          |
| UI/Body          | Libre Franklin, -apple-system, 'Segoe UI', Helvetica, sans-serif  |
| Overline         | Josefin Sans 700, Futura, 'Century Gothic', sans-serif            |

### Type Scale

| Level      | Font              | Size | Weight | Line Height | Letter Spacing | Usage                          |
|------------|-------------------|------|--------|-------------|----------------|--------------------------------|
| Display    | Libre Caslon Text | 56px | 700    | 1.05        | -0.02em        | Lead story headline            |
| Headline   | Libre Caslon Text | 38px | 700    | 1.1         | -0.02em        | Story page and submit headline |
| Subhead    | Libre Caslon Text | 24px | 700    | 1.2         | -0.01em        | Card headlines, box titles     |
| Body Large | Libre Franklin    | 20px | 400    | 1.65        | 0              | Lead summary                   |
| Body       | Libre Franklin    | 16px | 400    | 1.7         | 0              | Default text                   |
| Body Small | Libre Franklin    | 14px | 400    | 1.6         | 0              | Card summaries, lists          |
| Caption    | Libre Franklin    | 12px | 500    | 1.5         | 0.01em         | Source and time, photo captions |
| Overline   | Josefin Sans      | 11px | 700    | 1.4         | 0.12em         | Section labels, nav, dateline  |

## Spacing

| Property                   | Value                        |
|----------------------------|------------------------------|
| Base unit                  | 8px                          |
| Scale                      | 4, 8, 16, 24, 32, 48, 64, 96 |
| Component padding, small   | 8px                          |
| Component padding, medium  | 16px                         |
| Component padding, large   | 24px (cards), 32px (bands)   |
| Section spacing, mobile    | 48px                         |
| Section spacing, desktop   | 64px                         |
| Page side gutter           | 16px phone to 48px desktop   |

## Border Radius

| Token | Value  | Usage                  |
|-------|--------|------------------------|
| None  | 0px    | Every element          |
| Full  | 9999px | Not used               |

Sharp, square edges everywhere, like type set on a page.

## Shadows

**Philosophy:** completely flat. Hierarchy comes from weight, size and ink-on-paper contrast, never from elevation.

| Level   | CSS Value | Usage |
|---------|-----------|-------|
| All     | `none`    | None  |

**Focus Ring:** `0 0 0 2px #FBF6EC, 0 0 0 4px #2E1A10`.

**Orange Underline:** a 3px bottom border in `#D9531E` marks the active section tab and closes the lead story.

**Postcard Frame:** the candy stripes as a 16px frame around a cream card with a 3px orange inner border, holding a script line and a retro shadowed headline (Josefin Sans caps, `text-shadow: 2px 2px 0 #FBF6EC, 5px 5px 0 #2E1A10`). Used for "Greetings from Mansfield" and "Got good news?".

**Candy Stripes:** `repeating-linear-gradient(-55deg, #D9531E 0 9px, #FBF6EC 9px 18px)`, 14px tall, at the top of the paper and above the footer, plus a 10px edge on the memories band. Never as a background behind content.

## Components

### Buttons

**Primary** (Share on Facebook, Subscribe)
- Background `#2E1A10`, text `#FBF6EC`, border `2px solid #2E1A10`
- Padding 10px 24px, min height 44px
- Libre Franklin 14px, weight 700, uppercase, letter spacing 0.06em
- Radius 0
- Hover: background and border `#D9531E`
- Active: background and border `#B8431A`

**Secondary** (Show more stories)
- Transparent background, text `#2E1A10`, border `2px solid #2E1A10`
- Hover: background `#2E1A10`, text `#FBF6EC`

**Ghost** (Copy link, Read at source, Visit)
- No border, text `#2E1A10`
- Hover: text `#D9531E`; Active: text `#B8431A`

**Sizes:** Small 6px 16px / 12px (cards), Medium 10px 24px / 14px.

**Disabled:** opacity 0.35, cursor not-allowed.

### Cards

**Default** (story cards)
- Background `#FBF6EC`, border `2px solid #E6D6BD`, radius 0, padding 24px, no shadow
- Photo runs edge to edge across the top
- Hover: border `#2E1A10`

**Elevated** (voting box)
- Border-top `4px solid #D9531E`, other sides `2px solid #E6D6BD`, padding 24px

### Section Tabs
- Josefin Sans 11px, weight 700, uppercase, letter spacing 0.12em, color `#6F5443`
- Count in `#A8927F`
- Selected: text `#2E1A10`, 3px orange bottom border
- Only sections with stories are listed

### Lists ("Also today", shops, voting steps)
- Padding 14px 0, border bottom `1px solid #E6D6BD`
- Hover: background `#F3E6CF`

### Toast (share and copy feedback)
- Background `#2E1A10`, text `#FBF6EC`, Libre Franklin 14px weight 600, padding 8px 16px, radius 0, no shadow

### Logo
- Primary: `logo-retro.svg`, one color (orange): script "Sunny" over "MANSFIELD" in Josefin Sans caps, with a cream gap and brown offset shadow, like a vintage "Greetings from" postcard
- Also: `logo-retro-one-color.svg` (no shadow, for print and stamps), `logo-retro-reverse.svg` (for the brown footer)
- Older arch lockups (horizontal, stacked) remain for reference; the arch mark is the favicon and Facebook profile picture
- Mark: an arch like the city's welcome sign with the sun rising at the end of Main Street
- Clear space: at least the height of the "SUNNY" letters on every side
- Don't recolor, stretch, add effects or retype it

## Do's and Don'ts

1. **Do** make the lead headline big: Libre Caslon Text at 38px and up.
2. **Don't** use orange for more than one element per screen outside the logo and stripes. It is a highlighter, not paint.
3. **Do** use 2px borders for cards, buttons and the paper sheet.
4. **Don't** add rounded corners, shadows or gradients.
5. **Do** use pull quotes with a 4px orange left border and Body Large italic.
6. **Don't** use more than three type sizes in one block: headline, body, caption.
7. **Do** set section labels in uppercase Josefin Sans overlines.
8. **Do** keep brown ink on cream paper for every block of text.
9. **Do** let the photo collage peek through the paper (sheet at 86% cream), but **don't** put it directly behind small text: cards, the lead story and lists sit on solid cream. Stripes are trim only.
10. **Do** use orange on hover for primary buttons, so sharing feels lively.
