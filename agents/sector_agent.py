"""Sector Agent (Layer 1): traces a mega-project's real supply chain using
live web search plus USASpending confirmation, tagging every candidate
CONFIRMED or INFERRED. Never assigns DIRECT_RECIPIENT -- that's added by the
Phase 4 orchestrator (layer_orchestrator) when it fans out Layer 2.

prompts/sector_agent.txt's Input section only exposes a single
{{sector_brief_json}} placeholder even though its own methodology text
references get_recent_filings / get_federal_contract_awards /
get_federal_subawards as available inputs. Rather than editing the
finalized prompt, this agent pre-fetches all three and folds them into the
sector_brief dict before serializing it into that one placeholder -- the
prompt's own instructions on how to use that data still apply.
"""

import json

from agents.llm_utils import build_prompt, call_llm, parse_json_response, wrap_untrusted
from data import fetchers
from memory import cognee_client

MODEL = "claude-sonnet-5"
WEB_SEARCH_TOOL = {"type": "web_search_20250305", "name": "web_search"}


def _safe_recent_filings(ticker: str) -> list:
    # get_recent_filings is EDGAR-only (US SEC filers) -- non-US tickers
    # (e.g. LSE-listed "BA.L", ADR-only symbols) raise CompanyNotFoundError.
    # A missing US filing history isn't fatal here: the agent still has web
    # search and USASpending to work with.
    try:
        return fetchers.get_recent_filings(ticker)
    except Exception:
        return []


async def run(run_id: str, sector_brief: dict) -> dict:
    filings_by_ticker = {
        candidate["ticker"]: _safe_recent_filings(candidate["ticker"])
        for candidate in sector_brief.get("starting_ticker_universe", [])
        if candidate.get("ticker")
    }

    keyword = sector_brief.get("catalyst_hypothesis") or sector_brief.get("sector_name", "")
    contract_awards = fetchers.get_federal_contract_awards(keyword)
    subawards = fetchers.get_federal_subawards(keyword)

    # sector_brief itself is the Meta-orchestrator's own synthesized output;
    # recent_filings/contract_awards/subawards are raw externally-fetched
    # content (SEC filing text, USASpending award descriptions) and get
    # wrapped separately rather than merged into one dict and dumped, so the
    # untrusted-content tag boundary is unambiguous in the final prompt text.
    sector_brief_json = json.dumps(sector_brief, default=str) + "\n\nAdditional pre-fetched research data:\n" + wrap_untrusted(
        {
            "recent_filings": filings_by_ticker,
            "federal_contract_awards": contract_awards,
            "federal_subawards": subawards,
        }
    )

    prompt = build_prompt("sector_agent.txt", sector_brief_json=sector_brief_json)
    response_text = await call_llm(prompt, model=MODEL, max_tokens=16000, tools=[WEB_SEARCH_TOOL])
    findings = parse_json_response(response_text)

    await cognee_client.write_sector_context(run_id, findings["sector_name"], findings)
    return findings
