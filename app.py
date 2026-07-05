"""StockSage Streamlit UI -- the input form, progress display, and report
preview. All research/reasoning lives in agents/; this file just wires the
pipeline phases to a synchronous UI.

Two paths:
- "View a Sample Report": demo.py replays a real, previously-generated
  example (tests/fixtures/sample_report.md) with zero network calls.
- "Generate a New Report": the real pipeline below, gated behind its own
  explicit button so it never runs by accident.

Streamlit callbacks are synchronous but the real pipeline is async. Rather
than inject a progress callback into run_layer1/run_layer2's internal
asyncio.gather() calls (real complexity for what a demo needs today), each
pipeline phase is called directly, in sequence, from one wrapper coroutine
run via a single asyncio.run() -- the status label is updated between
phases, not during them.
"""

import asyncio
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone

import streamlit as st

from demo import generate_demo_report, get_demo_timeline
from report import html_preview

SECTOR_OPTIONS = [
    "Defense",
    "Semiconductor",
    "Nuclear Energy",
    "Oil and Gas",
    "Infrastructure",
    "Technology",
]
HORIZON_OPTIONS = ["6 months", "1 year", "2 years"]

# Fixed phase skeleton matching _run_pipeline's real, always-in-this-order
# status.update() calls -- lets the real flow use the same upcoming/active/
# completed timeline component as the demo, even though (unlike the demo)
# the real flow doesn't know exact label text or per-company counts until
# each phase actually starts.
_REAL_PIPELINE_PHASE_COUNT = 6


def _generate_run_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    return f"{timestamp}_{uuid.uuid4().hex[:6]}"


class _TimelineStatus:
    """Adapts real-pipeline status.update(label=..., state=...) calls onto
    the custom HTML timeline component, so the real and demo paths share one
    visual progress UI. Not exercised in the visual-design pass this was
    built in (zero live API calls that turn), but kept correctly wired for
    when "Generate a New Report" is actually run.
    """

    def __init__(self, container):
        self._container = container
        self._labels = ["Waiting to start..."] * _REAL_PIPELINE_PHASE_COUNT
        self._index = -1
        self._errored = False
        self._render()

    def _render(self):
        active = len(self._labels) if self._errored else self._index
        self._container.markdown(html_preview.render_timeline(self._labels, active), unsafe_allow_html=True)

    def update(self, label: str, state: str | None = None):
        self._index += 1
        if self._index < len(self._labels):
            self._labels[self._index] = label
        else:
            self._labels.append(label)
        if state == "error":
            self._errored = True
        self._render()
        if state == "complete":
            self._index = len(self._labels)
            self._render()


async def _run_pipeline(parent_category: str, budget_usd: float, horizon: str, status) -> tuple[str, str]:
    """Runs Meta-orchestrator -> Layer 1 -> Layer 2 -> settle -> Synthesis,
    one phase at a time, updating `status`'s label between phases. Always
    tears down this run's Cognee memory in `finally`, success or failure --
    skipping that would reintroduce the cross-run contamination Phase 2 was
    built specifically to prevent.
    """
    from agents import layer_orchestrator, meta_orchestrator, synthesis_agent
    from memory import cognee_client

    run_id = _generate_run_id()
    try:
        status.update(label="Researching the sector -- pulling recent news and framing a specific catalyst...")
        sector_brief = await meta_orchestrator.interpret_user_input(parent_category, budget_usd, horizon)

        status.update(label="Tracing the supply chain -- web search, SEC filings, and federal award records...")
        sector_results = await layer_orchestrator.run_layer1(run_id, [sector_brief])
        sector_result = sector_results[0]

        candidate_count = len(sector_result.get("candidate_companies", []))
        status.update(
            label=(
                f"Analyzing {candidate_count} candidate companies (plus the project's direct recipient, "
                "if one was identified) -- fundamentals, market signal, and risk..."
            )
        )
        await layer_orchestrator.run_layer2(run_id, sector_result)

        status.update(label="Finalizing the memory graph before synthesis...")
        await cognee_client.settle_run(run_id)

        status.update(label="Writing the synthesis report...")
        report_markdown = await synthesis_agent.run(run_id, budget_usd, horizon)

        status.update(label="Report ready.", state="complete")
        return run_id, report_markdown
    finally:
        await cognee_client.cleanup_run(run_id)


def _run_demo_flow():
    fixture_text, steps = get_demo_timeline()
    labels = [label for label, _ in steps]
    timeline_container = st.empty()

    for i, (label, duration) in enumerate(steps):
        timeline_container.markdown(html_preview.render_timeline(labels, i), unsafe_allow_html=True)
        time.sleep(duration)
    timeline_container.markdown(html_preview.render_timeline(labels, len(steps)), unsafe_allow_html=True)

    tmp_path = None
    try:
        run_metadata = {
            "parent_categories": ["Defense"],
            "budget_usd": 5000,
            "horizon": "1 year",
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
            tmp_path = tmp_file.name
        generate_demo_report(run_metadata, tmp_path)
        with open(tmp_path, "rb") as f:
            pdf_bytes = f.read()
    except Exception as exc:
        # Clean, styled message -- never a raw traceback.
        st.error(f"Couldn't generate the sample PDF: {exc}")
        return
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    st.session_state.report_markdown = fixture_text
    st.session_state.pdf_bytes = pdf_bytes
    st.session_state.report_filename = "stocksage_sample_report.pdf"
    st.session_state.report_source = "demo"


def _run_real_flow(sector: str, budget_usd: float, horizon: str):
    timeline_container = st.empty()
    status = _TimelineStatus(timeline_container)

    try:
        run_id, report_markdown = asyncio.run(_run_pipeline(sector, budget_usd, horizon, status))
    except Exception as exc:
        st.error(f"Something went wrong while generating your report: {exc}")
        return

    tmp_path = None
    try:
        run_metadata = {
            "parent_categories": [sector],
            "budget_usd": budget_usd,
            "horizon": horizon,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        }
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_file:
            tmp_path = tmp_file.name
        from report.pdf_generator import generate_pdf

        generate_pdf(report_markdown, run_metadata, tmp_path)
        with open(tmp_path, "rb") as f:
            pdf_bytes = f.read()
    except Exception as exc:
        st.error(f"Report generated, but PDF rendering failed: {exc}")
        with st.expander("Raw report (markdown)"):
            st.markdown(report_markdown)
        return
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)

    st.session_state.report_markdown = report_markdown
    st.session_state.pdf_bytes = pdf_bytes
    st.session_state.report_filename = f"stocksage_{sector.lower().replace(' ', '_')}_{run_id}.pdf"
    st.session_state.report_source = "real"


def main():
    st.set_page_config(page_title="StockSage", page_icon="📊", layout="wide")
    st.markdown(html_preview.get_injected_css(), unsafe_allow_html=True)

    if "report_markdown" not in st.session_state:
        st.session_state.report_markdown = None
        st.session_state.pdf_bytes = None
        st.session_state.report_filename = None
        st.session_state.report_source = None

    st.markdown(html_preview.render_hero(), unsafe_allow_html=True)
    st.markdown(html_preview.render_how_it_works(), unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("View a Sample Report")
        st.caption("A real example our system generated earlier -- not a live run. Free, instant.")
        demo_clicked = st.button("View a Sample Report", use_container_width=True)

    sector, budget_usd, horizon, real_clicked = None, None, None, False
    with col2:
        st.subheader("Generate a New Report")
        st.caption("Runs the real research pipeline live. Takes 8-15 minutes and calls external APIs.")
        with st.form("generate_form"):
            sector = st.selectbox("Sector", SECTOR_OPTIONS)
            budget_usd = st.number_input("Budget (USD)", min_value=100, value=5000, step=100)
            horizon = st.selectbox("Investment horizon", HORIZON_OPTIONS, index=1)
            real_clicked = st.form_submit_button("Generate a New Report (live, 8-15 min)", use_container_width=True)

    if demo_clicked:
        _run_demo_flow()
    if real_clicked:
        _run_real_flow(sector, budget_usd, horizon)

    if st.session_state.report_markdown:
        if st.session_state.report_source == "demo":
            st.info("This is a real example our system generated earlier -- not a live run.")
        else:
            st.success("Report ready.")
        st.markdown(html_preview.render_report_preview(st.session_state.report_markdown), unsafe_allow_html=True)
        st.download_button(
            label="Download PDF",
            data=st.session_state.pdf_bytes,
            file_name=st.session_state.report_filename,
            mime="application/pdf",
        )


if __name__ == "__main__":
    main()
