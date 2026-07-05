"""Fundamental Agent (Layer 2): tests whether a company's actual financial
data supports the specific thesis the Sector Agent proposed for it -- not
just whether the company looks generically healthy.
"""

import asyncio

from agents.llm_utils import build_prompt, call_llm, parse_json_response
from data import fetchers
from memory import cognee_client

MODEL = "claude-sonnet-5"


async def _safe_fetch(fn, ticker, default):
    # fetchers.py's functions are synchronous, real-network-I/O calls
    # (yfinance/EDGAR). Calling them directly from an async function blocks
    # the ENTIRE event loop for their full duration -- observed live: one
    # slow get_insider_trades call (EDGAR, up to ~20s+ per company) froze
    # every other concurrent agent/Cognee call too, not just this one,
    # turning what should be parallel Layer 2 work into a serialized chain.
    # asyncio.to_thread runs the blocking call on a worker thread so it
    # can't stall anything else.
    #
    # yfinance-backed fetchers can also 404 outright for foreign/ADR-only
    # tickers (e.g. "Quote not found for symbol"). A missing quote for one
    # company shouldn't crash the whole concurrent Layer 2 fan-out -- the
    # prompt's own "insufficient data" handling covers a genuinely empty result.
    try:
        return await asyncio.to_thread(fn, ticker)
    except Exception:
        return default


async def run(run_id: str, company: dict) -> dict:
    ticker = company["ticker"]
    financial_data = {
        "fundamentals": await _safe_fetch(fetchers.get_fundamentals, ticker, {}),
        "earnings_history": await _safe_fetch(fetchers.get_earnings_history, ticker, []),
    }

    prompt = build_prompt(
        "fundamental_agent.txt",
        ticker=ticker,
        thesis_json=company,
        financial_data_json=financial_data,
    )
    response_text = await call_llm(prompt, model=MODEL, max_tokens=4096)
    findings = parse_json_response(response_text)

    await cognee_client.write_company_findings(run_id, ticker, "layer2", "fundamental", findings)
    return findings
