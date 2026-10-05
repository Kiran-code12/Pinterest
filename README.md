# AI Pinterest Affiliate Engine

A personal tool that turns affiliate products into reviewed, approved Pinterest pins and publishes them to **your own
Pinterest account through the official Pinterest API** (OAuth, no passwords, no scraping, no browser bots).

```
discover products -> affiliate link -> AI copy -> pin designs -> YOU review/edit -> YOU approve -> publish (or schedule) -> analytics
```

Nothing is ever published without your approval. Everything runs locally for ₹0 (SQLite, Pillow, no paid services required).

> **Status: working MVP (v0.1).** 159 automated tests pass and the whole journey was driven in a real headless browser.
> It has **not** been run against your real Amazon / Pinterest accounts (that needs your credentials): see
> [what needs you](#what-needs-your-credentials) and `engine-docs/PINTEREST_SETUP.md`.

## Quick start (Windows / macOS / Linux)

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows      (macOS/Linux: source .venv/bin/activate)
pip install -r requirements-app.txt
copy .env.example .env            # (macOS/Linux: cp)   then edit ADMIN_PASSWORD, and SECRET_KEY if you like
python -m uvicorn app.main:app_factory --factory --reload
```
Open <http://localhost:8000>, log in with `ADMIN_USERNAME` / `ADMIN_PASSWORD`.

**Try everything with no accounts at all:** set `PINTEREST_PROVIDER=mock` in `.env`. The demo catalog (fictional products)
plus a simulated Pinterest lets you walk the complete flow: discover → create → approve → connect → publish. A red
"SIMULATION MODE" banner shows whenever it is active, and nothing is sent anywhere.

**Use it for real:** follow `engine-docs/PINTEREST_SETUP.md` (set `PINTEREST_PROVIDER=real`, add your Pinterest app's id,
secret and redirect URI, restart, press *Connect Pinterest*).

## The workflow in the UI

| Step | Where |
|---|---|
| 1. Discover (“Find 20 beauty products…”) or import a CSV / add a product by hand | **Products** |
| 2. Select products (each shows provider → original URL → affiliate URL and a content-opportunity score) | **Products** |
| 3. Choose how many variations and which designs; copy + images are generated | **Create** |
| 4. Preview, **Edit**, **Regenerate**, **Approve** (or Reject) | **Pins → pin** |
| 5. Choose a board (or use the default) and press **Publish** (retry on failure) | **Pins → pin**, **Queue** |
| 6. See Pinterest-reported metrics vs numbers this app calculates | **Analytics** |
| Connect / disconnect Pinterest, pick default board, edit the disclosure wording | **Settings** |

Pin states: `draft → ready for review → approved → scheduled → publishing → published` (or `failed` → Retry, or `rejected`).

## What is built

* **Provider architecture** – `AffiliateProvider` with Amazon (Creators API), EarnKaro (import/manual links) and a demo catalog;
  one normalized `Product` model; add a network = one class + one line.
* **Safety rails** – the affiliate chain *product → provider → link → pin destination* is validated at creation, edit,
  approval, scheduling and publishing; a missing/changed/plain-URL destination **blocks** publishing.
* **AI copy with a fact-check** – deterministic local writer by default (free, offline); optional OpenAI / Anthropic.
  Every AI result is checked against the provider's product facts (no invented prices, numbers, ratings, medical or
  superlative claims); failures fall back to deterministic copy.
* **5 pin designs** (1000×1500): minimal card, beauty editorial, product collage, top picks, product spotlight.
* **Pinterest integration behind one interface** – `PinterestProvider` (`real.py` = official API v5 + OAuth, `mock.py` = simulation).
  Encrypted token storage, automatic refresh, reconnect prompts, board list + default board, per-pin board.
* **Publishing** – one `publish_pin()` used by the Publish button, Retry and the scheduler; duplicate protection
  (with *Publish anyway*); atomic claim so a double click cannot publish twice; failures stored with reason, time and attempts.
* **Scheduling** – 3 slots/day (configurable), runs only if you enable `SCHEDULER_ENABLED=true` and start `python -m app.worker`.
* **Security** – login + CSRF on every POST, strict CSP, rate limits, SSRF-safe image downloads, safe error pages,
  secrets only in env, tokens encrypted and never logged/shown.

## What needs your credentials

| Capability | You provide | Without it |
|---|---|---|
| Publish to Pinterest | Pinterest developer app: `PINTEREST_CLIENT_ID`, `PINTEREST_CLIENT_SECRET`, `PINTEREST_REDIRECT_URI`, then press **Connect** | Export a ZIP (image + text) or use `PINTEREST_PROVIDER=mock` |
| Amazon product search / prices | `AMAZON_CREDENTIAL_ID`, `AMAZON_CREDENTIAL_SECRET`, `AMAZON_PARTNER_TAG` (Creators API; your Associates account must be eligible) | CSV import or manual add (links built with your `AMAZON_PARTNER_TAG`) |
| EarnKaro | nothing (no public API found) | create Profit Links in EarnKaro, import them (CSV / manual) |
| AI copy from OpenAI / Anthropic | `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` + `AI_PROVIDER` | the free local writer |

## Tests and checks

```bash
pip install -r requirements-dev.txt
ruff check app tests_app          # lint
python -m pytest tests_app -q     # 159 tests (no network, no real credentials needed)
node scripts/ui_smoke.js          # optional: headless-browser run against a running mock-mode app (see the script header)
```

## Documentation

* `engine-docs/ARCHITECTURE.md` – design, data model, flows, **implementation status, limitations, next steps**
* `engine-docs/INTEGRATIONS.md` – what I found about the Amazon / EarnKaro / Pinterest APIs (with sources and what is unverified)
* `engine-docs/PINTEREST_SETUP.md` – connect your real Pinterest account, step by step

## Repository layout

```
app/                FastAPI app (new)         tests_app/    pytest suite for it
  providers/        Amazon, EarnKaro, demo    engine-docs/  documentation
  pinterest/        PinterestProvider real+mock, OAuth connection, token encryption, typed errors
  services/         products, scoring, pins, templates, publishing, analytics, grounding
  ai/               local / OpenAI / Anthropic copy writers
  web/              HTML pages + JSON API     scripts/      UI smoke test
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
