# AGENTS.md

Guidance for coding agents working in this repository.

## Skills

### Bitrefill

qupick's purchase layer is the **Bitrefill MCP** (`https://api.bitrefill.com/mcp`) — the `mcp__bitrefill__*` tools (`search-products`, `product-details`, `buy-products`, `get-invoice-by-id`). That MCP is the only hard dependency, and nothing from Bitrefill is vendored here. Connect it separately:

```bash
claude mcp add --transport http bitrefill https://api.bitrefill.com/mcp --scope user
```

Optionally, the upstream **bitrefill plugin** bundles the same MCP plus the bitrefill *skill* (capability routing, CLI/browse/REST fallbacks, safeguards prose) — `/plugin install bitrefill@bitrefill-skills` (<https://github.com/bitrefill/agents>). qupick uses only the MCP tools, not the skill's mechanics.

- **Enum/endpoint source of truth:** <https://docs.bitrefill.com>
- **Real money:** codes deliver instantly and are non-refundable. Confirm product, price, and payment method before buying; use a dedicated low-balance account; never expose high-balance accounts or wallet seeds.

### Qupick

Vendored at [`skills/qupick/SKILL.md`](skills/qupick/SKILL.md). Delegates purchase mechanics to the Bitrefill MCP above and adds portfolio selection on top.

**Triggers:** "pay with my worst performer", "use my worst crypto to buy X".

**Requires:** the **qupick MCP server** — the portfolio backend served at `http://127.0.0.1:8000/mcp`, exposing `mcp__qupick__*` tools. The backend must be up when the session starts for the tools to register; if they are missing the skill offers to start it backgrounded with `MARKET_DATA_SOURCE = config.backend.marketDataSource` (default `synthetic`) and then has the user reconnect MCP (`/mcp`). Also a local `skills/qupick/config.json` (copy of the committed `config.example.json`; gitignored because it holds the real email), and the agent's API key pasted **directly** into the `.mcp.json` `Authorization: Bearer` header (emailed at registration; Claude Code does not expand `${VAR}` in `.mcp.json` headers, so a literal key is required). Without the config the skill falls back to fully-interactive, on-chain-only behaviour.

**Selection vs settlement.** The skill always computes the worst performer — `min(μ)` over held crypto that Bitrefill accepts (`mcp__qupick__get_market`, static `PAYMENT_METHOD_MAP`). Selection is never bypassed by funding. It then resolves `config.funding.priority` against live balances (`GET /accounts/balance`) and on-chain holdings, settling against the first source that covers `price × (1 + fee_buffer_pct/100)`:

- `account_match` — Bitrefill account balance in the worst-performing asset → sells it → **retune**.
- `onchain_match` — on-chain wallet holdings of the worst-performing asset → sells it → **retune**.
- `account_fiat` — Bitrefill USD/EUR balance → no sale → **no retune**.

On shortfall (`funding.on_shortfall`): `reject` stops; `confirm` warns and waits. Retune (drop the spent asset, re-optimize) fires **only** when the worst performer was actually sold.

**Single human stop.** The flow is built to pause in exactly one place — the purchase approval. `mcp__bitrefill__buy-products` is deliberately kept off the `.claude/settings.local.json` allowlist. The six `mcp__qupick__*` tools are allowlisted (none spend real money), and the only `curl` is the read-only `/v2/accounts/balance` endpoint (write the URL first so prefix matching works). A purchase via `curl POST /v2/invoices` is **not** allowlisted and still prompts.

**Agent (re)use:** seeds over the Bitrefill-payable currencies (BTC, ETH, BNB, SOL, XRP, USDT, USDC, DOGE, ZEC, ALGO, FIL) via `mcp__qupick__register_agent` + `mcp__qupick__optimize`, or re-uses the existing agent — `get_agent` succeeding (with the key configured in the `.mcp.json` Bearer header) means skip creation.

#### `mcp__qupick__get_market`

Returns per-asset expected return (μ) and current holdings for the authenticated agent (no id echoed — the API key identifies the caller):

```json
{
  "assets": [
    {"ticker": "BTC", "name": "Bitcoin", "assetClass": "crypto", "mu": 0.0012, "units": 0.05, "usd": 3200.0},
    {"ticker": "ETH", "name": "Ethereum", "assetClass": "crypto", "mu": -0.0003, "units": 1.2, "usd": 3600.0}
  ]
}
```

Pre-solve: falls back to the agent's configured basket with `units=0`. Post-solve: reflects actual holdings. μ is the annualised hourly expected return over the last 7 days (`MU_WINDOW_HOURS=168`).