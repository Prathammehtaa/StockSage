"""Market Signal Agent (Layer 2): measures revealed behavior -- insider and
institutional activity -- plus dated factual events. Never sentiment, never
opinion. Reuses fetchers._ANALYST_OPINION_BLOCKLIST to filter company news
down to factual events, since get_news_sentiment's headlines aren't
pre-filtered the way get_sector_news's are.
"""

import asyncio

from agents.llm_utils import build_prompt, call_llm, parse_json_response, wrap_untrusted
from data import fetchers
from data.fetchers import _ANALYST_OPINION_BLOCKLIST
from memory import cognee_client

MODEL = "claude-haiku-4-5-20251001"


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
    # EDGAR/yfinance-backed fetchers can also raise outright for foreign/
    # ADR-only tickers (CompanyNotFoundError, "Quote not found for
    # symbol"). A missing data source for one company shouldn't crash the
    # whole concurrent Layer 2 fan-out -- the prompt's own "insufficient
    # data" handling covers a genuinely empty result.
    try:
        return await asyncio.to_thread(fn, ticker)
    except Exception:
        return default


def _filter_factual_events(news_items: list) -> list:
    events = []
    for item in news_items:
        haystack = f"{item.get('headline', '')} {item.get('summary', '')}".lower()
        if any(phrase in haystack for phrase in _ANALYST_OPINION_BLOCKLIST):
            continue
        events.append({"event": item.get("headline"), "date": item.get("datetime")})
    return events


async def run(run_id: str, company: dict) -> dict:
    ticker = company["ticker"]
    insider_trades = await _safe_fetch(fetchers.get_insider_trades, ticker, {})
    institutional_changes = await _safe_fetch(fetchers.get_institutional_changes, ticker, {})
    news_sentiment = await _safe_fetch(fetchers.get_news_sentiment, ticker, {})
    recent_factual_events = _filter_factual_events(news_sentiment.get("news", []))

    prompt = build_prompt(
        "market_signal_agent.txt",
        ticker=ticker,
        insider_trades_json=insider_trades,
        institutional_changes_json=institutional_changes,
        recent_factual_events_json=wrap_untrusted(recent_factual_events),
    )
    response_text = await call_llm(prompt, model=MODEL, max_tokens=2048)
    findings = parse_json_response(response_text)

    await cognee_client.write_company_findings(run_id, ticker, "layer2", "market_signal", findings)
    return findings
