# Connect your real Pinterest account (no password, no token pasting)

> **Not needed for the current MVP.** The app is in manual-publishing mode (`DIRECT_PUBLISHING_ENABLED=false`): you post drafts
> yourself and nothing here applies. Follow this guide only when you decide to enable direct publishing, then set
> `DIRECT_PUBLISHING_ENABLED=true` in `.env` in addition to the variables below.

You will register a small "app" with Pinterest, give this tool its **id/secret/redirect URI via `.env`**, and then approve
access once on Pinterest's own page. You never type your Pinterest password into this tool and you never paste a token anywhere.

## 1. Create a Pinterest developer app
1. Use a Pinterest **business** account (free to convert) and open <https://developers.pinterest.com/apps/>.
2. Create an app; request API access. New apps get **Trial access**: pins you publish are visible only to you, which is
   perfect for a first test. Public pins need **Standard access** (Pinterest reviews your app; it asks for a short demo video).
3. In the app settings add the redirect URI exactly as used below, e.g. `http://localhost:8000/pinterest/callback`
   (it must match character for character; if Pinterest refuses plain `http`, use an https tunnel URL and set the same value in `.env`).
4. Copy the **App id** and **App secret key**.

## 2. Configure `.env`
```
PINTEREST_PROVIDER=real
PINTEREST_CLIENT_ID=<app id>
PINTEREST_CLIENT_SECRET=<app secret key>
PINTEREST_REDIRECT_URI=http://localhost:8000/pinterest/callback
ADMIN_PASSWORD=<a strong password>
SECRET_KEY=<32+ random characters>      # python -c "import secrets;print(secrets.token_urlsafe(40))"
```
`.env` is git-ignored. Start: `python -m uvicorn app.main:app_factory --reload`.

## 3. Connect and test (the intended first run)
1. **Settings → Connect Pinterest** → Pinterest asks you to authorize → you land back on Settings showing
   `● Connected`, `@yourusername`, your boards and "can create pins".
2. Choose a **default board** (create a private test board on Pinterest first if you like).
3. **Products**: import one product with a real affiliate link (EarnKaro Profit Link CSV / manual add, or an Amazon ASIN with
   `AMAZON_PARTNER_TAG` set). *Demo* products cannot be published to a real account.
4. **Create** → generate 1 pin → open it → read the copy, **Approve**.
5. On the pin page pick the board → **Publish**. You should see `✓ Published successfully` and the **Pinterest Pin ID**.
   Open your board on Pinterest to see the pin (with Trial access only you can see it).
6. **Analytics → Sync now** later (Pinterest needs time to report) to pull Pinterest-reported metrics.

## If something goes wrong
| Symptom | Meaning / fix |
|---|---|
| "Set PINTEREST_CLIENT_ID…" | `.env` incomplete; restart the app after editing it |
| Pinterest says redirect URI mismatch | the URI in the app settings and `.env` differ |
| "authorization expired or revoked" banner | press **Reconnect Pinterest** in Settings, then **Retry** |
| "refused this action" (403) | scopes missing or app access level too low; reconnect and allow everything; check Trial/Standard |
| "rate limit" | wait; the pin is `failed` and can be retried |
| "Possible duplicate" | you already published this product/variation; **Publish anyway** or **Cancel** |
| Status `failed` + "outcome unknown" | Pinterest may have created the pin; check your board before retrying |
| Changed `SECRET_KEY` | stored token can't be decrypted; just reconnect |

**Disconnect** deletes the stored tokens and tries to revoke at Pinterest; you can also remove the app under Pinterest's
account settings. Scheduling stays off until you set `SCHEDULER_ENABLED=true` and run `python -m app.worker`.
