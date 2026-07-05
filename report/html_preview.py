"""Renders a Synthesis Agent markdown report as styled HTML for the
Streamlit in-app preview -- the web counterpart to pdf_generator.py.

Reuses pdf_generator's parse_report() (same block structure) and its
_md_inline_to_html()/_extract_risk_level() helpers directly, so the two
renderers can never silently drift into reading the report differently --
one parse, two presentations. Colors/fonts come from report/theme.py, the
single source shared with the PDF and with app.py's injected CSS.
"""

import re

from report import theme
from report.pdf_generator import (
    _extract_risk_level,
    _md_inline_to_html,
    _strip_unsupported_glyphs,
    parse_report,
)

# Matches the report's leading "**Sector:** Defense" / "**Budget:** $5,000 |
# **Horizon:** 1 year" metadata lines so render_text_block_html can style them
# as field rows instead of falling back to plain intro-text paragraphs.
# Restricted to this known allowlist (rather than matching any "**Label:**"
# line) because the same text block also contains body paragraphs that
# happen to start with a bolded lead-in (e.g. "**Important data-coverage
# note...:** Full Layer 2 research...") -- those must stay plain paragraphs,
# not get boxed as if they were report metadata.
_HEADER_FIELD_NAMES = {"sector", "budget", "horizon"}
_HEADER_FIELD_RE = re.compile(r"^\*\*(.+?):\*\*\s*(.*)$")


def _badge_class(classification: str) -> str:
    return {
        "READY": "badge-ready",
        "WATCH": "badge-watch",
        "SPECULATIVE": "badge-speculative",
    }.get(classification, "badge-speculative")


def _classification_slug(classification: str) -> str:
    return {
        "READY": "ready",
        "WATCH": "watch",
        "SPECULATIVE": "speculative",
    }.get(classification, "speculative")


# Lucide-style inline SVGs (24x24, stroke-based, currentColor) -- static
# markup, not the lucide.js CDN package, since Streamlit's st.markdown(...,
# unsafe_allow_html=True) doesn't execute <script> tags, so a JS-driven icon
# library would never actually swap its placeholders for real icons.
_ICON_SEARCH = (
    '<svg class="ss-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>'
)
_ICON_LINK = (
    '<svg class="ss-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M9 17H7A5 5 0 0 1 7 7h2"/>'
    '<path d="M15 7h2a5 5 0 1 1 0 10h-2"/><line x1="8" y1="12" x2="16" y2="12"/></svg>'
)
_ICON_CHECK_CIRCLE = (
    '<svg class="ss-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M21.8 10A10 10 0 1 1 17 3.34"/>'
    '<path d="m9 11 3 3L22 4"/></svg>'
)
_ICON_EDIT = (
    '<svg class="ss-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M12 20h9"/>'
    '<path d="M16.376 3.622a1 1 0 0 1 3.002 3.002L7.368 18.635a2 2 0 0 1-.855.506l-2.872.838a.5.5 0 0 1-.62-.62'
    'l.838-2.872a2 2 0 0 1 .506-.854z"/></svg>'
)
_ICON_CHECK = (
    '<svg class="ss-icon ss-icon-check" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3" '
    'stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>'
)


def render_sector_opportunity_html(block: dict) -> str:
    sector_name = _md_inline_to_html(_strip_unsupported_glyphs(block["sector_name"]))
    field_rows = "".join(
        f'<div class="ss-field-row"><span class="ss-field-label">{_md_inline_to_html(name)}:</span> '
        f'<span class="ss-field-value">{_md_inline_to_html(value)}</span></div>'
        for name, value in block["fields"]
    )
    return (
        f'<div class="ss-opportunity-block">'
        f'<div class="ss-opportunity-title">{sector_name}</div>'
        f"{field_rows}"
        f"</div>"
    )


def _render_field_row(name: str, value: str) -> str:
    is_risk_field = "risk" in name.lower()
    risk_level = _extract_risk_level(value) if is_risk_field else None
    label_html = _md_inline_to_html(name)
    value_html = _md_inline_to_html(value)
    if risk_level in ("HIGH", "VETO"):
        return (
            f'<div class="ss-risk-alert">'
            f'<span class="ss-risk-tag">RISK · {risk_level}</span> '
            f'<span class="ss-field-label">{label_html}:</span> {value_html}'
            f"</div>"
        )
    return (
        f'<div class="ss-field-row"><span class="ss-field-label">{label_html}:</span> '
        f'<span class="ss-field-value">{value_html}</span></div>'
    )


def render_company_card_html(block: dict) -> str:
    ticker = _md_inline_to_html(_strip_unsupported_glyphs(block["ticker"]))
    company_name = _md_inline_to_html(_strip_unsupported_glyphs(block["company_name"]))
    classification = block["classification"]
    badge_class = _badge_class(classification)
    classification_slug = _classification_slug(classification)

    exposure_type = ""
    other_fields = []
    for name, value in block["fields"]:
        if name.strip().lower() == "exposure" and not exposure_type:
            # First token of the value is the exposure type
            # (CONFIRMED | INFERRED | DIRECT_RECIPIENT), rest is the reason.
            parts = value.split("—", 1)
            exposure_type = parts[0].strip()
            reason = parts[1].strip() if len(parts) > 1 else ""
            other_fields.append(("Exposure reason", reason) if reason else (name, value))
        else:
            other_fields.append((name, value))

    exposure_html = f'<span class="ss-exposure-label">{_md_inline_to_html(exposure_type)}</span>' if exposure_type else ""
    field_rows_html = "".join(_render_field_row(name, value) for name, value in other_fields)

    return (
        f'<div class="ss-company-card ss-company-card--{classification_slug}">'
        f'<div class="ss-company-header">'
        f'<span class="ticker-mono ss-ticker">{ticker}</span>'
        f'<span class="ss-company-name">{company_name}</span>'
        f'<span class="ss-badge {badge_class}">{classification}</span>'
        f"</div>"
        f"{exposure_html}"
        f'<div class="ss-company-fields">'
        f"{field_rows_html}"
        f"</div>"
        f"</div>"
    )


def render_cross_sector_html(block: dict) -> str:
    heading = _md_inline_to_html(block.get("heading") or "Cross-Sector Observations")
    bullets = "".join(f'<li>{_md_inline_to_html(b)}</li>' for b in block["bullets"])
    return (
        f'<div class="ss-cross-sector">'
        f'<div class="ss-section-heading">{heading}</div>'
        f"<ul>{bullets}</ul>"
        f"</div>"
    )


def render_disclaimer_html(block: dict) -> str:
    return f'<div class="ss-disclaimer">{_md_inline_to_html(block["text"])}</div>'


def render_text_block_html(block: dict) -> str:
    # Each line becomes ("heading" | "field" | "text", html); adjacent
    # "field" lines (Sector/Budget/Horizon) are grouped into one
    # .ss-report-header box below, instead of rendering inline as plain
    # paragraphs indistinguishable from body text.
    parts = []
    for line in block["text"].splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            parts.append(("heading", f'<div class="ss-section-heading">{_md_inline_to_html(line.lstrip("#").strip())}</div>'))
            continue
        segments = [seg.strip() for seg in line.split("|")]
        matches = [_HEADER_FIELD_RE.match(seg) for seg in segments]
        if matches and all(m and m.group(1).strip().lower() in _HEADER_FIELD_NAMES for m in matches):
            for m in matches:
                parts.append(
                    (
                        "field",
                        f'<div class="ss-field-row"><span class="ss-field-label">{_md_inline_to_html(m.group(1))}:</span> '
                        f'<span class="ss-field-value">{_md_inline_to_html(m.group(2))}</span></div>',
                    )
                )
            continue
        parts.append(("text", f'<p class="ss-intro-text">{_md_inline_to_html(line)}</p>'))

    chunks = []
    i = 0
    while i < len(parts):
        kind, html = parts[i]
        if kind == "field":
            group_html = []
            while i < len(parts) and parts[i][0] == "field":
                group_html.append(parts[i][1])
                i += 1
            chunks.append(f'<div class="ss-report-header">{"".join(group_html)}</div>')
        else:
            chunks.append(html)
            i += 1
    return "".join(chunks)


def render_report_preview(markdown_text: str) -> str:
    """Renders a full Synthesis Agent markdown report as one HTML string,
    matching the PDF's section order: intro text, sector opportunity block,
    that sector's company cards, cross-sector observations, disclaimer."""
    parts = []
    for block in parse_report(markdown_text):
        block_type = block.get("type")
        if block_type == "sector_opportunity":
            parts.append(render_sector_opportunity_html(block))
        elif block_type == "company":
            parts.append(render_company_card_html(block))
        elif block_type == "cross_sector":
            parts.append(render_cross_sector_html(block))
        elif block_type == "disclaimer":
            parts.append(render_disclaimer_html(block))
        else:
            parts.append(render_text_block_html(block))
    return '<div class="ss-report-preview">' + "".join(parts) + "</div>"


def render_hero() -> str:
    # Each line/circle carries its own animation-delay so the pulse/flow
    # reads as organic drift across the graph rather than one uniform blink.
    svg = (
        f'<svg class="ss-hero-bg" viewBox="0 0 800 240" preserveAspectRatio="none">'
        f'<g class="ss-hero-lines" stroke="{theme.HERO_GRAPH_COLOR}" stroke-width="1" fill="none" opacity="0.6">'
        '<line x1="60" y1="40" x2="220" y2="90" style="animation-delay:0s" />'
        '<line x1="220" y1="90" x2="180" y2="190" style="animation-delay:-0.8s" />'
        '<line x1="220" y1="90" x2="420" y2="60" style="animation-delay:-1.6s" />'
        '<line x1="420" y1="60" x2="560" y2="140" style="animation-delay:-2.4s" />'
        '<line x1="420" y1="60" x2="640" y2="30" style="animation-delay:-3.2s" />'
        '<line x1="560" y1="140" x2="740" y2="170" style="animation-delay:-4.0s" />'
        '<line x1="180" y1="190" x2="380" y2="210" style="animation-delay:-4.8s" />'
        '<line x1="380" y1="210" x2="560" y2="140" style="animation-delay:-5.6s" />'
        "</g>"
        f'<g class="ss-hero-nodes" fill="{theme.HERO_GRAPH_COLOR}" opacity="0.8">'
        '<circle cx="60" cy="40" r="4" style="animation-delay:0s" />'
        '<circle cx="220" cy="90" r="5" style="animation-delay:-0.4s" />'
        '<circle cx="180" cy="190" r="4" style="animation-delay:-0.8s" />'
        '<circle cx="420" cy="60" r="5" style="animation-delay:-1.2s" />'
        '<circle cx="640" cy="30" r="4" style="animation-delay:-1.6s" />'
        '<circle cx="560" cy="140" r="5" style="animation-delay:-2.0s" />'
        '<circle cx="740" cy="170" r="4" style="animation-delay:-2.4s" />'
        '<circle cx="380" cy="210" r="4" style="animation-delay:-2.8s" />'
        "</g>"
        "</svg>"
    )
    return (
        f'<div class="ss-hero">'
        f"{svg}"
        f'<div class="ss-hero-content">'
        f'<div class="ss-hero-title">StockSage</div>'
        f'<div class="ss-hero-tagline">Investment research built on confirmed events, not analyst guesswork.</div>'
        f"</div>"
        f"</div>"
    )


_HOW_IT_WORKS_STEPS = [
    (_ICON_SEARCH, "Research real events"),
    (_ICON_LINK, "Trace the supply chain"),
    (_ICON_CHECK_CIRCLE, "Validate independently"),
    (_ICON_EDIT, "Synthesize honestly"),
]


def render_how_it_works() -> str:
    cards = "".join(
        f'<div class="ss-how-card"><div class="ss-how-icon">{icon}</div>'
        f'<div class="ss-how-label">{label}</div></div>'
        for icon, label in _HOW_IT_WORKS_STEPS
    )
    return f'<div class="ss-how-it-works">{cards}</div>'


def render_timeline(steps: list, active_index: int) -> str:
    """steps: list of label strings, in order. active_index: the step
    currently in progress (0-based). Steps before it render as completed
    (checkmark, dimmed); the active one is highlighted; steps after are
    grayed out as upcoming. active_index >= len(steps) renders all steps
    as completed (used for the final "done" frame)."""
    rows = []
    for i, label in enumerate(steps):
        if i < active_index:
            state = "completed"
            marker = _ICON_CHECK
        elif i == active_index:
            state = "active"
            marker = str(i + 1)
        else:
            state = "upcoming"
            marker = str(i + 1)
        rows.append(
            f'<div class="ss-timeline-step {state}">'
            f'<span class="ss-timeline-marker">{marker}</span>'
            f'<span class="ss-timeline-label">{label}</span>'
            f"</div>"
        )
    return f'<div class="ss-timeline">{"".join(rows)}</div>'


def get_injected_css() -> str:
    # Built as an indented triple-quoted string for readability, then every
    # line's LEADING whitespace is stripped before returning (safe for CSS,
    # which doesn't rely on indentation for meaning): CommonMark/Python-
    # Markdown treats any line indented 4+ spaces as a literal code block,
    # which is exactly what made HTML render as escaped text elsewhere in
    # this file until that was found and fixed. Left indented here (rather
    # than rewritten line-by-line like the other functions) purely because
    # rewriting ~250 lines of CSS as single-line concatenations wouldn't be
    # more readable or safer -- the strip below removes the actual risk.
    css = f"""
    <style>
    @import url('{theme.GOOGLE_FONTS_IMPORT_URL}');

    html, body, [class*="css"] {{
        font-family: {theme.FONT_BODY};
    }}
    [data-testid="stAppViewContainer"], .stApp, body {{
        background: {theme.BG_PAGE} !important;
    }}

    .ss-icon {{
        width: 1.25rem;
        height: 1.25rem;
        display: inline-block;
        vertical-align: middle;
    }}
    .ss-icon-check {{ width: 0.9rem; height: 0.9rem; }}

    .ss-hero {{
        position: relative;
        text-align: center;
        padding: 3rem 1rem 2.5rem 1rem;
        overflow: hidden;
        border-radius: 12px;
        background: {theme.BG_SURFACE};
        border: 1px solid {theme.BORDER};
        box-shadow: {theme.CARD_SHADOW};
        margin-bottom: 1.5rem;
    }}
    .ss-hero-bg {{
        position: absolute;
        top: 0; left: 0; width: 100%; height: 100%;
        opacity: 0.4;
        z-index: 0;
    }}
    .ss-hero-nodes circle {{
        animation: ss-node-pulse 3.6s ease-in-out infinite;
    }}
    .ss-hero-lines line {{
        stroke-dasharray: 6 5;
        animation: ss-dash-flow 7s linear infinite;
    }}
    @keyframes ss-node-pulse {{
        0%, 100% {{ opacity: 0.3; }}
        50% {{ opacity: 0.6; }}
    }}
    @keyframes ss-dash-flow {{
        to {{ stroke-dashoffset: -100; }}
    }}
    .ss-hero-content {{ position: relative; z-index: 1; }}
    .ss-hero-title {{
        font-family: {theme.FONT_HEADLINE};
        font-weight: 600;
        font-size: 3.2rem;
        letter-spacing: -0.01em;
        margin-bottom: 0.4rem;
        color: {theme.CLASSIFICATION_HEX['READY']};
    }}
    .ss-hero-tagline {{
        font-family: {theme.FONT_BODY};
        font-size: 1.05rem;
        color: {theme.TEXT_MUTED};
    }}

    .ss-how-it-works {{
        display: flex;
        gap: 0.75rem;
        margin: 1.5rem 0 2rem 0;
    }}
    .ss-how-card {{
        flex: 1;
        background: {theme.BG_SURFACE};
        border: 1px solid {theme.BORDER};
        border-radius: 10px;
        padding: 1rem 0.5rem;
        text-align: center;
        box-shadow: {theme.CARD_SHADOW};
        transition: transform 0.25s ease, box-shadow 0.25s ease;
    }}
    .ss-how-card:hover {{
        transform: translateY(-3px);
        box-shadow: {theme.CARD_SHADOW_HOVER};
    }}
    .ss-how-icon {{
        margin-bottom: 0.4rem;
        color: {theme.CLASSIFICATION_HEX['READY']};
    }}
    .ss-how-label {{
        font-size: 0.85rem;
        color: {theme.TEXT_MUTED};
        font-weight: 500;
    }}

    .ss-timeline {{
        background: {theme.BG_SURFACE};
        border: 1px solid {theme.BORDER};
        border-radius: 10px;
        padding: 1.25rem 1.5rem;
        margin: 1rem 0;
        box-shadow: {theme.CARD_SHADOW};
    }}
    .ss-timeline-step {{
        display: flex;
        align-items: center;
        gap: 0.75rem;
        padding: 0.55rem 0;
        font-size: 0.95rem;
        transition: opacity 0.3s ease;
    }}
    .ss-timeline-marker {{
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 1.6rem; height: 1.6rem;
        border-radius: 50%;
        font-size: 0.8rem;
        font-family: {theme.FONT_MONO};
        flex-shrink: 0;
        transition: background 0.3s ease, color 0.3s ease;
    }}
    .ss-timeline-label {{ transition: color 0.3s ease; }}
    .ss-timeline-step.completed {{ opacity: 0.55; }}
    .ss-timeline-step.completed .ss-timeline-marker {{
        background: {theme.CLASSIFICATION_HEX['READY']};
        color: #FFFFFF;
    }}
    .ss-timeline-step.completed .ss-timeline-label {{ color: {theme.TEXT_MUTED}; }}
    .ss-timeline-step.active .ss-timeline-marker {{
        background: {theme.CLASSIFICATION_HEX['READY']};
        color: #FFFFFF;
        animation: ss-pulse 1.4s ease-in-out infinite;
    }}
    .ss-timeline-step.active .ss-timeline-label {{
        color: {theme.TEXT_PRIMARY};
        font-weight: 600;
    }}
    .ss-timeline-step.upcoming .ss-timeline-marker {{
        background: {theme.BORDER};
        color: {theme.TEXT_MUTED};
    }}
    .ss-timeline-step.upcoming .ss-timeline-label {{ color: {theme.TEXT_MUTED}; }}
    @keyframes ss-pulse {{
        0%   {{ box-shadow: 0 0 0 0 rgba(11, 110, 79, 0.45); }}
        70%  {{ box-shadow: 0 0 0 7px rgba(11, 110, 79, 0); }}
        100% {{ box-shadow: 0 0 0 0 rgba(11, 110, 79, 0); }}
    }}

    .ss-report-preview {{ margin-top: 1rem; }}

    .ss-report-header {{
        background: {theme.BG_SURFACE};
        border: 1px solid {theme.BORDER};
        border-radius: 10px;
        padding: 1rem 1.5rem;
        margin: 0.5rem 0 1.25rem 0;
        box-shadow: {theme.CARD_SHADOW};
        transition: transform 0.25s ease, box-shadow 0.25s ease;
    }}
    .ss-report-header:hover {{
        transform: translateY(-3px);
        box-shadow: {theme.CARD_SHADOW_HOVER};
    }}

    .ss-opportunity-block {{
        background: {theme.BG_SURFACE};
        border: 1px solid {theme.OPPORTUNITY_BORDER};
        border-radius: 10px;
        padding: 1.25rem 1.5rem;
        margin: 1.25rem 0 1.5rem 0;
        box-shadow: {theme.CARD_SHADOW};
        transition: transform 0.25s ease, box-shadow 0.25s ease;
    }}
    .ss-opportunity-block:hover {{
        transform: translateY(-3px);
        box-shadow: {theme.CARD_SHADOW_HOVER};
    }}
    .ss-opportunity-title {{
        font-family: {theme.FONT_HEADLINE};
        font-size: 1.4rem;
        font-weight: 600;
        color: {theme.OPPORTUNITY_BORDER};
        margin-bottom: 0.6rem;
    }}

    .ss-company-card {{
        background: {theme.BG_SURFACE};
        border: 1px solid {theme.BORDER};
        border-left: 4px solid transparent;
        border-radius: 10px;
        padding: 1.25rem 1.5rem;
        margin: 0 0 1rem 0;
        box-shadow: {theme.CARD_SHADOW};
        transition: transform 0.25s ease, box-shadow 0.25s ease;
    }}
    .ss-company-card:hover {{
        transform: translateY(-3px);
        box-shadow: {theme.CARD_SHADOW_HOVER};
    }}
    .ss-company-card--ready {{ border-left-color: {theme.CLASSIFICATION_HEX['READY']}; }}
    .ss-company-card--watch {{ border-left-color: {theme.CLASSIFICATION_HEX['WATCH']}; }}
    .ss-company-card--speculative {{ border-left-color: {theme.CLASSIFICATION_HEX['SPECULATIVE']}; }}
    .ss-company-header {{
        display: flex;
        align-items: baseline;
        gap: 0.75rem;
        margin-bottom: 0.5rem;
        flex-wrap: wrap;
    }}
    .ss-ticker {{
        font-size: 1.3rem;
        font-weight: 600;
        color: {theme.TEXT_PRIMARY};
    }}
    .ss-company-name {{
        font-family: {theme.FONT_HEADLINE};
        font-size: 1.15rem;
        color: {theme.TEXT_PRIMARY};
    }}
    .ss-badge {{
        margin-left: auto;
        padding: 0.2rem 0.75rem;
        border-radius: 999px;
        font-size: 0.75rem;
        font-weight: 700;
        letter-spacing: 0.03em;
        text-transform: uppercase;
    }}
    .badge-ready {{ background: {theme.CLASSIFICATION_HEX['READY']}; color: #FFFFFF; }}
    .badge-watch {{ background: {theme.CLASSIFICATION_HEX['WATCH']}; color: #FFFFFF; }}
    .badge-speculative {{ background: {theme.CLASSIFICATION_HEX['SPECULATIVE']}; color: #FFFFFF; }}

    .ss-exposure-label {{
        display: inline-block;
        font-size: 0.75rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: {theme.TEXT_MUTED};
        margin-bottom: 0.6rem;
    }}

    .ss-company-fields {{ margin-top: 0.25rem; }}
    .ss-field-row {{
        padding: 0.45rem 0;
        line-height: 1.65;
        font-size: 0.92rem;
        color: {theme.TEXT_PRIMARY};
        border-bottom: 1px solid {theme.BORDER};
    }}
    .ss-field-row:last-child {{ border-bottom: none; }}
    .ss-field-label {{ color: {theme.TEXT_MUTED}; font-weight: 600; }}
    .ss-field-value {{ color: {theme.TEXT_PRIMARY}; }}

    .ss-risk-alert {{
        background: {theme.RISK_BG};
        border: 1px solid {theme.RISK_BORDER};
        border-radius: 8px;
        padding: 0.75rem 1rem;
        margin: 0.5rem 0;
        line-height: 1.6;
        font-size: 0.92rem;
        color: {theme.RISK_TEXT};
    }}
    .ss-risk-tag {{
        display: inline-block;
        font-family: {theme.FONT_MONO};
        font-weight: 700;
        font-size: 0.75rem;
        color: {theme.RISK_BORDER};
        margin-right: 0.4rem;
    }}

    .ss-cross-sector {{
        background: {theme.BG_SURFACE};
        border: 1px solid {theme.BORDER};
        border-radius: 10px;
        padding: 1.25rem 1.5rem;
        margin: 1rem 0;
        box-shadow: {theme.CARD_SHADOW};
        transition: transform 0.25s ease, box-shadow 0.25s ease;
    }}
    .ss-cross-sector:hover {{
        transform: translateY(-3px);
        box-shadow: {theme.CARD_SHADOW_HOVER};
    }}
    .ss-cross-sector ul {{ margin: 0.4rem 0 0 0; padding-left: 1.2rem; }}
    .ss-cross-sector li {{
        color: {theme.TEXT_PRIMARY};
        line-height: 1.7;
        font-size: 0.92rem;
        margin-bottom: 0.4rem;
    }}

    .ss-section-heading {{
        font-family: {theme.FONT_HEADLINE};
        font-size: 1.2rem;
        font-weight: 600;
        color: {theme.TEXT_PRIMARY};
        margin: 0.5rem 0;
    }}
    .ss-intro-text {{
        color: {theme.TEXT_MUTED};
        line-height: 1.7;
        font-size: 0.92rem;
    }}
    .ss-disclaimer {{
        color: {theme.TEXT_MUTED};
        font-size: 0.78rem;
        line-height: 1.6;
        margin-top: 1.5rem;
        padding-top: 1rem;
        border-top: 1px solid {theme.BORDER};
    }}

    /* Tickers, dollar amounts, and other numeric data use tabular mono
       figures wherever they appear -- the detail that makes this read as
       a trading tool rather than a generic dashboard. */
    .ticker-mono {{
        font-family: {theme.FONT_MONO};
        font-variant-numeric: tabular-nums;
    }}
    </style>
    """
    return "\n".join(line.lstrip() for line in css.splitlines())
