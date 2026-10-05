# Integrations research (what is real, what is not)

Researched on 2026-10-05 from public documentation and search summaries. The official documentation sites were not
reachable from the build environment, so details below are marked **[doc]** (stated by documentation excerpts) or
**[unverified]** (my inference; check on first live use).

## Amazon Associates India
* **PA-API 5.0 has been retired** (deprecated 30 Apr 2026, retired 15 May 2026) **[doc]**. Its successor is the **Creators API**
  with OAuth 2.0 (Credential ID + Secret) instead of AWS-signed requests **[doc]**. Your old `AMAZON_ACCESS_KEY` /
  `AMAZON_SECRET_KEY` therefore do not apply; use `AMAZON_CREDENTIAL_ID` / `AMAZON_CREDENTIAL_SECRET`.
* Token: POST JSON `{grant_type: client_credentials, client_id, client_secret, scope: "creatorsapi::default"}` to the Login with
  Amazon endpoint; India = credential version 3.2 -> `https://api.amazon.co.uk/auth/o2/token` **[doc]**.
* Calls: `POST https://creatorsapi.amazon/catalog/v1/searchItems|getItems`, headers `Authorization: Bearer …` and
  `x-marketplace: www.amazon.in`; body keys lowerCamelCase (`keywords`, `partnerTag`, `partnerType`, `resources`, `itemIds`) **[doc]**.
* Response paths (`searchResult.items[].asin`, `detailPageURL`, `images.primary.large.url`, `itemInfo.title.displayValue`,
  `offersV2.listings[].price.money.amount`) and resource names **[unverified]**: the adapter reads them defensively.
* Credentials are issued in Associates Central to eligible accounts **[doc]**; eligibility rules **[unverified]**: check yours.
* Without credentials: CSV import / manual add; the app builds `https://www.amazon.in/dp/<ASIN>?tag=<your tag>` (a normal
  tagged Associates link) and rejects Amazon links without your tag.
* No page scraping anywhere.

## EarnKaro
No officially documented public API, product feed or Profit-Link generation endpoint was found (EarnKaro advertises its app,
website tools and a "Magic Tool"). Therefore **no EarnKaro endpoint is called** and nothing automates its dashboard/app.
`EarnKaroProvider` is import/manual only and validates that a link is on an EarnKaro domain. If EarnKaro publishes an
official API, implement `search_products` / `get_affiliate_link` in that class; nothing else changes. Ask EarnKaro support
whether a publisher API exists for your account.

## Pinterest API v5 (official) **[doc]** unless noted
* Authorize: `https://www.pinterest.com/oauth/` (`client_id`, `redirect_uri`, `response_type=code`, `scope`, `state`).
* Token / refresh: `POST https://api.pinterest.com/v5/oauth/token`, HTTP Basic `client_id:client_secret`, form body.
* Revoke: `POST /v5/oauth/token/revoke`. A community report says it failed for some apps **[unverified]**, so the app
  always deletes local tokens and tells you if revocation was not confirmed (you can also remove the app in Pinterest settings).
* Account `GET /user_account`; boards `GET /boards`; create `POST /pins` (`board_id`, `title`≤100, `description`, `link`,
  `alt_text`, `media_source` = `image_base64`/`image_url`); analytics `GET /pins/{id}/analytics` (`metric_types`).
* Scopes: `boards:read`, `boards:write`, `pins:read`, `pins:write`, `user_accounts:read`.
* New apps have **Trial access** (pins visible only to you, ~1,000 calls/day); **Standard access** is granted after review
  (video demo) and makes pins public.
* The create-pin response documents `id`/`link`; a pin URL is not documented, so none is invented.
