---
name: qupick
description: "This skill uses quantum computers to pick the best crypto asset to pay with, given current market conditions."
compatibility: "Requires: (1) a running portfolio backend at http://127.0.0.1:8000; (2) Bitrefill MCP (https://api.bitrefill.com/mcp) or CLI available; (3) a local skills/qupick/config.json (see config.example.json). Delegates all purchase mechanics to the bitrefill skill."
metadata:
  author: hackathon
  version: "4.0.0"
---

# Pay with most suitable crypto asset in your portfolio

Identify the most suitable crypto in the portfolio (lowest annualised expected return) using a quantum unconstrained binary optimization, settle a Bitrefill product against the cheapest available funding source, then retune the portfolio only if the chosen crypto was actually sold.

Delegates all purchase mechanics to the [`bitrefill`](../bitrefill/SKILL.md) skill — read and invoke that skill for product search, pricing, buying, and payment polling. This skill adds portfolio seeding, selection logic, and an account-aware funding waterfall on top.

The flow is designed to stop for the user in **exactly one** place — the purchase approval (step 6). Defaults, a config file, and a permission allowlist remove the other interruptions.

## Calling conventions (curl)

The Claude Code permission allowlist scopes `curl` by **URL prefix**, and prefix matching only works if the URL is the first argument. **Always write the URL immediately after `curl`,** then flags:

```bash
curl http://127.0.0.1:8000/agents/{id}/market
curl http://127.0.0.1:8000/agents/{id}/optimize -X POST -H "Content-Type: application/json" -d '{...}'
curl https://api.bitrefill.com/v2/accounts/balance -H "Authorization: Bearer $BITREFILL_API_KEY"
```

Allowlisted (no prompt): the local backend (`http://127.0.0.1:8000/*`) and the read-only Bitrefill balance endpoint. **Not allowlisted** (will prompt): any other Bitrefill REST URL — in particular `POST /v2/invoices`. Real purchases go through the bitrefill skill's `buy-products`, which is also deliberately not allowlisted, so the approval gate in step 6 always fires.

## Backend API reference

Base URL: `http://127.0.0.1:8000`

All endpoints are JSON over HTTP. Error responses follow FastAPI's default shape:
`{"detail": [...]}` for validation errors (422) or `{"detail": "..."}` for app errors.

### POST /agents — create agent

**Request body** (`AgentConfig`, `name` + `sliders` required):
```json
{
  "name": "string",
  "email": "string | null",
  "handle": "string | null",
  "sliders": {
    "rebalanceFrequency": 50,
    "riskPreference": 50,
    "maxPositionSize": 50
  },
  "assets": ["BTC", "ETH", "..."]
}
```

`SliderValues` constraints — **all three fields are required**, each a number 0–100:
- `rebalanceFrequency` — how often the agent rebalances (0 = rarely, 100 = hourly max)
- `riskPreference` — risk-aversion term γ (0 = conservative, 100 = aggressive)
- `maxPositionSize` — per-asset weight cap (0 = equal-weight 1/n, 100 = up to ~50% in one asset)

Default sensible values when the user hasn't expressed a preference: `{"rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50}`.

**Response** (`SubmitAgentResponse`):
```json
{
  "agentId": "afae79c9",
  "qrUrl": "https://qtw-tradinggame.netlify.app/p/afae79c9",
  "bankroll": 10000.0
}
```

### GET /agents/{agent_id} — fetch agent config

**Response** (`AgentConfig`): same shape as the POST request body. Use this to retrieve the current asset basket when re-using an existing agent.

### POST /agents/{agent_id}/optimize — optimise (and optionally retune)

**Request body** (entirely optional — omit body for a plain re-optimise with no changes):
```json
{
  "sliders": { ... },
  "assets": ["BTC", "ETH", "..."]
}
```

Both `sliders` and `assets` are individually optional. If `assets` is provided the agent's basket is replaced atomically before solving — this is the retune path. A retune liquidates all existing holdings and reallocates over the new basket.

**Response** (`RoutingResult`):
```json
{
  "provider": "Gurobi",
  "providerType": "CPU",
  "solveTime": 0.0068,
  "vsClassical": 41.67,
  "portfolio": [
    {"ticker": "BNB", "pct": 29.55, "usd": 2954.55},
    {"ticker": "FIL", "pct": 29.55, "usd": 2954.55}
  ],
  "kind": "first | retune | null",
  "jobId": "7b49f676a6f5",
  "solvedAt": "2026-06-17T12:27:55.026750+00:00"
}
```

`providerType` is `"QPU"` or `"CPU"` — tells you whether a quantum solver was used.

### GET /agents/{agent_id}/market — live holdings + μ values

**Response** (`MarketResult`):
```json
{
  "agentId": "afae79c9",
  "assets": [
    {
      "ticker": "BTC",
      "name": "Bitcoin",
      "assetClass": "crypto",
      "mu": -0.002567,
      "units": 0.00370,
      "usd": 227.07
    }
  ]
}
```

`assetClass` is `"crypto"` or `"stock"`. `mu` is the annualised expected return — negative means the asset is expected to lose value.

### GET /leaderboard — hackathon scoreboard

Returns an array of `LeaderboardEntry`:
```json
[
  {
    "rank": 1,
    "agentId": "afae79c9",
    "name": "string",
    "handle": "string | null",
    "total": 10423.50,
    "plUSD": 423.50,
    "plPct": 4.235,
    "jobsSolved": 12,
    "primaryProvider": "QPU"
  }
]
```

## Bitrefill account balance (REST)

```bash
curl https://api.bitrefill.com/v2/accounts/balance -H "Authorization: Bearer $BITREFILL_API_KEY"
```

Returns the pre-funded account balances. The account can hold balances in more than one asset (e.g. USD, EUR, BTC). Read the response **generically** — do not hardcode a field layout; look for per-asset entries giving an asset/currency and an available amount. These balances are funding sources in the step-5 waterfall alongside on-chain wallet payment.

## Flow

### 0. Read config

Load `skills/qupick/config.json` (mirror of the committed `config.example.json`):

```json
{
  "agentId": "afae79c9",
  "defaults": {
    "name": "Konrad",
    "email": "konrad@postquant.xyz",
    "country": "US",
    "sliders": { "rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50 }
  },
  "funding": {
    "priority": ["account_match", "onchain_match", "account_fiat"],
    "fee_buffer_pct": 2,
    "on_shortfall": "reject"
  },
  "denomination": { "policy": "smallest_gte" },
  "backend": { "marketDataSource": "synthetic" }
}
```

- `agentId` — reuse this agent (skip creation). `null`/absent → create one in step 2 and **write the returned id back into `config.json`**.
- `defaults` — name / email / country / sliders, used only when creating a new agent and for product country.
- `funding.priority` — settlement order (see step 5). `funding.fee_buffer_pct` — coverage buffer (default 2). `funding.on_shortfall` — `reject` | `confirm`.
- `denomination.policy` — `smallest_gte` auto-picks the smallest package ≥ the requested amount.
- `backend.marketDataSource` — `MARKET_DATA_SOURCE` for the backend auto-start (step 2). Default `synthetic` (deterministic, offline). Absent → `synthetic`.

**If `config.json` is missing or malformed, do not crash.** Fall back to the fully-interactive behaviour: ask the user for name/email, ask which denomination, treat `funding.priority` as `["onchain_match"]` (on-chain only), and note that no config was found.

### 1. Determine available currencies (static map)

The set of cryptos the Bitrefill account can pay with is fixed. No live "list payment methods" endpoint exists. This map is the source of truth; per-product restrictions are confirmed live in step 5.

| Ticker | Bitrefill payment_method |
|--------|--------------------------|
| BTC    | bitcoin                  |
| ETH    | ethereum                 |
| BNB    | bnb                      |
| SOL    | solana                   |
| XRP    | ripple                   |
| USDT   | usdt                     |
| USDC   | usdc_base                |
| DOGE   | dogecoin                 |
| ZEC    | zcash                    |
| ALGO   | algorand                 |
| FIL    | filecoin                 |

Any portfolio asset whose ticker is not in this table cannot be spent on Bitrefill and is dropped silently.

### 2. Seed the agent (REST)

**Backend health check (offer to start).** Before the first backend call, probe it:

```bash
curl http://127.0.0.1:8000/leaderboard
```

If it answers (even `[]`), proceed. If the connection is refused, the backend is down — **offer to start it**, and on the user's yes, launch it backgrounded and poll until ready. Use `config.backend.marketDataSource` (default `synthetic`) for the data source:

```bash
env MARKET_DATA_SOURCE=synthetic uv run --directory backend uvicorn backend.api.app:app --workers 1 --port 8000
```

Run it as a background process, then poll `curl http://127.0.0.1:8000/leaderboard` every ~1s until it responds (cold start can take a few seconds; a first `503 no feasible solution ... before deadline` on a later optimise is normal — retry once). If the user declines, or the backend never comes up, stop with an actionable message and the manual command. Do not auto-start without the user's yes.

The default `synthetic` start command is allowlisted, so the user's yes is the only stop. A non-default `marketDataSource` produces a different command that the harness will prompt for once.

If `config.agentId` is set, fetch the current config to read the existing basket — skip creation:

```bash
curl http://127.0.0.1:8000/agents/{agentId}
```

If this returns **404 `agent not found`** (a stale id, or a freshly restarted in-memory backend), the configured agent no longer exists — fall through to the create path below **and overwrite `config.agentId`** with the new id. Treat 404 exactly like an absent id; do not stop.

If `config.agentId` is `null`/absent (or 404'd above), create one seeded over the available Bitrefill currencies, using `config.defaults`:

```bash
curl http://127.0.0.1:8000/agents -X POST -H "Content-Type: application/json" -d '{
  "name": "<config.defaults.name>",
  "email": "<config.defaults.email>",
  "sliders": {"rebalanceFrequency": 50, "riskPreference": 50, "maxPositionSize": 50},
  "assets": ["BTC", "ETH", "BNB", "SOL", "XRP", "USDT", "USDC", "DOGE", "ZEC", "ALGO", "FIL"]
}'
# → { "agentId": "<id>", "qrUrl": "...", "bankroll": 10000.0 }
```

**Write the returned `agentId` back into `config.json`.** Then optimise immediately (no body needed for first run):

```bash
curl http://127.0.0.1:8000/agents/{agentId}/optimize -X POST
```

`agentId` is used in every subsequent call.

### 3. Pick the product (MCP)

Use the bitrefill skill's `search-products` (with `country = config.defaults.country`) → `product-details` to settle on:
- Product name + country
- Price in USD (from the `packages` array — use the field `payment_price` with `payment_currency == "USD"`)
- Accepted `payment_methods` list (from `product-details` — the authoritative per-product filter)
- Denomination (`package_id`), selected by `config.denomination.policy`:
  - `smallest_gte` (default) — given a target amount, auto-select the smallest package whose value is ≥ the target. No user prompt.
  - if no policy/config — ask the user which denomination.

### 4. Fetch market (REST)

```bash
curl http://127.0.0.1:8000/agents/{agentId}/market
```

Returns the current USD value and μ for every asset in the basket.

### 5. Select the loser, then resolve the funding waterfall

Selection and settlement are **separate**. Selection always runs and is never bypassed by how the bill is paid.

**5a. Selection (always runs).** Build spendable crypto candidates — assets where **all** of:
- `assetClass == "crypto"`
- `units > 0` (actually held)
- `ticker ∈ PAYMENT_METHOD_MAP`
- `PAYMENT_METHOD_MAP[ticker]` appears in the product's `payment_methods` list (from step 3)

The **loser** is `min(μ)` across these candidates. If there are no spendable crypto candidates at all, **hard stop** — tell the user and do not silently substitute a stablecoin.

**5b. Settlement waterfall.** Let `price = denomination_price_usd × (1 + funding.fee_buffer_pct/100)`. Read live balances (`GET /accounts/balance`, see above). Walk `funding.priority` in order and take the **first source that covers the full `price`** — no invoice splitting (a Bitrefill invoice takes one payment method):

| Token | Source | Pays via | Sells loser? | Retune? |
|-------|--------|----------|--------------|---------|
| `account_match` | Bitrefill account balance held in the loser asset (e.g. account BTC) | `buy-products(payment_method:"balance", auto_pay:true)` | yes | yes |
| `onchain_match` | Wallet holdings of the loser asset (on-chain BTC) | `buy-products(payment_method:MAP[loser], return_payment_link:true)` → pay link → poll | yes | yes |
| `account_fiat` | Bitrefill USD/EUR account balance | `buy-products(payment_method:"balance", auto_pay:true)` | no | no |

- `account_match` / `account_fiat` coverage is checked against the **account balances** from `GET /accounts/balance` (the loser-asset balance for `account_match`; the fiat balances for `account_fiat`).
- `onchain_match` coverage is checked against the loser's **wallet holdings** (`usd` from step 4).
- Record which token was chosen — step 6 maps it to `buy-products` arguments and step 7 gates the retune on it.

**5c. Shortfall.** If no source in `funding.priority` covers the full `price`, apply `funding.on_shortfall`:
- `reject` (default) — stop with a clear message naming the gap (which sources were tried, how much each covered of `price`). No purchase.
- `confirm` — present the shortfall and wait for explicit user approval to proceed on-chain with the loser asset (legacy fallback). Only proceed on an explicit yes.

> **Distinguishing `account_match` from `account_fiat`.** Both pay via `payment_method:"balance"`. If `buy-products` / the balance API cannot direct the debit to a specific asset, treat a `balance` payment as `account_fiat` (**no retune**) unless Bitrefill reports the loser-asset balance was actually debited. Never retune on an unconfirmed crypto debit.

### 6. Confirm + buy (MCP) — the single human stop

Resolve the waterfall to one concrete source **before** showing this screen, so the user approves a fully-specified transaction:

```
Product:   [name] — [denomination]
Price:     $[price]  (incl. [fee_buffer_pct]% buffer)
Loser:     [TICKER] ([payment_method], μ=[mu])        ← always shown
Settle:    [chosen source]
             account_match  → Bitrefill account [TICKER] ($[avail]) · sells loser ✓ · will retune
             onchain_match  → On-chain [TICKER] (wallet $[usd])     · sells loser ✓ · will retune
             account_fiat   → Bitrefill [USD/EUR] balance ($[avail]) · no sale · portfolio unchanged
Approve?
```

After explicit approval, use the bitrefill skill to buy, mapping the chosen token to `buy-products` arguments:
- `account_match` / `account_fiat`: `buy-products(cart_items=[{product_id, package_id, quantity:1}], payment_method="balance", auto_pay=true)` → instant.
- `onchain_match`: `buy-products(cart_items=[{product_id, package_id, quantity:1}], payment_method=MAP[loser], return_payment_link=true)` → pay via the returned link → poll `get-invoice-by-id` until `status == "complete"`.

Then `get-order-by-id` for the redemption code / QR. Log: `invoice_id`, product, amount, chosen funding token, payment method.

### 7. Retune (REST) — only if the loser was sold

Retune **only** when settlement used `account_match` or `onchain_match` (the loser was actually sold). If settlement used `account_fiat`, **skip the retune** and report: "paid from fiat balance; portfolio unchanged."

When retuning, remove the spent ticker from the basket and re-optimise (pass only `assets`; omit `sliders` to keep existing values):

```bash
curl http://127.0.0.1:8000/agents/{agentId}/optimize -X POST -H "Content-Type: application/json" -d '{
  "assets": ["BTC", "BNB", "SOL", "..."]
}'
```

(The list is the current basket minus the spent ticker.) Report the new allocation: what was kept, what was dropped, new `portfolio` percentages.

## Worked example

> "Buy a $20 Steam gift card and dump my worst crypto"

1. **Config** — `config.json` has `agentId = afae79c9`, `funding.priority = ["account_match","onchain_match","account_fiat"]`, `denomination.policy = smallest_gte`.
2. **Currencies** — static map gives 11 tickers.
3. **Agent** — `agentId` present → `GET /agents/afae79c9` for the basket; skip creation.
4. **Product** — `search-products("Steam", country="US")` → `steam-usa`; `product-details` → `smallest_gte($20)` picks `package_id = steam-usa<&>20`, price $21.60, accepts `bitcoin`/`ethereum`/`solana`/`usdc_base`.
5. **Market** — `GET /agents/afae79c9/market` → BTC (μ=−0.0026, $227), ETH (μ=+0.0001, $227), SOL (μ=−0.0003, $227), USDC (μ=+0.00005, $2272).
6. **Select** — all four spendable + accepted. Worst μ = **BTC**. `price = 21.60 × 1.02 = $22.03`.
7. **Waterfall** — `GET /accounts/balance`: account BTC $60 (covers $22.03) → `account_match` wins. Sells the loser → will retune.
8. **Confirm** — "Steam USD $20 · loser BTC (μ=−0.0026) · settle Bitrefill account BTC ($60) · sells loser ✓ · will retune · Approve?"
9. **Buy** — `buy-products(..., payment_method="balance", auto_pay=true)` → instant → redemption code.
10. **Retune** — `POST /agents/afae79c9/optimize {"assets": ["ETH","BNB","SOL","XRP","USDT","USDC","DOGE","ZEC","ALGO","FIL"]}` (BTC dropped).

## Safeguards

This skill executes real-money purchases. See [`skills/bitrefill/references/safeguards.md`](../bitrefill/references/safeguards.md) for the full spending policy:
- Confirm before every purchase — step 6 is the single, non-negotiable approval stop. `buy-products` is deliberately **not** on the Claude Code allowlist, so the harness also prompts.
- Stop before `buy-products` unless the user opts into a real purchase (real money).
- Treat codes as cash — never log or paste redemption codes in public channels.
- Use a dedicated low-balance account. `config.json` (real `agentId` / email) is gitignored.
- Log every purchase: `invoice_id`, product, amount, funding token, method.

The retune in step 7 is irreversible — when it fires, the spent asset is removed from the basket permanently until the user re-adds it manually. It fires only when the loser was actually sold (`account_match` / `onchain_match`).
