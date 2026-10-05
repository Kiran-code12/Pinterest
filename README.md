# AI Pinterest Affiliate Engine

A personal tool that turns affiliate products into **ready-to-post Pinterest drafts**: it finds the product, keeps the
affiliate link attached, writes the title and description, designs the pin image and saves everything in a **Draft
Library**. You download the image, copy the text and post it on Pinterest yourself.

```
FIND PRODUCT -> AFFILIATE LINK -> AI PIN GENERATION -> SAVED AS DRAFT -> Draft Library
   -> edit / regenerate -> download image -> copy title, description, affiliate URL
   -> YOU post it on Pinterest -> mark "Published manually"
```

**The app does not contact Pinterest.** `DIRECT_PUBLISHING_ENABLED` is `false` by default and, while it is off, the Pinterest
provider is a stub with no network code, so no request can be made (this is tested). The complete official-API integration
(OAuth, boards, publishing, scheduling, analytics sync) is built, tested and isolated; switch it on later, when your Pinterest
app has the access you need, with `DIRECT_PUBLISHING_ENABLED=true` (see `engine-docs/PINTEREST_SETUP.md`).

> **Status: working MVP (v0.2).** 181 automated tests pass and the whole draft workflow, including real file downloads and real
> clipboard copying, was driven in a headless browser. It has **not** been run against your real Amazon account
> (needs your credentials), see [what needs you](#what-needs-your-credentials).

## Quick start (Windows / macOS / Linux)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows      (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-app.txt
copy .env.example .env            # (macOS/Linux: cp)   then set ADMIN_PASSWORD
python -m uvicorn app.main:app_factory --reload
```
Open <http://localhost:8000> and log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`. The built-in **demo catalog** (fictional
products) lets you try the whole flow with no accounts; replace it with real products via CSV import / manual add (EarnKaro,
Amazon) or Amazon API search once you have credentials.

## The workflow in the UI

| Step | Where |
|---|---|
| 1. Find products ("Find 20 beauty products...") or import a CSV / add one by hand; select the good ones | **Products** |
| 2. Choose how many variations and which designs; pins are generated and **saved as drafts** | **Create** |
| 3. Browse drafts (search, filter by design, Drafts / Published / Archived tabs) | **Draft Library** |
| 4. Open a draft: edit text, **regenerate title & description**, **regenerate design**, change the design | draft page |
| 5. **Download** the image (PNG 1000x1500, or JPG), **copy title / description / affiliate URL** (or all three at once) | library card or draft page |
| 6. Post on Pinterest, then **Mark as published manually** (optional pin URL + board name); undo with *Move back to drafts* | draft page |
| 7. **Archive** (restorable) or **Delete** drafts; select several to download a ZIP (images + CSV) or archive in bulk | Draft Library |
| 8. Type in your Pinterest numbers per published pin; see your own rates calculated | **Analytics** |
| Edit the affiliate-disclosure wording that is added to descriptions | **Settings** |

Draft states: `draft -> published manually`, `archived` (from anywhere, restorable). Editing is locked once a pin is marked as
published or archived, so what you posted is what is recorded.

## What is built

* **Provider architecture**: `AffiliateProvider` with Amazon (Creators API), EarnKaro (import/manual links) and a demo catalog; one
  normalized `Product` model; a new network is one class + one line.
* **Affiliate link integrity**: product -> provider -> link -> pin URL is validated when a draft is created, edited and marked as
  published. A missing, changed or plain-product-URL destination is flagged and blocks the action; the URL on a pin is not editable.
* **AI copy with a fact-check**: free deterministic local writer by default; optional OpenAI / Anthropic. Every result is checked
  against the provider's product facts (no invented prices, numbers, ratings, medical or superlative claims) with a safe fallback.
* **5 pin designs** (2:3, 1000x1500): minimal card, beauty editorial, product collage, top picks, product spotlight.
* **Draft Library** with version history of every copy change.
* **Isolated Pinterest integration** (off by default): `PinterestProvider` (official API v5 + OAuth in `real.py`, simulation in
  `mock.py`, stub in `disabled.py`), encrypted tokens, boards, duplicate protection, scheduling, retry, analytics sync.
* **Security**: login + CSRF on every POST, strict CSP, rate limits, SSRF-safe image downloads, safe error pages, secrets only in
  env, CSV-formula-safe exports, files only ever read/deleted inside the app's assets folder.

## What needs your credentials

| Capability | You provide | Without it |
|---|---|---|
| Amazon product search / prices | `AMAZON_CREDENTIAL_ID`, `AMAZON_CREDENTIAL_SECRET`, `AMAZON_PARTNER_TAG` (Creators API; account must be eligible) | CSV import or manual add (links built with your `AMAZON_PARTNER_TAG`) |
| EarnKaro | nothing (no public API found) | create Profit Links in EarnKaro, import them (CSV / manual) |
| AI copy from OpenAI / Anthropic | `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` + `AI_PROVIDER` | the free local writer |
| *Later:* direct publishing | Pinterest app id/secret/redirect URI + `DIRECT_PUBLISHING_ENABLED=true` | manual posting (the current MVP) |

## Tests and checks

```bash
pip install -r requirements-dev.txt
ruff check app tests_app          # lint
python -m pytest tests_app -q     # 181 tests (no network, no real credentials needed)
node scripts/ui_smoke.js          # optional: headless-browser run of the draft workflow against a running app (see script header)
```

## Documentation

* `engine-docs/ARCHITECTURE.md`: design, data model, flows, **implementation status, limitations, next steps**
* `engine-docs/INTEGRATIONS.md`: what I found about the Amazon / EarnKaro / Pinterest APIs (with sources and what is unverified)
* `engine-docs/PINTEREST_SETUP.md`: enabling direct publishing later

## Repository layout

```
app/                FastAPI app                tests_app/    pytest suite
  providers/        Amazon, EarnKaro, demo     engine-docs/  documentation
  services/         products, scoring, AI fact-check, 5 templates, pins, drafts (library), analytics, publishing
  pinterest/        isolated: real / mock / disabled provider, OAuth connection, token encryption, typed errors
  ai/               local / OpenAI / Anthropic copy writers
  web/              HTML pages + JSON API      scripts/      browser smoke test
pinbot/ content/ data/ tests/     the earlier static-site + pin-queue tool (still works, see below)
```

---

# Earlier tool: static affiliate site + pin queue (`pinbot/`)

Kept working and unchanged (it deploys the GitHub Pages site via `.github/workflows/daily.yml`). Its tests run with
`python -m unittest discover tests`; it needs only `requirements.txt` (Pillow). Its notes follow.


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
