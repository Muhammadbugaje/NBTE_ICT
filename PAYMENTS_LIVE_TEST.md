# Testing Remita and Paystack with real test keys

Until keys are set, development uses the built-in simulator (`QA_PAYMENTS_SIMULATE=True` in
`settings/dev.py`, `payments/providers/simulator.py`). The moment a provider's keys are present in
`.env`, that provider talks to the real API and the simulator is switched off for it. Production
never simulates (`settings/prod.py`).

## 1. Put the keys in `.env` (never in the code, never in chat)

```dotenv
# --- Paystack (Dashboard → Settings → API Keys & Webhooks, "Test" mode) ---
PAYSTACK_PUBLIC_KEY=pk_test_xxxxxxxxxxxxxxxxxxxxxxxx
PAYSTACK_SECRET_KEY=sk_test_xxxxxxxxxxxxxxxxxxxxxxxx
# PAYSTACK_BASE_URL=https://api.paystack.co        # same URL for test and live; the key decides

# --- Remita (Remita merchant/demo profile → API credentials) ---
REMITA_MERCHANT_ID=xxxxxxx
REMITA_API_KEY=xxxx
REMITA_API_TOKEN=                                   # only if your profile issues one
REMITA_BASE_URL=https://remitademo.net/remita       # demo; live is https://login.remita.net/remita
REMITA_API_PATH=/exapp/api/v1/send/api              # where Remita's JSON API sits under the base URL
REMITA_WEBHOOK_ALLOWED_IPS=                         # optional: Remita's notification IPs, comma-separated

# --- Links in callbacks and e-mails must use the public tunnel address while testing ---
QA_PORTAL_URL=https://<your-tunnel>.trycloudflare.com
```

Then, in **QA settings → Fees & bank accounts**, enter the **Remita service type ID** on each fee
schedule (Remita issues it for each revenue item). Paystack needs no per-fee set-up.

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
| Paystack | Settings → API Keys & Webhooks → Test Webhook URL | `https://<tunnel>/payments/webhooks/paystack/` |
| Paystack | Test Callback URL | leave empty (the system sends its own callback per payment) |
| Remita | Merchant profile → Notification URL | `https://<tunnel>/payments/webhooks/remita/` |

Paystack signs every webhook (`x-paystack-signature`, HMAC-SHA512 with your secret key); unsigned
or wrongly signed calls are rejected and logged. Remita does not sign; the system therefore always
re-checks the payment with Remita's status API before recording anything, and can restrict callers
with `REMITA_WEBHOOK_ALLOWED_IPS`.

## 4. The test, step by step

1. **Portal** (`https://<tunnel>/`): sign in as an institution (demo: `admin@demo-poly.local`).
   Open **Payments**, open an unpaid bill.
2. **Print the slip** (it carries the payment reference with check digits, the RRR when Remita is
   used, the NBTE account and a QR verification code).
3. **Pay online**: choose Paystack (test card `4084 0840 8408 4081`, any future expiry, CVV `408`,
   PIN `0000`, OTP `123456` — Paystack's published test card) or Remita (pay the RRR on Remita's
   demo checkout).
4. **The webhook arrives** → *QA → Finance (QA) → Webhook log* shows it with "signature valid" and
   the outcome. The bill becomes *Provider reported paid (evidence only)* and enters the Finance queue.
   If no webhook arrives, the 15-minute payments sweep (`qa_payments_sweep`) polls the provider and
   catches up.
5. **Bank transfer instead**: on the bill, choose *I have paid — upload proof* and upload the teller.
6. **Finance officer** (`qa.finance@demo.nbte.local`): *Verification queue* → open the item →
   first pass *Recommend confirm*.
7. **Director of Finance** (`finance.director@demo.nbte.local`): same item → **Confirm payment**.
8. **Back on the portal**: the bill shows *Payment confirmed* and the **Payment Advice** is ready to
   preview, download and print. The process that was waiting for the fee moves on by itself.

A provider report or a webhook never confirms a bill on its own; that rule is fixed by policy.

## 5. If something fails

| Symptom | Check |
|---|---|
| "Paystack refused the request (HTTP 401)" | The secret key is wrong or has spaces. |
| "The fee schedule has no Remita service type ID" | Step 1, QA settings → Fees. |
| Remita answers HTML or 404 | `REMITA_BASE_URL` / `REMITA_API_PATH` (demo vs live). |
| Webhook log shows "invalid signature" | The webhook was sent with another account's secret key. |
| No webhook at all | The tunnel is not running, or the dashboard has the wrong URL. Coming back from the checkout page checks the payment at once; otherwise the 15-minute sweep catches up. |
| The checkout returns to `localhost` | `QA_PORTAL_URL` still points to localhost. |
