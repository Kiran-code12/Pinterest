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
