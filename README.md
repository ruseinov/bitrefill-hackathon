# qupick — usage

Pay for a Bitrefill product (gift card, top-up, eSIM) with the **worst-performing crypto** in your
portfolio — the asset with the lowest expected return μ — then retune the portfolio without it.

The agent logic lives in [`SKILL.md`](skills/qupick/SKILL.md); this README is the operator's quick-start.
Purchase mechanics are delegated to the sibling [`bitrefill`](skills/bitrefill/SKILL.md) skill.

## Prerequisites

- The **portfolio backend** running locally on `http://127.0.0.1:8000` (see below).
- The **Bitrefill MCP** connected (`https://api.bitrefill.com/mcp`, OAuth or API key), or the
  Bitrefill REST API key — used for product search, balance reads, and invoice creation.
- A funding source the waterfall can draw on: a pre-funded **Bitrefill account balance** (USD, EUR,
  and/or the loser asset) and/or a funded on-chain wallet for the loser asset. The funding order is
  configurable (see [Configure](#configure)).
- A **`skills/qupick/config.json`** — copy `skills/qupick/config.example.json` and fill it in. Without
  it the skill still runs, but fully interactively (it asks for name, denomination, and pays on-chain
  only).

## Install the skill

Claude Code discovers skills under `.claude/skills/`. Because `qupick` links the bitrefill
skill via the relative path `../bitrefill/SKILL.md`, install **both** as siblings:

```bash
mkdir -p .claude/skills
cp -R skills/qupick .claude/skills/
cp -R skills/bitrefill     .claude/skills/
```

`.claude/` is gitignored — this is a local install, not committed.

## Configure

Copy the example config and edit it:

```bash
cp skills/qupick/config.example.json skills/qupick/config.json
```

`config.json` is gitignored (it holds your real `agentId` / email). Fields:

| Field | Purpose |
|-------|---------|
| `agentId` | Reuse an existing portfolio agent. `null` → the skill creates one and writes the id back. |
| `defaults` | `name` / `email` / `country` / `sliders` used when creating a new agent. |
| `funding.priority` | Settlement order. Default `["account_match", "onchain_match", "account_fiat"]`. |
| `funding.fee_buffer_pct` | Coverage buffer over the sticker price (default `2`). |
| `funding.on_shortfall` | `reject` (stop) or `confirm` (warn and ask) when nothing covers the price. |
| `denomination.policy` | `smallest_gte` auto-picks the smallest package ≥ the requested amount. |
| `backend.marketDataSource` | `MARKET_DATA_SOURCE` used when the skill auto-starts the backend. Default `synthetic` (offline, deterministic). |

**Funding waterfall.** The worst performer (`min(μ)`) is *always* computed. The bill is then settled by
the first source in `funding.priority` that covers the price:

- `account_match` — Bitrefill account balance held in the loser asset → sells the loser → **retunes**.
- `onchain_match` — on-chain wallet holdings of the loser asset → sells the loser → **retunes**.
- `account_fiat` — Bitrefill USD/EUR balance → settles without selling crypto → **no retune**.

Reorder or drop tokens to change behaviour — e.g. `["account_fiat", "account_match", "onchain_match"]`
to spend fiat first, or drop `account_fiat` to only ever sell crypto.

This config plus the permission allowlist in `.claude/settings.local.json` make a run stop in **exactly
one** place — the purchase approval.

## Run the backend

The skill **health-checks the backend** at the start of a run and, if it's down, **offers to start it**
for you (backgrounded, with `MARKET_DATA_SOURCE = backend.marketDataSource`, polling until ready). The
default `synthetic` start command is allowlisted, so a "yes" is the only stop. To run it yourself instead:

```bash
cd backend
MARKET_DATA_SOURCE=synthetic uv run uvicorn backend.api.app:app --workers 1 --port 8000
```

Wait until `GET http://127.0.0.1:8000/leaderboard` responds (it returns `[]` on a fresh start).

> First-solve cold start: the very first `optimize` call can return
> `503 no feasible solution ... before deadline` while the D-Wave/Gurobi libs warm up. Just retry
> once — subsequent solves are sub-10ms.

## Use it

Ask the agent in natural language, e.g.:

> "Buy a $20 Steam gift card and pay with my worst-performing crypto."

The skill then runs the flow from `SKILL.md`:

0. **Read config** — `skills/qupick/config.json` (agent id, defaults, funding order). Missing/malformed
   → fully interactive fallback, no crash.
1. **Available currencies** — static map of Bitrefill-payable crypto (BTC, ETH, BNB, SOL, XRP, USDT,
   USDC, DOGE, ZEC, ALGO, FIL).
2. **Health-check + seed agent (REST)** — probe the backend; if down, offer to start it. Then reuse
   `config.agentId` via `GET /agents/{id}`, or `POST /agents` from `config.defaults` then
   `POST /agents/{id}/optimize` (and write the new id back to the config).
3. **Pick product (MCP)** — `search-products` → `product-details` for price + accepted
   `payment_methods`; `denomination.policy` auto-selects the package.
4. **Market (REST)** — `GET /agents/{id}/market` for per-asset μ, units, USD value.
5. **Select + fund** — compute the worst performer (`min(μ)`) over held, product-accepted crypto, then
   read `GET /accounts/balance` and resolve `funding.priority` to the first source covering the price.
6. **Confirm + buy (MCP)** — the agent **stops for your explicit approval** at a fully-resolved
   screen (loser + chosen funding source), then buys: instant `balance` pay, or an on-chain link it
   polls to `complete`. Surfaces the redemption code.
7. **Retune (REST)** — `POST /agents/{id}/optimize` with the basket minus the spent ticker — **only**
   when the loser was actually sold (`account_match` / `onchain_match`). Fiat settlement leaves the
   portfolio unchanged.

### Example

```
/market ranked by μ (worst first):
  BTC   crypto   μ=-0.002567   $230.67   ← worst performer (always selected)
  SOL   crypto   μ=-0.000341   $225.07
  USDC  crypto   μ=+0.000046   $2272.70
  ETH   crypto   μ=+0.000129   $229.61

Product:  Steam USD $20 ($21.60, +2% buffer → $22.03) · accepts bitcoin/ethereum/solana/usdc_base
Loser:    BTC (μ=-0.0026)
Waterfall: account_match → Bitrefill account BTC $60 covers $22.03 ✓
Settle:   Bitrefill account BTC · sells loser ✓ · will retune
Buy:      payment_method="balance", auto_pay=true → complete → redemption code
Retune:   drop BTC, re-optimize over the remaining 10 currencies
```

## Safeguards (real money)

- The agent **never buys without explicit approval** — it always pauses at step 6.
- Codes deliver instantly and are **non-refundable**; treat redemption codes as cash and redeem ASAP.
- Use a dedicated, low-balance wallet. Full policy: [`safeguards.md`](skills/bitrefill/references/safeguards.md).
- The step-7 retune is irreversible — the spent asset leaves the basket until you re-add it.