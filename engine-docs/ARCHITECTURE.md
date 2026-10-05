# Architecture and status

> **Current MVP = manual publishing.** Pins are generated and stored as **drafts**; you download the image, copy the text and post
> on Pinterest yourself. `DIRECT_PUBLISHING_ENABLED=false` (default) installs `DisabledPinterestProvider` (no network code) so no
> Pinterest request can occur. The direct-publishing machinery described below is complete, tested and dormant.

## Stack and why
Python 3.11+, FastAPI (HTML pages and JSON API), SQLAlchemy 2 + SQLite (zero-cost; swap `DATABASE_URL` for Postgres
later), Jinja2 templates (no JS build step), Pillow for pin images, httpx for all outbound HTTP, `cryptography`
(Fernet) for token encryption. No paid infrastructure. The earlier `pinbot/` tool is untouched and independent.

## Layers
```
web/ (pages.py, api.py, deps.py)   thin: auth, CSRF, rate limit, forms -> services
services/                          all business rules (products, scoring, content, grounding, pins, templates,
                                   publishing, analytics, app_settings, imagery)
providers/                         AffiliateProvider: amazon.py, earnkaro.py, demo.py, registry.py
pinterest/                         PinterestProvider (provider.py) -> real.py | mock.py ; connection.py ; crypto.py ; errors.py
ai/                                AIProvider: local.py | remote.py (OpenAI, Anthropic) ; factory.py
publishers/export.py               ZIP export fallback (image + copy-paste text)
models.py / db.py / config.py / security.py / logging_setup.py
```
No Pinterest HTTP call exists outside `pinterest/real.py`; no affiliate HTTP call outside `providers/amazon.py`.

## Data model (`app/models.py`)
`users` · `affiliate_providers` · `products` (+`product_sources` raw import audit) · `affiliate_links` (one per product+provider:
original URL, affiliate URL, validity) · `pin_templates` · `pins` (**`affiliate_link_id` NOT NULL** + `destination_url` snapshot)
· `pin_assets` (every rendered PNG with sha256) · `pin_variations` (copy version history) · `publishing_queue`
(status, attempts, error code/message, ambiguity flag) · `published_pins` (Pinterest pin id, board, title, description,
affiliate URL, asset, provider, simulated flag) · `analytics` (source = `pinterest` or `manual`, raw payload) ·
`pinterest_connections` (account, **encrypted** tokens, scopes, default board, status) · `pinterest_boards` (cache) ·
`app_settings` (disclosure text; no secrets).

## Draft workflow (`services/drafts.py`, `web/pages.py`)
`create_pins` always leaves pins in status `draft`. The Draft Library (`/drafts`) lists by view (drafts / published / archived /
all) with search, design filter and paging. Per draft: edit text, regenerate copy, regenerate/change design (all create new
`pin_variations` / `pin_assets`, nothing is overwritten), `final_texts()` (the exact title, description incl. the configured
disclosure, affiliate URL and alt text to paste), PNG/JPG download, ZIP+CSV export (CSV cells are formula-neutralised),
`mark_published_manually` (validates the affiliate chain and the optional pin URL, stores a `published_pins` row with
`mode=manual`), `move_back_to_draft`, `archive`/`restore` (remembers the previous status), `delete` (removes DB rows, then files,
only inside the assets folder; refuses pins published through the API). Statuses: `draft`, `published_manually`, `archived`
(+ the dormant direct-publishing states `approved`, `scheduled`, `publishing`, `published`, `failed`, `rejected`). Existing
databases get new columns automatically (`Database.add_missing_columns`).

## Key flows
**Affiliate link integrity.** `check_destination(pin)` verifies: link exists, flagged valid, pin URL == link URL, not the plain
product URL, valid http(s), link belongs to the product. It runs on create, edit, approve, schedule, export and publish.
The pin destination is not user-editable; changing the product's link resets unpublished pins to *ready for review*.

**Copy generation.** `AIProvider.generate_pin_copy(facts, concept)` receives only provider-supplied facts. `grounding.validate_copy`
rejects unverified claims, numbers/prices not in the facts, budget wording without a verified low price, over-length text.
On failure the pin gets deterministic local copy and the reasons are shown as warnings.

**Publishing (`services/publishing.py::publish_pin`).** preflight blockers (approved? destination intact? connected with
`pins:write`+`boards:read`? board chosen? image present? demo pin on a real account?) → duplicate findings (needs *Publish
anyway*) → atomic `UPDATE ... SET status='publishing' WHERE status IN (approved, scheduled, failed)` → `create_pin` through
`ConnectionService.call` (one transparent token refresh on 401) → success: `published_pins` row + status `published`;
any error: status `failed` + code, message, attempt count, timestamp. Ambiguous failures (timeout/502-504 after sending)
are flagged so Retry asks for confirmation instead of risking a duplicate.

**OAuth.** *Connect* (POST, CSRF) stores a random `state` in the session and redirects to Pinterest. The callback verifies
`state` in constant time, exchanges the code (HTTP Basic client auth, server side), reads the account and boards, and stores
the tokens Fernet-encrypted (key derived from `SECRET_KEY`). Tokens refresh automatically 5 min before expiry; a failed refresh
marks the connection `expired` and the UI asks to reconnect. *Disconnect* attempts revocation, then always deletes local tokens.

**Scheduler.** `enqueue` assigns the next free slot (≤ `MAX_PINS_PER_DAY` per local day) or a chosen time. `process_due`
calls the same `publish_pin`; it never overrides duplicate protection, stops on reconnect-needed, honours the daily cap.
`python -m app.worker` loops only when `SCHEDULER_ENABLED=true`; `--once` and the Queue page's button run a pass on demand.

## Implementation status (v0.1)
| Area | Status |
|---|---|
| Foundation, DB, auth/CSRF/CSP/rate limits, logging redaction | done, tested |
| Providers: demo (full), EarnKaro import, Amazon import + Creators API adapter | done; **Amazon API untested against live account** |
| Discovery, dedupe, scoring (labelled heuristic), CSV import, manual add | done, tested |
| AI: local (default), OpenAI, Anthropic via official REST, fact-check + fallback | done; remote providers tested with mocked HTTP only |
| 5 pin templates, preview, edit, regenerate, approve/reject, version history | done, tested, viewed in browser |
| Pinterest OAuth, account, boards, default/per-pin board, publish, retry, duplicates, revoke | done; **real API tested only via mocked HTTP** |
| Mock Pinterest provider incl. expired token, revoked, rate limit, invalid board/image/url, outages | done |
| Queue page, scheduling, worker, daily cap | done (automatic publishing is opt-in) |
| Analytics: official endpoint sync, manual entry, Pinterest-reported vs calculated, simple insights | done |
| Draft Library, manual publishing records, archive/restore/delete, downloads, ZIP+CSV export, copy buttons | done, tested incl. real-browser run |
| Export ZIP fallback | done |

## Known limitations
* The copy buttons use the browser clipboard API (works on `localhost`/https) with a fallback for other contexts.
* "Published manually" is a record you create: the app cannot verify that the pin really exists on Pinterest.
* **Nothing has touched your real accounts.** The real Pinterest and Amazon code paths follow public documentation and are
  covered with mocked HTTP; expect small adjustments on first live contact (field names, error texts). See INTEGRATIONS.md.
* Pinterest apps start with *Trial* access (pins are visible only to you); *Standard* access needs Pinterest's review.
* Pinterest's create-pin response is not documented to include a pin URL, so **View Pin** appears only if the API returns one.
* Pinterest may treat shortened/redirect affiliate links (e.g. `ekaro.in`) less favourably than links to your own pages. The
  legacy site tool (`pinbot/`) offers the landing-page approach; a "destination = my landing page" mode is a next step.
* Amazon prices are only drawn on images while fresh (< 24 h); Amazon image/price caching rules are your responsibility.
* Single admin user; single process (rate limiter is in-memory); SQLite; no schema migrations yet (`create_all`).
* Product images must come from provider APIs/imports or your own files; there is deliberately no page scraping.
* No OAuth PKCE (Pinterest's docs describe the standard authorization-code flow; `state` is used against CSRF).

## Next steps
1. Run the real Pinterest connection (guide) and fix any live-API surprises; apply for Standard access if needed.
2. Amazon Creators API credentials → verify field paths; add price-refresh job.
3. Alembic migrations; Postgres option; multi-user.
4. Landing-page destination mode (own pages carrying the disclosure, linking to the affiliate URL).
5. Board sections, pin scheduling by Pinterest itself where the API supports it, CSV bulk export.
6. Learning loop: use synced analytics to rank concepts/templates/keywords and bias future generation.
