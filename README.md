# Pinterest Automation + Website

Automates an affiliate workflow: product catalog -> static website -> Pinterest pins.

## Flow
1. Add products to `data/products.json` (real image + affiliate URLs).
2. `python -m pinbot build` – generates the site in `docs/` (host free on GitHub Pages: Settings -> Pages -> `/docs`).
3. `python -m pinbot queue` – creates a pin (with UTM-tagged link to your site) for each new product.
4. `python -m pinbot publish` – dry-run preview; add `--live` to post via Pinterest API v5 (paced, max `--limit` per run).

Copy `.env.example` to `.env` and fill in `PINTEREST_ACCESS_TOKEN`, `PINTEREST_BOARD_ID`, `SITE_URL`.
Run `python -m unittest discover tests` for tests. No third-party dependencies.

## Notes
- Pins link to your own pages (which carry the affiliate disclosure) rather than straight to affiliate links, which Pinterest favors.
- Schedule `queue`/`publish --live` with cron or a GitHub Actions workflow once you're happy with dry-runs.

## Zero-budget path (no Pinterest API needed)
You can run everything free today:
1. Put real products in `data/products.json` (niche: beauty / self-care "glow" ideas).
2. `python -m pinbot build && python -m pinbot queue && python -m pinbot export`
3. In Pinterest: **Create -> Bulk create pins** -> upload `data/pinterest_bulk.csv`. Pins are scheduled 3/day (09:00, 14:00, 20:00) up to 30 days ahead. Set `PINTEREST_BOARD_NAME` in `.env` to match your board (default "Glowing Era").
4. Optional hands-off alternative: claim your site in Pinterest, then **Settings -> Claim -> Auto-publish** with `SITE_URL/feed.xml`.

When you later get Pinterest API access, add `PINTEREST_ACCESS_TOKEN` / `PINTEREST_BOARD_ID` as GitHub secrets and `SITE_URL` as a repo variable; `.github/workflows/daily.yml` then posts 3 pins every day.

## Beauty/skincare setup (free)
- **Pin images** are generated automatically (`pinbot/images.py`, 1000x1500, soft pink gradient + hook headline). Run `pip install -r requirements.txt`. Add `"image_url"` to a product to use your own image instead.
- **Affiliate links**: put your Amazon, EarnKaro or Cuelinks link in `affiliate_url` (replace the `REPLACE` placeholders). Set `AMAZON_TAG` to auto-add your Associates tag to amazon links.
- **Hosting**: push to `main`, then Settings -> Pages -> Source: **GitHub Actions** (repo must be public for free Pages). Set repo variables `SITE_URL` (e.g. `https://<user>.github.io/<repo>`), `SITE_NAME`, `AMAZON_TAG`.
- Pin images need a live `SITE_URL`: deploy first, then upload the bulk CSV.
- Avoid showing prices on the site (Amazon Associates rules), and keep the affiliate disclosure on every page (already included).

## Guides (content marketing)
Write guides as markdown in `content/<slug>.md`:

```
---
title: ...            (page title)
hook: ...             (short headline on the pin image)
description: ...      (meta description + pin text)
tags: a, b, c         (become pin hashtags)
products: slug1, slug2   (product slugs from products.json, shown as "Products mentioned")
---
Body with `## headings`, `- lists`, **bold**, [links](https://...).
```
Each guide gets a page under `/a/`, its own generated pin, and a place in the RSS feed. Queue order is guides first, then products. Add 1-2 new guides a week and the daily workflow keeps posting 3 pins/day.

## Photo pins (recommended: photo-led pins perform better in beauty)
Put product photos in `data/images/` and reference them with `"photo": "name.jpg"` in `products.json`.
- A product with a photo gets a photo pin: headline on top, photo in a rounded white card.
- A guide whose products have photos gets a collage pin (up to 3 products).
- No photo = the gradient text pin.
Only use photos you own or that your affiliate program explicitly lets you use (Amazon SiteStripe/Associates creatives, merchant creatives from EarnKaro/Cuelinks). Don't copy images off product pages.

## Auto-download product photos
Add `"image_source"` to a product: either a direct image link or the product page link (the page's `og:image` preview image is used).
`python -m pinbot fetch-images` downloads them into `data/images/` and sets `photo` for you (`--force` re-downloads). Failures are listed and don't stop the others.
Some shops block automated downloads; for those, save the image by hand and set `photo` yourself. You are responsible for having the right to use the images you download.
