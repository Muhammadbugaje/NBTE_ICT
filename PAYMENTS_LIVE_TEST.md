# Testing Remita and Paystack with real test keys

The in-app success simulator has been removed. Development uses a real provider only when its test
credentials are configured; otherwise online methods stay unavailable and bank transfer remains
the fallback. Production refuses to start when a gateway named in
`PAYMENTS_GATEWAYS` has no keys (v2.1 T-SEC-8; `manage.py check`/`migrate` also stop with
`payments.E001`/`E002` when a gateway is switched on in QA settings without keys).

When you later receive NBTE's verified (live) keys, you only replace the values in `.env` and the
base URL for Remita. Nothing else changes.

## 1. Put the keys in `.env` (never in the code, never in chat)

These are the exact names the code reads (`nbte_ict/settings/base.py`). `.env.example` lists them
all with comments.

```dotenv
# Which gateways this server uses (production fails fast if one of them has no keys)
PAYMENTS_GATEWAYS=remita,paystack

# --- Remita (Remita merchant / demo profile -> API credentials) ---
REMITA_BASE_URL=https://remitademo.net/remita       # TEST. Live: https://login.remita.net/remita
REMITA_API_PATH=/exapp/api/v1/send/api              # where Remita's JSON API sits under the base URL
REMITA_MERCHANT_ID=xxxxxxx                          # "Merchant ID"
REMITA_API_KEY=xxxx                                 # "API key"
REMITA_SERVICE_TYPE_ID=xxxxxxx                      # "Service type ID" (default for every fee; a fee can override it)
REMITA_API_TOKEN=                                   # only if your profile issues one
REMITA_WEBHOOK_ALLOWED_IPS=                         # optional: Remita's notification IPs, comma-separated
REMITA_WEBHOOK_TOKEN=change-me-long-random          # webhook secret, see step 3

# --- Paystack (Dashboard -> Settings -> API Keys & Webhooks, switch to "Test mode") ---
PAYSTACK_BASE_URL=https://api.paystack.co           # same URL for test and live; the key decides the mode
PAYSTACK_PUBLIC_KEY=pk_test_xxxxxxxxxxxxxxxxxxxxxxxx
PAYSTACK_SECRET_KEY=sk_test_xxxxxxxxxxxxxxxxxxxxxxxx  # also the webhook secret (Paystack signs with it)

# --- Links in callbacks and e-mails must use the public tunnel address while testing ---
QA_PORTAL_URL=https://<your-tunnel>.trycloudflare.com
# Keep the removed simulator disabled
QA_PAYMENTS_SIMULATE=False
```

Generate the Remita webhook token with
`python -c "import secrets; print(secrets.token_urlsafe(32))"`.

Optional: in **QA settings → Fees & bank accounts**, enter a different **Remita service type ID**
on a fee schedule when Remita issued a separate revenue item for it; otherwise
`REMITA_SERVICE_TYPE_ID` is used. Paystack needs no per-fee set-up.

Restart the server after editing `.env`.

## 2. Give the providers a public address (they cannot reach `localhost`)

Either tool works; the important part is that requests reach Django with the **portal host name**
(`qa.localhost` in development), because the portal is chosen by host name:

```bash
# Cloudflare quick tunnel (no account needed)
cloudflared tunnel --url http://localhost:8000 --http-host-header qa.localhost

# or ngrok
ngrok http 8000 --host-header=qa.localhost
```

Copy the `https://…` address it prints into `QA_PORTAL_URL` (step 1). `*.trycloudflare.com` and
`*.ngrok-free.app` are already allowed hosts in development.

## 3. Register the webhook addresses

| Provider | Where | Address |
|---|---|---|
| Paystack | Settings → API Keys & Webhooks → **Test** Webhook URL | `https://<tunnel>/payments/webhooks/paystack/` |
| Paystack | Test Callback URL | leave empty (the system sends its own callback per payment) |
| Remita | Merchant profile → Notification URL | `https://<tunnel>/payments/webhooks/remita/?token=<REMITA_WEBHOOK_TOKEN>` |

Paystack signs every webhook (`x-paystack-signature`, HMAC-SHA512 with your secret key); unsigned
or wrongly signed calls are rejected and logged. Remita does not sign, so the system checks the
`token` in the notification URL (when `REMITA_WEBHOOK_TOKEN` is set), optionally the caller's IP,
and **always** re-checks the payment with Remita's status API before recording anything.

## 4. The test, step by step

1. **Portal** (`https://<tunnel>/`): sign in as an institution (demo: `admin@demo-poly.local`).
   Open **Payments**, open an unpaid bill. It shows the breakdown, e.g. *Institution fee +
   Processing fee = Total* (the polytechnic processing fee is ₦1,000,000, to confirm).
2. **How will you pay?** Choose **Remita**, **Paystack** or **Bank transfer**. Only the chosen
   method's steps stay on the page; the others are hidden ("Change method" brings them back).
3. **Pay online**: Paystack — Paystack's published test card `4084 0840 8408 4081`, any future
   expiry, CVV `408`, PIN `0000`, OTP `123456`. Remita — *Continue to Remita* and pay the RRR on
   the demo checkout.
4. **The webhook arrives** → *QA → Finance (QA) → Webhook log* shows it with "signature valid" and
   the outcome. The system re-queries the provider; only a matching amount and currency is recorded
   as provider-verified evidence. The item goes **directly to the Director of Finance**; there is no
   first-pass officer. No receipt upload is asked for. If the amounts differ, Finance and ICT get
   an alert and the Director reviews the evidence. If no webhook arrives, returning from checkout
   or the 15-minute sweep (`qa_payments_sweep`) checks the payment.
5. **Bank transfer instead**: choose *Bank transfer*, download the slip, pay, press *I have paid*
   and upload the teller. The item also goes directly to the Director of Finance for a decision.
6. **Director of Finance** (`finance.director@demo.nbte.local`) is notified on every claim; open
   the item → **Confirm payment**. ICT monitors (`qa.ict_monitor@demo.nbte.local`) can press *Send
   reminder to the Director of Finance* once a day per item.
7. **Back on the portal**: the institution is taken to its dashboard with a welcome card and the next
   step; the bill shows the official receipt (QR, PAID stamp) to download. The application moves on
   by itself (signal `payments.signals.obligation_confirmed`).

A provider report or a webhook never confirms a bill on its own; the Director of Finance always
decides (fixed by policy).

## 5. Going live

1. Replace the test values in `.env` with NBTE's live keys (`pk_live_…`, `sk_live_…`, live Remita
   merchant ID / API key / service type ID) and set `REMITA_BASE_URL=https://login.remita.net/remita`.
2. Register the live webhook URLs (step 3, with the real portal address, e.g.
   `https://qa.nbte.gov.ng/payments/webhooks/paystack/`).
3. Restart. `python manage.py check --deploy` must report no `payments.E00x` error.

## 6. If something fails

| Symptom | Check |
|---|---|
| Server refuses to start: "Payment gateway keys missing" | A gateway in `PAYMENTS_GATEWAYS` has an empty key. |
| "Paystack refused the request (HTTP 401)" | The secret key is wrong or has spaces. |
| "The fee schedule has no Remita service type ID" | Set `REMITA_SERVICE_TYPE_ID` (or one on the fee). |
| Remita answers HTML or 404 | `REMITA_BASE_URL` / `REMITA_API_PATH` (demo vs live). |
| Webhook log shows "invalid signature" | Paystack: another account's secret key. Remita: the `?token=` in the notification URL differs from `REMITA_WEBHOOK_TOKEN`. |
| Item not "Provider verified" | The provider's amount/currency differs from the balance (see the alert); the Director can review the evidence directly. |
| No webhook at all | The tunnel is not running, or the dashboard has the wrong URL. Coming back from the checkout page checks the payment at once; otherwise the 15-minute sweep catches up. |
| The checkout returns to `localhost` | `QA_PORTAL_URL` still points to localhost. |
