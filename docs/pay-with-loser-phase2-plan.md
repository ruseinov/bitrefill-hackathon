# Phase 2 plan: agent-driven worst-loser → Bitrefill invoice

Phase 1 (`docs/pay-with-loser-plan.md`, shipped in `1d0b804`) added the
`GET /agents/{id}/market` endpoint and `skills/pay-with-loser/SKILL.md`. Phase 2 **enhances that
skill** so the agent itself orchestrates the full loop by hitting the portfolio **REST endpoints**
and the **Bitrefill MCP** — no standalone script. The agent: seeds a portfolio agent with the
currencies our Bitrefill account can pay in, picks the worst performer (preferring one that holds
enough value), and creates the invoice via the Bitrefill MCP.

## Context

- The skill already delegates purchase mechanics to the `bitrefill` skill (MCP) and reads
  `/market`. Two capabilities are missing vs. the user's intent: **(1) seeding** an agent from the
  Bitrefill-payable currencies, and **(2) "enough currency"** preference (task #1).
- **No live "list payment methods" endpoint** — `/v2/payment-methods`,
  `/v2/accounts/payment-methods`, `/v2/accounts` all 404. "Available currencies" = the skill's
  static `ticker→payment_method` table, confirmed per-product via MCP `product-details`.
- Account balance is `0 BTC`; crypto invoices are paid from an external wallet, not the balance.
- Prices come back in satoshis; USD value is the package `value`/`amount`.

## Changes to `skills/pay-with-loser/SKILL.md`

Rework the flow into these agent steps (REST = portfolio backend, MCP = Bitrefill):

1. **Determine available currencies** — the crypto our Bitrefill account can pay with = the static
   `PAYMENT_METHOD_MAP` tickers (BTC, ETH, BNB, SOL, XRP, USDT, USDC, DOGE, ZEC, ALGO, FIL) ∩ the
   tradable universe. Document that no live endpoint exists (probes 404'd), so this map is the
   source of truth, refined per-product in step 5.

2. **Seed the agent (REST)** — *new step, replaces "ask the user to run demo.py"*:
   - `POST http://127.0.0.1:8000/agents` with `{name, email, sliders, assets: <available currency
     tickers>}` → `agent_id`.
   - `POST /agents/{id}/optimize` → gives the agent holdings over those currencies.
   *(User step 1: "seed our agent with currencies available in our Bitrefill account.")*

3. **Pick the product (MCP)** — `search-products` → `product-details` → product, `package_id`,
   and **USD price**.

4. **Fetch market (REST)** — `GET /agents/{id}/market` → per-asset `mu`, `units`, `usd`,
   `assetClass`.

5. **Choose the worst loser, preferring enough currency (task #1)** — candidates where
   `assetClass=="crypto"`, `units>0`, `ticker ∈ PAYMENT_METHOD_MAP`, **and** the product is payable
   in that currency (confirm via MCP `product-details`). Prefer candidates whose `usd >=
   product_price_usd` (+ small fee buffer); among those pick **min(μ)**.
   - **Caveat (per user):** "enough funds" is a *preference, not a hard gate*. If **no** candidate
     holds enough, **fall back to the absolute worst loser** (min μ across spendable candidates) and
     **create the invoice with it anyway** — surface the shortfall and **let the user decide**
     whether to proceed/fund it. Only stop entirely if there are **no** spendable crypto candidates.

6. **Confirm + buy (MCP)** — present `product · denomination · $price · pay with TICKER
   (payment_method, μ=…)` and, when applicable, `⚠️ holdings only cover $X of $Y — proceed?`. Wait
   for explicit approval, then `buy-products(cart_items=[{product_id, package_id, quantity:1}],
   payment_method=MAP[worst], return_payment_link=true)` → pay via link → poll `get-invoice-by-id`
   to `complete` → `get-order-by-id` for the code. Log `invoice_id`, product, amount, method. No
   auto-pay.

7. **Retune (REST)** — `POST /agents/{id}/optimize` with `assets = currencies − spentTicker`;
   report kept/dropped.

Update the worked example and the static table notes accordingly; keep the safeguards section.

## Files
| File | Change |
|------|--------|
| `skills/pay-with-loser/SKILL.md` | add **Determine available currencies** + **Seed the agent (REST)** steps; rework step 5 to the enough-currency *preference* with the worst-loser fallback; clarify REST-vs-MCP per step |
| `AGENTS.md` | optional: note the seed + enough-currency behavior |

## Open items
- **Task #1** (created/updated): choose the worst loser, preferring enough currency, with the
  worst-loser-anyway fallback when funds fall short — define "enough" (notional portfolio `usd` vs.
  external wallet balance) when wording the skill.
- Dynamic discovery of Bitrefill-accepted currencies if an endpoint ever ships (today: static map).

## Verification (manual, agent-driven)
1. Start backend: `cd backend && MARKET_DATA_SOURCE=synthetic uvicorn backend.api.app:app --reload --workers 1`.
2. Drive the skill end-to-end: seed agent over the available currencies → optimize →
   `GET /agents/{id}/market` → pick a cheap product via MCP → confirm the chosen pay currency is
   the lowest-μ crypto whose `usd` covers the price (or the outright worst loser, with a shortfall
   warning, when none cover it).
3. Stop before `buy-products` unless the user opts into a real purchase (real money — defer to
   `skills/bitrefill/references/safeguards.md`).