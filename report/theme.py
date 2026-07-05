"""Single source of truth for StockSage's visual design tokens -- colors and
fonts shared between the Streamlit web preview (app.py/html_preview.py, via
injected CSS) and the PDF (report/pdf_generator.py, via reportlab).

One warm-cream "financial publication" light palette, used identically by
both mediums -- there is no separate dark-web/light-PDF split anymore, so
the two engines can't visually drift apart.
"""

# ---- Core palette ----
BG_PAGE = "#FAF6F0"
BG_SURFACE = "#FFFFFF"
TEXT_PRIMARY = "#1A1A1A"
TEXT_MUTED = "#6B6560"
BORDER = "#E8E2D9"

# ---- Elevation (web-only; reportlab has no box-shadow equivalent -- PDF
# cards use BORDER as a plain box instead) ----
CARD_SHADOW = "0 2px 12px rgba(139, 131, 120, 0.12)"
CARD_SHADOW_HOVER = "0 6px 20px rgba(139, 131, 120, 0.18)"

# ---- Classification accents -- identical hue and value in web and PDF ----
CLASSIFICATION_HEX = {
    "READY": "#0B6E4F",
    "WATCH": "#B45309",
    "SPECULATIVE": "#8B8378",
}
# PDF-only: each accent composited at ~12% opacity onto white, for the
# tinted header band behind a company's classification. Web cards use a
# plain white surface with a left accent bar instead, so this stays PDF-only.
CLASSIFICATION_BG_PDF = {
    "READY": "#E2EEEA",
    "WATCH": "#F6EAE1",
    "SPECULATIVE": "#F1F0EF",
}

# ---- Risk alert (HIGH / VETO) -- identical on web and PDF, both light ----
RISK_BORDER = "#991B1B"
RISK_BG = "#FEF2F2"
RISK_TEXT = "#991B1B"

# ---- Sector opportunity block -- a distinct "informational" accent, never
# reused for a classification/risk meaning, so no color means two things ----
OPPORTUNITY_BORDER = "#3B82F6"
OPPORTUNITY_BG = "#E8F0FE"

# ---- Hero graph motif -- muted, not the classification green, so it reads
# as ambient texture rather than implying a READY signal ----
HERO_GRAPH_COLOR = "#8B8378"

# ---- Fonts ----
FONT_HEADLINE = "'Newsreader', serif"
FONT_BODY = "'Inter', sans-serif"
FONT_MONO = "'JetBrains Mono', monospace"

GOOGLE_FONTS_IMPORT_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Newsreader:ital,opsz,wght@0,72,400;0,72,600;0,72,700&"
    "family=Inter:wght@400;500;600;700&"
    "family=JetBrains+Mono:wght@400;500;600&"
    "display=swap"
)
