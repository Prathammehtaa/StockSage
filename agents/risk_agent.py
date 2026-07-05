"""Risk Agent (Layer 2): adversarial by design -- finds reasons NOT to invest,
using recent 8-K filings plus live web search for risk events the filings
don't capture (lawsuits, regulatory actions, executive departures, etc.).
"""

import asyncio

from agents.llm_utils import build_prompt, call_llm, parse_json_response, wrap_untrusted
from data import fetchers
from memory import cognee_client

MODEL = "claude-haiku-4-5-20251001"
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}


async def run(run_id: str, company: dict) -> dict:
    ticker = company["ticker"]
    try:
        # get_recent_filings is synchronous, real EDGAR network I/O -- run
        # on a worker thread so a slow EDGAR response can't block the whole
        # event loop (and every other concurrent agent/Cognee call) while
        # it's in flight. See fundamental_agent.py's _safe_fetch for the
        # live symptom this fixes.
        filings = await asyncio.to_thread(fetchers.get_recent_filings, ticker)
    except Exception:
        # EDGAR-only -- non-US tickers (LSE-listed, ADR-only symbols) raise
        # CompanyNotFoundError. Missing US filing history isn't fatal here:
        # the agent still has web search to find real risk events.
        filings = []

    prompt = build_prompt("risk_agent.txt", ticker=ticker, filings_json=wrap_untrusted(filings))
    response_text = await call_llm(prompt, model=MODEL, max_tokens=4096, tools=[WEB_SEARCH_TOOL])
    findings = parse_json_response(response_text)

    await cognee_client.write_company_findings(run_id, ticker, "layer2", "risk", findings)
    return findings
