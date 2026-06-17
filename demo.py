"""Demo: create an agent, optimize, find the worst-performing asset, sell it.

Run with the backend already up:
    MARKET_DATA_SOURCE=synthetic uvicorn backend.api.app:app --reload --workers 1

Then in another terminal:
    cd backend && uv run python ../demo.py
"""

from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# We import backend internals directly to get per-asset μ (expected return).
# The HTTP API has no /market endpoint, so the cleanest path is to call the
# same estimators the CLI uses.
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend", "src"))
os.environ.setdefault("MARKET_DATA_SOURCE", "synthetic")

import httpx

from backend import config
from backend.financial.estimators.expected_return import expected_return
from backend.financial.prices.source import get_source

BASE = "http://127.0.0.1:8000"

BASKET = ["BTC", "ETH", "IONQ", "QBTS", "GOOGL"]
SLIDERS = {"rebalanceFrequency": 50, "riskPreference": 70, "maxPositionSize": 50}

SEP = "─" * 56


def section(title: str) -> None:
    print(f"\n{SEP}\n  {title}\n{SEP}")


def fmt_portfolio(portfolio: list[dict]) -> None:
    print(f"  {'ticker':<8}{'weight':>9}{'usd':>14}")
    for p in portfolio:
        print(f"  {p['ticker']:<8}{p['pct']:>8.2f}%{p['usd']:>14,.2f}")
    total_pct = sum(p["pct"] for p in portfolio)
    total_usd = sum(p["usd"] for p in portfolio)
    print(f"  {'total':<8}{total_pct:>8.2f}%{total_usd:>14,.2f}")


# ---------------------------------------------------------------------------
# Step 1: Create agent
# ---------------------------------------------------------------------------
section("1 · Create agent")
with httpx.Client(base_url=BASE) as client:
    r = client.post(
        "/agents",
        json={"name": "Demo", "email": "demo@example.com", "sliders": SLIDERS, "assets": BASKET},
    )
    r.raise_for_status()
    agent = r.json()

agent_id = agent["agentId"]
bankroll = agent["bankroll"]
print(f"  agent_id  {agent_id}")
print(f"  bankroll  ${bankroll:,.0f}")
print(f"  basket    {', '.join(BASKET)}")

# ---------------------------------------------------------------------------
# Step 2: Initial optimization
# ---------------------------------------------------------------------------
section("2 · Initial optimization")
with httpx.Client(base_url=BASE, timeout=30) as client:
    r = client.post(f"/agents/{agent_id}/optimize", json={})
    r.raise_for_status()
    result = r.json()

print(f"  solver    {result['provider']} ({result['providerType']})")
print(f"  time      {result['solveTime'] * 1000:.1f} ms")
print(f"  kind      {result['kind']}")
print()
initial_portfolio = result["portfolio"]
fmt_portfolio(initial_portfolio)

# Record initial USD allocation per ticker.
initial_usd: dict[str, float] = {p["ticker"]: p["usd"] for p in initial_portfolio}

# ---------------------------------------------------------------------------
# Step 3: Wait for prices to drift, then find the worst loser
# ---------------------------------------------------------------------------
section("3 · Finding the worst loser (via expected-return μ)")

# Use the same estimator the optimizer uses: annualised hourly expected return.
# The asset in our basket with the most negative μ is the one losing the most.
source = get_source()
tickers_in_portfolio = [p["ticker"] for p in initial_portfolio]
returns = source.hourly_returns(tickers_in_portfolio, config.SIGMA_WINDOW_HOURS)
mu = expected_return(returns, config.MU_WINDOW_HOURS)

mu_by_ticker = dict(zip(tickers_in_portfolio, mu.tolist()))
print(f"  {'ticker':<8}{'μ/hr':>12}  {'initial usd':>14}")
for ticker in tickers_in_portfolio:
    print(f"  {ticker:<8}{mu_by_ticker[ticker]:>12.6f}  ${initial_usd[ticker]:>13,.2f}")

worst_ticker = min(mu_by_ticker, key=mu_by_ticker.__getitem__)
print(f"\n  worst loser → {worst_ticker}  (μ = {mu_by_ticker[worst_ticker]:.6f})")

# ---------------------------------------------------------------------------
# Step 4: Retune without the loser
# ---------------------------------------------------------------------------
section(f"4 · Selling {worst_ticker} — retune without it")

new_basket = [t for t in tickers_in_portfolio if t != worst_ticker]
print(f"  new basket  {', '.join(new_basket)}")
print()

with httpx.Client(base_url=BASE, timeout=30) as client:
    r = client.post(
        f"/agents/{agent_id}/optimize",
        json={"sliders": SLIDERS, "assets": new_basket},
    )
    r.raise_for_status()
    retune_result = r.json()

print(f"  solver    {retune_result['provider']} ({retune_result['providerType']})")
print(f"  time      {retune_result['solveTime'] * 1000:.1f} ms")
print(f"  kind      {retune_result['kind']}")
print()
fmt_portfolio(retune_result["portfolio"])

# ---------------------------------------------------------------------------
# Step 5: Summary
# ---------------------------------------------------------------------------
section("5 · Summary")
old_tickers = {p["ticker"] for p in initial_portfolio}
new_tickers = {p["ticker"] for p in retune_result["portfolio"]}
sold = sorted(old_tickers - new_tickers)
kept = sorted(new_tickers)
print(f"  sold      {', '.join(sold) or '(none)'}")
print(f"  kept      {', '.join(kept)}")
print()
print(f"  Check the leaderboard: {BASE}/leaderboard")
print(f"  Swagger UI:             {BASE}/docs")
