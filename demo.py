"""Zero-cost "sample report" demo path -- replays the real progress-display
experience using ONLY the locked-in fixture (tests/fixtures/sample_report.md).
No network calls, no agents, no Cognee. Real catalyst/company/sector names
come from parsing the fixture itself, so this can never drift from what a
real run actually produced, and stays in sync if the fixture is ever
replaced with a newer real example.
"""

import os

from report.pdf_generator import generate_pdf, parse_report

_FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "tests", "fixtures", "sample_report.md")

# Real phase durations from an actual run were roughly: Meta-orchestrator
# ~20-30s, Sector Agent (web search) ~150-250s, Layer 2 (companies)
# ~150-700s, settle ~10-45s, Synthesis ~30-60s -- i.e. company analysis and
# supply-chain tracing dominate. These weights are compressed proportionally
# into a ~20-30s demo rather than evenly spaced, so the pacing still *feels*
# like the real thing.
_RESEARCH_SECONDS = 1.5
_CATALYST_SECONDS = 1.0
_TRACING_SECONDS = 5.5
_OPPORTUNITY_SECONDS = 1.0
_COMPANY_ANALYSIS_TOTAL_SECONDS = 10.0
_SETTLE_SECONDS = 0.8
_SYNTHESIS_SECONDS = 2.5
_READY_SECONDS = 0.5


def _load_fixture_text() -> str:
    with open(_FIXTURE_PATH, encoding="utf-8") as f:
        return f.read()


def _build_timeline_steps(markdown_text: str) -> list[tuple[str, float]]:
    blocks = parse_report(markdown_text)
    opportunity = next((b for b in blocks if b["type"] == "sector_opportunity"), None)
    companies = [b for b in blocks if b["type"] == "company"]

    steps = [("Researching the sector — pulling recent news and framing a specific catalyst", _RESEARCH_SECONDS)]

    if opportunity:
        catalyst = next((value for name, value in opportunity["fields"] if name.lower() == "the opportunity"), None)
        if catalyst:
            summary = catalyst if len(catalyst) <= 100 else catalyst[:97] + "..."
            steps.append((f"Catalyst confirmed: {summary}", _CATALYST_SECONDS))

    steps.append(("Tracing the supply chain — web search, SEC filings, federal award records", _TRACING_SECONDS))

    if opportunity:
        steps.append((f"Sector opportunity identified: {opportunity['sector_name']}", _OPPORTUNITY_SECONDS))

    if companies:
        per_company = max(_COMPANY_ANALYSIS_TOTAL_SECONDS / len(companies), 0.6)
        for company in companies:
            steps.append((f"Analyzing {company['ticker']} — {company['company_name']}", per_company))
    else:
        steps.append(("Analyzing candidate companies — fundamentals, market signal, risk", _COMPANY_ANALYSIS_TOTAL_SECONDS))

    steps.append(("Finalizing the memory graph before synthesis", _SETTLE_SECONDS))
    steps.append(("Writing the synthesis report", _SYNTHESIS_SECONDS))
    steps.append(("Report ready", _READY_SECONDS))
    return steps


def get_demo_timeline() -> tuple[str, list]:
    """Returns (fixture_markdown_text, [(step_label, duration_seconds), ...])
    for the caller to animate. Pure/no side effects beyond a local file read."""
    text = _load_fixture_text()
    return text, _build_timeline_steps(text)


def generate_demo_report(run_metadata: dict, output_path: str) -> str:
    """Renders the fixture to a PDF via the real, local pdf_generator.py --
    zero cost, no network calls of any kind."""
    text = _load_fixture_text()
    return generate_pdf(text, run_metadata, output_path)
