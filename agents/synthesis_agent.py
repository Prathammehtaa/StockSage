"""Synthesis Agent (Layer 3): reads every sector and company finding written
during this run, reconciles them, and produces the final markdown report.
Output is markdown, not JSON -- do not run it through parse_json_response.
"""

from agents.llm_utils import build_prompt, call_llm
from memory import cognee_client

MODEL = "claude-sonnet-5"


async def run(run_id: str, budget_usd: float, horizon: str) -> str:
    all_findings = await cognee_client.read_all_findings(run_id)
    prompt = build_prompt(
        "synthesis_agent.txt",
        all_findings_json=all_findings,
        budget_usd=budget_usd,
        horizon=horizon,
    )
    return await call_llm(prompt, model=MODEL, max_tokens=16000)
