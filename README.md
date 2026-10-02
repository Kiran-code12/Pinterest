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
