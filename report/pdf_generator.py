"""Converts the Synthesis Agent's markdown report into a styled PDF.

The Synthesis Agent's prompt (prompts/synthesis_agent.txt) specifies a
fixed markdown shape -- "### TICKER -- Company -- CLASSIFICATION" headers,
"**Field:** value" lines, a Cross-Sector Observations section, a closing
disclaimer -- but it's still LLM output: real runs show the model
occasionally bolding an entire risk statement for emphasis instead of just
the field label (e.g. "**Risk flag: ... HIGH.** more text" rather than
"**Risk flag:** ... HIGH. more text"). Parsing here is intentionally
lenient about where the bold markers land, and anything that still doesn't
match a known pattern falls back to plain formatted text rather than
crashing -- same crash-tolerant convention as data/fetchers.py.

Whether a Risk flag is severe enough to render in a highlighted box is
read from a required literal "[LOW|MEDIUM|HIGH|VETO]" token the prompt now
asks the Risk flag value to always lead with -- not guessed from the
surrounding prose (an earlier keyword-scan approach had real false
positives/negatives; see git history if resurrecting that is ever tempting).
"""

import re
from datetime import datetime
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from report import theme

# Colors sourced from report/theme.py -- the single source of truth shared
# with html_preview.py's injected CSS, so the web preview and this PDF use
# the exact same light palette and never visually drift apart.
_CLASSIFICATION_HEX = theme.CLASSIFICATION_HEX
_CLASSIFICATION_COLORS = {key: colors.HexColor(value) for key, value in _CLASSIFICATION_HEX.items()}
_CLASSIFICATION_BG = {key: colors.HexColor(value) for key, value in theme.CLASSIFICATION_BG_PDF.items()}
_RISK_BG = colors.HexColor(theme.RISK_BG)
_RISK_BORDER = colors.HexColor(theme.RISK_BORDER)
_RISK_TEXT = colors.HexColor(theme.RISK_TEXT)
_OPPORTUNITY_BG = colors.HexColor(theme.OPPORTUNITY_BG)
_OPPORTUNITY_BORDER = colors.HexColor(theme.OPPORTUNITY_BORDER)
_TEXT_MUTED = colors.HexColor(theme.TEXT_MUTED)

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_COMPANY_HEADER_RE = re.compile(r"^#{1,3}\s*(.+?)\s*[—-]\s*(.+?)\s*[—-]\s*(READY|WATCH|SPECULATIVE)\s*$", re.IGNORECASE)
_SECTOR_HEADING_RE = re.compile(r"^##\s*(.+?)\s*$")
_FIELD_RE = re.compile(r"^\*\*([A-Za-z][\w /]*?):\*{0,2}\s*(.*)$")
_SECTOR_OPPORTUNITY_FIELD_NAMES = {"the opportunity", "the project", "why this matters"}

# prompts/synthesis_agent.txt requires the Risk flag value to always lead
# with a literal "[LOW|MEDIUM|HIGH|VETO]" token. Reading that token
# deterministically replaces an earlier keyword/negation-based heuristic
# that scanned the prose for "HIGH"/"VETO" -- that approach had real false
# positives (e.g. "no veto" and "High Court" both tripped it) and false
# negatives it couldn't rule out; a required, structured token has neither.
_RISK_TOKEN_RE = re.compile(r"^\[(LOW|MEDIUM|HIGH|VETO)\]\s*", re.IGNORECASE)


def _extract_risk_level(value: str) -> str | None:
    match = _RISK_TOKEN_RE.match(value.strip())
    return match.group(1).upper() if match else None

# reportlab's base-14 fonts (Helvetica etc.) only cover WinAnsiEncoding --
# emoji and other symbol-block characters render as a fallback "tofu" box.
# The Risk/Sector Agent prompts don't forbid emoji (observed live: the
# model emits "🚩" in some risk statements), so raw LLM text needs this
# stripped before rendering, same as any other untrusted/uncontrolled text.
_UNSUPPORTED_GLYPH_RE = re.compile(
    "[" "\U0001F000-\U0001FFFF" "\U00002600-\U000027BF" "\U00002B00-\U00002BFF" "\U0000FE00-\U0000FE0F" "]"
)


def _strip_unsupported_glyphs(text: str) -> str:
    return _UNSUPPORTED_GLYPH_RE.sub("", text)


def _md_inline_to_html(text: str) -> str:
    """Escapes raw text for reportlab's Paragraph XML parser, then converts
    markdown **bold** spans to <b> tags. Any stray unpaired '**' left over
    (e.g. from a malformed bold span) is stripped rather than left as a
    visible artifact. Also strips glyphs the base PDF font can't render."""
    text = _strip_unsupported_glyphs(text)
    parts = []
    last = 0
    for m in _BOLD_RE.finditer(text):
        parts.append(escape(text[last : m.start()]))
        parts.append(f"<b>{escape(m.group(1))}</b>")
        last = m.end()
    parts.append(escape(text[last:]))
    return "".join(parts).replace("**", "")


_HEADING_SPLIT_RE = re.compile(r"\n(?=#{2,3}\s)")


def _split_blocks(markdown_text: str) -> list[str]:
    # Split on markdown horizontal rules ("---") first -- the model uses
    # these between major sections. Headings (## or ###) also always start
    # a fresh block on their own, whether or not a preceding "---" is
    # present -- the model isn't guaranteed to insert one between a new
    # sector's opportunity block and its first company card.
    rule_blocks = re.split(r"\n\s*-{3,}\s*\n", markdown_text.strip())
    blocks = []
    for rule_block in rule_blocks:
        for piece in _HEADING_SPLIT_RE.split(rule_block.strip()):
            piece = piece.strip()
            if piece:
                blocks.append(piece)
    return blocks


def _parse_company_block(lines: list[str]) -> dict | None:
    header_match = _COMPANY_HEADER_RE.match(lines[0].strip())
    if not header_match:
        return None
    ticker, company_name, classification = header_match.groups()

    fields = []
    notes = []
    buffer_name, buffer_value = None, None
    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue
        field_match = _FIELD_RE.match(line)
        if field_match:
            if buffer_name is not None:
                fields.append((buffer_name, buffer_value))
            buffer_name, buffer_value = field_match.group(1).strip(), field_match.group(2).strip()
        elif buffer_name is not None:
            buffer_value = f"{buffer_value} {line}"
        else:
            notes.append(line)
    if buffer_name is not None:
        fields.append((buffer_name, buffer_value))

    return {
        "type": "company",
        "ticker": ticker.strip(),
        "company_name": company_name.strip(),
        "classification": classification.strip().upper(),
        "fields": fields,
        "notes": notes,
    }


def _parse_sector_opportunity_block(lines: list[str]) -> dict | None:
    heading_match = _SECTOR_HEADING_RE.match(lines[0].strip())
    if not heading_match:
        return None
    sector_name = heading_match.group(1).strip()

    fields = []
    buffer_name, buffer_value = None, None
    for raw_line in lines[1:]:
        line = raw_line.strip()
        if not line:
            continue
        field_match = _FIELD_RE.match(line)
        if field_match:
            if buffer_name is not None:
                fields.append((buffer_name, buffer_value))
            buffer_name, buffer_value = field_match.group(1).strip(), field_match.group(2).strip()
        elif buffer_name is not None:
            buffer_value = f"{buffer_value} {line}"
    if buffer_name is not None:
        fields.append((buffer_name, buffer_value))

    # Only claim this shape if it actually has at least one of the expected
    # opportunity fields -- otherwise this is some other "## ..." heading
    # (e.g. Cross-Sector Observations) and the caller should try that next.
    if not any(name.lower() in _SECTOR_OPPORTUNITY_FIELD_NAMES for name, _ in fields):
        return None

    return {"type": "sector_opportunity", "sector_name": sector_name, "fields": fields}


def _parse_block(block: str) -> dict:
    lines = block.splitlines()
    first = lines[0].strip() if lines else ""

    if first.startswith("#"):
        parsed = _parse_company_block(lines)
        if parsed:
            return parsed
        parsed = _parse_sector_opportunity_block(lines)
        if parsed:
            return parsed
        if "cross-sector" in first.lower():
            bullets = [ln.strip().lstrip("-* ").strip() for ln in lines[1:] if ln.strip()]
            return {"type": "cross_sector", "heading": first.lstrip("#").strip(), "bullets": bullets}

    if first.lower().startswith("**disclaimer"):
        return {"type": "disclaimer", "text": block}

    return {"type": "text", "text": block}


def parse_report(markdown_text: str) -> list[dict]:
    return [_parse_block(block) for block in _split_blocks(markdown_text)]


def _build_styles():
    styles = getSampleStyleSheet()
    styles.add(
        ParagraphStyle(
            "CoverTitle",
            parent=styles["Title"],
            fontSize=26,
            spaceAfter=6,
            alignment=TA_CENTER,
        )
    )
    styles.add(
        ParagraphStyle(
            "CoverMeta",
            parent=styles["Normal"],
            fontSize=11,
            alignment=TA_CENTER,
            spaceAfter=4,
        )
    )
    styles.add(
        ParagraphStyle(
            "CoverNotice",
            parent=styles["Normal"],
            fontSize=12,
            alignment=TA_CENTER,
            textColor=_RISK_TEXT,
            spaceBefore=18,
            spaceAfter=6,
        )
    )
    styles.add(ParagraphStyle("BodyText2", parent=styles["Normal"], fontSize=10, spaceAfter=8, leading=14))
    styles.add(ParagraphStyle("RiskText", parent=styles["BodyText2"], textColor=_RISK_TEXT))
    styles.add(
        ParagraphStyle(
            "SectionHeading",
            parent=styles["Heading2"],
            spaceBefore=14,
            spaceAfter=8,
        )
    )
    styles.add(
        ParagraphStyle(
            "DisclaimerStyle",
            parent=styles["Normal"],
            fontSize=8.5,
            textColor=_TEXT_MUTED,
            leading=12,
        )
    )
    return styles


def _company_flowables(block: dict, styles) -> list:
    classification = block["classification"]
    header_color = _CLASSIFICATION_COLORS.get(classification, colors.black)
    header_hex = _CLASSIFICATION_HEX.get(classification, "#000000")
    header_bg = _CLASSIFICATION_BG.get(classification, colors.whitesmoke)

    header_text = (
        f"<b>{escape(_strip_unsupported_glyphs(block['ticker']))} — {escape(_strip_unsupported_glyphs(block['company_name']))}</b>"
        f"  &nbsp;&nbsp; <font color='{header_hex}'><b>[{escape(classification)}]</b></font>"
    )
    header_para = Paragraph(header_text, ParagraphStyle("CompanyHeader", parent=styles["Heading3"], spaceAfter=0))
    header_table = Table([[header_para]], colWidths=[6.5 * inch])
    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), header_bg),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LINEBELOW", (0, 0), (-1, -1), 1.5, header_color),
            ]
        )
    )

    flowables = [header_table, Spacer(1, 4)]

    for field_name, value in block["fields"]:
        label_html = _md_inline_to_html(field_name)
        is_risk_field = "risk" in field_name.lower()
        risk_level = _extract_risk_level(value) if is_risk_field else None
        value_html = _md_inline_to_html(value)
        if risk_level in ("HIGH", "VETO"):
            para = Paragraph(f"<b>RISK [{risk_level}] — {label_html}:</b> {value_html}", styles["RiskText"])
            risk_table = Table([[para]], colWidths=[6.5 * inch])
            risk_table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, -1), _RISK_BG),
                        ("BOX", (0, 0), (-1, -1), 1, _RISK_BORDER),
                        ("LEFTPADDING", (0, 0), (-1, -1), 8),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                )
            )
            flowables.append(risk_table)
            flowables.append(Spacer(1, 4))
        else:
            flowables.append(Paragraph(f"<b>{label_html}:</b> {value_html}", styles["BodyText2"]))

    # Defensive fallback: unrecognized lines within this company's block --
    # a format variance the field parser didn't catch. Still render them
    # (never drop content) as plain text -- no risk detection here, since
    # severity is now read from a required structured token on the Risk
    # flag field itself, not guessed from arbitrary prose.
    for note in block.get("notes", []):
        flowables.append(Paragraph(_md_inline_to_html(note), styles["BodyText2"]))

    flowables.append(Spacer(1, 10))
    return flowables


def _sector_opportunity_flowables(block: dict, styles) -> list:
    sector_name_html = _md_inline_to_html(block["sector_name"])
    cell_content = [
        Paragraph(
            f"<b>{sector_name_html}</b>",
            ParagraphStyle("SectorOppHeading", parent=styles["Heading2"], textColor=_OPPORTUNITY_BORDER, spaceAfter=8),
        )
    ]
    for field_name, value in block["fields"]:
        label_html = _md_inline_to_html(field_name)
        value_html = _md_inline_to_html(value)
        cell_content.append(Paragraph(f"<b>{label_html}:</b> {value_html}", styles["BodyText2"]))

    box = Table([[cell_content]], colWidths=[6.5 * inch])
    box.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), _OPPORTUNITY_BG),
                ("BOX", (0, 0), (-1, -1), 1.5, _OPPORTUNITY_BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return [box, Spacer(1, 14)]


def _cross_sector_flowables(block: dict, styles) -> list:
    flowables = [Paragraph(escape(block["heading"]) or "Cross-Sector Observations", styles["SectionHeading"])]
    for bullet in block["bullets"]:
        flowables.append(Paragraph(f"&bull;&nbsp; {_md_inline_to_html(bullet)}", styles["BodyText2"]))
    flowables.append(Spacer(1, 10))
    return flowables


def _disclaimer_flowables(block: dict, styles) -> list:
    return [
        Spacer(1, 10),
        Paragraph(_md_inline_to_html(block["text"]), styles["DisclaimerStyle"]),
    ]


def _text_flowables(block: dict, styles) -> list:
    flowables = []
    for line in block["text"].splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("#"):
            flowables.append(Paragraph(_md_inline_to_html(line.lstrip("#").strip()), styles["SectionHeading"]))
        else:
            flowables.append(Paragraph(_md_inline_to_html(line), styles["BodyText2"]))
    flowables.append(Spacer(1, 6))
    return flowables


def _cover_page_flowables(run_metadata: dict, styles) -> list:
    sectors = run_metadata.get("parent_categories") or run_metadata.get("sectors") or []
    if isinstance(sectors, str):
        sectors = [sectors]
    budget_usd = run_metadata.get("budget_usd")
    horizon = run_metadata.get("horizon")
    generated_at = run_metadata.get("generated_at") or datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    flowables = [
        Spacer(1, 1.5 * inch),
        Paragraph("StockSage Research Report", styles["CoverTitle"]),
        Spacer(1, 0.3 * inch),
        Paragraph(f"Generated: {escape(str(generated_at))}", styles["CoverMeta"]),
        Paragraph(f"Sector(s): {escape(', '.join(sectors)) if sectors else 'N/A'}", styles["CoverMeta"]),
        Paragraph(
            f"Budget: {escape(f'${budget_usd:,.0f}') if isinstance(budget_usd, (int, float)) else escape(str(budget_usd))}"
            f" &nbsp;|&nbsp; Horizon: {escape(str(horizon))}",
            styles["CoverMeta"],
        ),
        Paragraph(
            "<b>Point-in-time snapshot, based on available data at generation time — not investment advice.</b>",
            styles["CoverNotice"],
        ),
        PageBreak(),
    ]
    return flowables


def generate_pdf(markdown_report: str, run_metadata: dict, output_path: str) -> str:
    """Renders the Synthesis Agent's markdown report to a PDF at output_path.

    run_metadata expects: parent_categories (list[str] or str), budget_usd,
    horizon, and optionally generated_at (defaults to now).
    """
    styles = _build_styles()
    doc = SimpleDocTemplate(
        output_path,
        pagesize=LETTER,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
    )

    story = _cover_page_flowables(run_metadata, styles)

    for block in parse_report(markdown_report):
        block_type = block.get("type")
        try:
            if block_type == "company":
                story.append(KeepTogether(_company_flowables(block, styles)))
            elif block_type == "sector_opportunity":
                story.extend(_sector_opportunity_flowables(block, styles))
            elif block_type == "cross_sector":
                story.extend(_cross_sector_flowables(block, styles))
            elif block_type == "disclaimer":
                story.extend(_disclaimer_flowables(block, styles))
            else:
                story.extend(_text_flowables(block, styles))
        except Exception:
            # Last-resort fallback: never let one malformed block crash the
            # whole PDF -- render its raw text plainly instead.
            raw_text = block.get("text") if isinstance(block.get("text"), str) else str(block)
            story.append(Paragraph(escape(raw_text), styles["BodyText2"]))

    doc.build(story)
    return output_path
