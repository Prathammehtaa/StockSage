"""Fans out Layer 1 (Sector Agent, one per sector) and Layer 2 (Fundamental /
Market Signal / Risk agents, one trio per candidate company) with run-scoped
Cognee memory and a concurrency limit on the Layer 2 fan-out.
"""

import asyncio

from agents import fundamental_agent, market_signal_agent, risk_agent, sector_agent

LAYER2_CONCURRENCY = 2

_NOT_A_REAL_TICKER_PREFIXES = ("n/a", "none", "tbd", "unknown", "null")


def _is_real_ticker(value) -> bool:
    # The Sector Agent doesn't always return a bare "N/A" for a multi-recipient
    # or no-single-owner project -- it can return a full explanatory sentence
    # ("N/A -- multi-recipient budget plan..."). Real tickers never contain
    # whitespace, so that's a more robust signal than exact-matching a sentinel set.
    if not value:
        return False
    value = value.strip()
    if not value or " " in value:
        return False
    return not value.lower().startswith(_NOT_A_REAL_TICKER_PREFIXES)


async def run_layer1(run_id: str, sector_briefs: list) -> list:
    return await asyncio.gather(*(sector_agent.run(run_id, brief) for brief in sector_briefs))


async def run_layer2(run_id: str, sector_result: dict) -> None:
    companies = list(sector_result.get("candidate_companies", []))

    prime_project = sector_result.get("prime_project", {})
    recipient_ticker = prime_project.get("recipient_ticker")
    if _is_real_ticker(recipient_ticker):
        companies.append(
            {
                "ticker": recipient_ticker,
                "company_name": prime_project.get("recipient", ""),
                "exposure_type": "DIRECT_RECIPIENT",
                "evidence": prime_project.get("description", ""),
                "contract_layer": "direct recipient / project owner",
                "why_positioned": (
                    "Direct recipient/owner of the underlying project driving this "
                    "sector's thesis; included for baseline context, likely already "
                    "priced in rather than a new discovery."
                ),
                "specific_angle": "",
                "estimated_order_to_revenue_lag": "",
            }
        )

    semaphore = asyncio.Semaphore(LAYER2_CONCURRENCY)

    async def _run_company(company: dict) -> None:
        async with semaphore:
            await asyncio.gather(
                fundamental_agent.run(run_id, company),
                market_signal_agent.run(run_id, company),
                risk_agent.run(run_id, company),
            )

    await asyncio.gather(*(_run_company(company) for company in companies))
