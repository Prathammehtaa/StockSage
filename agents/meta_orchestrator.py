"""Meta-orchestrator: interprets a broad sector category + budget + horizon
into one specific, evidence-grounded sector brief, then drives the full
pipeline (Layer 1 -> Layer 2 -> Synthesis) with run-isolated Cognee memory.
"""

import uuid
from datetime import datetime, timezone

from agents.llm_utils import build_prompt, call_llm, parse_json_response, wrap_untrusted
from data import fetchers

MODEL = "claude-sonnet-5"


def _generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{timestamp}_{uuid.uuid4().hex[:6]}"


async def interpret_user_input(parent_category: str, budget_usd: float, horizon: str) -> dict:
    news = fetchers.get_sector_news(parent_category)
    prompt = build_prompt(
        "meta_orchestrator.txt",
        parent_category=parent_category,
        budget_usd=budget_usd,
        horizon=horizon,
        recent_sector_news_json=wrap_untrusted(news),
    )
    # 4096 was hit and truncated on a real run whose news/ticker-universe
    # output was larger than the earlier truncation (originally 2048) that
    # prompted the first bump -- output size genuinely varies with how much
    # real news is available, so give this real headroom.
    response_text = await call_llm(prompt, model=MODEL, max_tokens=8192)
    return parse_json_response(response_text)


async def run(user_input: dict) -> dict:
    from agents import layer_orchestrator, synthesis_agent
    from memory import cognee_client

    run_id = _generate_run_id()
    try:
        parent_categories = user_input["parent_categories"]
        budget_usd = user_input["budget_usd"]
        horizon = user_input["horizon"]

        sector_briefs = [
            await interpret_user_input(category, budget_usd, horizon) for category in parent_categories
        ]

        sector_results = await layer_orchestrator.run_layer1(run_id, sector_briefs)

        for sector_result in sector_results:
            await layer_orchestrator.run_layer2(run_id, sector_result)

        # Bounded settle step, once for the whole run -- not per write. See
        # cognee_client.settle_run's docstring for why: per-write
        # verify-and-retry was tried and measured to not reliably converge
        # while adding 100+ seconds of latency; this trades per-write
        # precision for a single fixed delay before Synthesis reads.
        await cognee_client.settle_run(run_id)

        report = await synthesis_agent.run(run_id, budget_usd, horizon)
        return {"run_id": run_id, "sector_briefs": sector_briefs, "sector_results": sector_results, "report": report}
    finally:
        await cognee_client.cleanup_run(run_id)
