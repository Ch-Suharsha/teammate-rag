"""
Direct PDF research poster generator for Atlas (DATA 298B, Team 1).
Uses reportlab — no office application required.

Run: python3 eval/make_poster_pdf.py
Output: eval/poster/atlas_poster.pdf
"""
import os
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import landscape
from reportlab.lib.units import inch
from reportlab.lib.colors import HexColor, white, black
from reportlab.platypus import Paragraph
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT

# ── Page size ─────────────────────────────────────────────────────────
W = 48 * inch
H = 36 * inch

# ── Colours ──────────────────────────────────────────────────────────
NAVY   = HexColor("#002255")
ORANGE = HexColor("#E57200")
GOLD   = HexColor("#FFC107")
LGRAY  = HexColor("#F2F4F6")
DKGRAY = HexColor("#333333")
WHITE  = white
BORDER = HexColor("#CCCCCC")

# ── Layout constants (inches) ─────────────────────────────────────────
GUTTER = 0.85         # orange strip width on each side
INNER_PAD = 0.28      # gap between gutter and column
HDR_H = 5.0           # header height
FTR_H = 0.65          # footer height
COL_GAP = 0.30        # gap between columns
SEC_RADIUS = 0        # corner radius for section bars

# Column layout
COL_X0   = GUTTER + INNER_PAD
COL_AREA = W/inch - 2*(GUTTER + INNER_PAD) - 0.05
COL_W    = (COL_AREA - 2*COL_GAP) / 3

# Content area vertical
CONT_Y0  = H/inch - HDR_H - 0.30    # top of content (inches from bottom = H - Y)
CONT_BOT = FTR_H + 0.10              # bottom of content from page bottom

def cx(n):
    return (COL_X0 + n*(COL_W + COL_GAP)) * inch

def cy(y_from_top):
    """Convert y-from-top (inches) to reportlab y (from bottom)."""
    return H - y_from_top * inch

# ── Drawing helpers ───────────────────────────────────────────────────

def filled_rect(c, x, y_top, w, h, color, stroke=False, stroke_color=BORDER):
    """Draw a filled rectangle. x,y_top,w,h all in inches."""
    c.saveState()
    c.setFillColor(color)
    if stroke:
        c.setStrokeColor(stroke_color)
        c.setLineWidth(0.5)
    else:
        c.setStrokeColor(color)
        c.setLineWidth(0)
    c.rect(x*inch, cy(y_top + h), w*inch, h*inch,
           fill=1, stroke=(1 if stroke else 0))
    c.restoreState()


def text_block(c, x, y_top, w, h, txt, size=9, bold=False,
               color=DKGRAY, align="left", leading_mult=1.35):
    """Draw a block of text fitting inside a rectangle (inches)."""
    if not txt.strip():
        return
    c.saveState()
    font = "Helvetica-Bold" if bold else "Helvetica"
    c.setFont(font, size)
    c.setFillColor(color)
    # wrap manually with reportlab's multi-line
    from reportlab.lib.utils import simpleSplit
    leading = size * leading_mult
    lines = []
    for raw_line in txt.split("\n"):
        wrapped = simpleSplit(raw_line, font, size, w * inch - 2)
        lines.extend(wrapped if wrapped else [""])
    y_cursor = cy(y_top) - size  # top baseline
    for line in lines:
        if y_cursor < cy(y_top + h):
            break
        if align == "center":
            c.drawCentredString(x*inch + w*inch/2, y_cursor, line)
        elif align == "right":
            c.drawRightString((x + w)*inch, y_cursor, line)
        else:
            c.drawString(x*inch + 4, y_cursor, line)
        y_cursor -= leading
    c.restoreState()


def section(c, x, y_top, w, h, title, lines,
            title_size=11, body_size=9, body_leading=1.38):
    """Draw an orange title bar + light-grey body panel with text."""
    bar_h = 0.28
    filled_rect(c, x, y_top, w, bar_h, ORANGE)
    text_block(c, x + 0.06, y_top, w - 0.12, bar_h,
               title, size=title_size, bold=True, color=WHITE)
    body_h = h - bar_h
    filled_rect(c, x, y_top + bar_h, w, body_h, LGRAY, stroke=True)
    body_text = "\n".join(lines)
    text_block(c, x + 0.12, y_top + bar_h + 0.09,
               w - 0.24, body_h - 0.12,
               body_text, size=body_size, color=DKGRAY,
               leading_mult=body_leading)
    return y_top + h


def bullets(c, x, y_top, w, h, title, items,
            title_size=11, body_size=9):
    """section() with bullet prefix."""
    return section(c, x, y_top, w, h, title,
                   [f"•  {i}" for i in items],
                   title_size=title_size, body_size=body_size)


def image_section(c, x, y_top, w, h, title, img_path, title_size=11):
    """Orange title bar + embedded image."""
    bar_h = 0.28
    filled_rect(c, x, y_top, w, bar_h, ORANGE)
    text_block(c, x + 0.06, y_top, w - 0.12, bar_h,
               title, size=title_size, bold=True, color=WHITE)
    img_h = h - bar_h
    filled_rect(c, x, y_top + bar_h, w, img_h, LGRAY, stroke=True)
    if os.path.exists(img_path):
        c.drawImage(img_path, x*inch + 2, cy(y_top + h) + 2,
                    w*inch - 4, img_h*inch - 4,
                    preserveAspectRatio=True, anchor="c", mask="auto")
    return y_top + h

# ── Charts ────────────────────────────────────────────────────────────
CHARTS_DIR = "eval/charts"
CH = {
    "radar":   f"{CHARTS_DIR}/1_radar_dimensions.png",
    "cat_bar": f"{CHARTS_DIR}/2_geval_by_category.png",
    "dual":    f"{CHARTS_DIR}/3_dual_metric_by_category.png",
    "scatter": f"{CHARTS_DIR}/4_scatter_task_vs_geval.png",
    "heatmap": f"{CHARTS_DIR}/5_heatmap_dim_category.png",
    "dist":    f"{CHARTS_DIR}/6_geval_distribution.png",
    "donut":   f"{CHARTS_DIR}/7_category_distribution.png",
    "summary": f"{CHARTS_DIR}/8_summary_dashboard.png",
}

# ── Build PDF ─────────────────────────────────────────────────────────
out_dir = "eval/poster"
os.makedirs(out_dir, exist_ok=True)
pdf_path = f"{out_dir}/atlas_poster.pdf"

c = canvas.Canvas(pdf_path, pagesize=(W, H))

# 1. White background
filled_rect(c, 0, 0, W/inch, H/inch, WHITE)

# 2. Orange gutter strips (left + right)
filled_rect(c, 0,              0, GUTTER, H/inch, ORANGE)
filled_rect(c, W/inch - GUTTER, 0, GUTTER, H/inch, ORANGE)

# 3. Navy header
filled_rect(c, GUTTER, 0, W/inch - 2*GUTTER, HDR_H, NAVY)

# University line
text_block(c, GUTTER + 0.3, 0.15, W/inch - 2*GUTTER - 0.6, 0.55,
           "San José State University   ·   College of Science   ·   Data Science M.S. Program  (DATA 298B)",
           size=13, color=GOLD, align="center")

# Title
text_block(c, GUTTER + 0.3, 0.68, W/inch - 2*GUTTER - 0.6, 1.7,
           "Atlas: AI-Powered Customer Support Agent\nwith Fine-Tuned Phi-4-mini and RAG Pipeline",
           size=36, bold=True, color=WHITE, align="center", leading_mult=1.25)

# Team names
text_block(c, GUTTER + 0.3, 2.55, W/inch - 2*GUTTER - 0.6, 0.55,
           "Thota Himaja Sree     ·     Rondla Chandana     ·     Patel Meetkumar     ·     Cheedalla Suharsha",
           size=17, bold=True, color=WHITE, align="center")

# Advisor
text_block(c, GUTTER + 0.3, 3.10, W/inch - 2*GUTTER - 0.6, 0.45,
           "Faculty Advisor: Prof. Simon Shim     ·     Team 1",
           size=14, color=GOLD, align="center")

# Orange bar at header bottom
filled_rect(c, 0, HDR_H - 0.10, W/inch, 0.10, ORANGE)

# ── Content layout ────────────────────────────────────────────────────
CY = HDR_H + 0.28   # content top (from page top)

# R = row heights
R1 = 9.5
R2 = 10.0
# R3 fills the rest
R3 = H/inch - CY - R1 - R2 - FTR_H - 0.50

y1 = CY
y2 = y1 + R1 + 0.18
y3 = y2 + R2 + 0.20

# ── COLUMN 0 ─────────────────────────────────────────────────────────
col = 0; x0 = COL_X0 + col*(COL_W + COL_GAP)

# Introduction
y = bullets(c, x0, y1, COL_W, 4.2, "1.  Introduction", [
    "E-commerce platforms require scalable, intelligent customer support",
    "Rule-based chatbots fail on long-tail queries and nuanced language",
    "LLMs offer fluent responses but hallucinate without grounding",
    "Atlas combines fine-tuned SLM + RAG + deterministic tool routing",
    "Goal: accurate, empathetic, grounded support at small-model cost",
])

# Motivation
y = bullets(c, x0, y, COL_W, 2.55, "2.  Motivation & Problem Statement", [
    "Support tickets cost ~$5–15 each; automation saves 40–70 %",
    "Customers demand instant resolution of order, refund & policy queries",
    "Challenge: hallucination, identity verification, latency, cost",
    "Research question: can a fine-tuned 3.8 B SLM + RAG match GPT-4-class accuracy?",
])

# Dataset
bullets(c, x0, y, COL_W, 2.55, "3.  Dataset", [
    "Amazon product catalog — 359 K items (title, category, price, description)",
    "Policy knowledge base — 45 hand-authored entries (returns, refunds, shipping)",
    "Fine-tune corpus — 2,400 synthetic customer-support Q&A pairs",
    "Evaluation set — 50 hand-crafted test cases across 8 intent categories",
])

# Row 2 Col 0 — Architecture
y = bullets(c, x0, y2, COL_W, 4.95, "4.  System Architecture", [
    "FastAPI backend · PostgreSQL (orders, sessions) · Qdrant vector DB",
    "HuggingFace Inference Endpoint — fine-tuned Phi-4-mini adapter (A10G GPU)",
    "Retrieval: sentence-transformers/all-MiniLM-L6-v2 (384-dim), cosine top-6",
    "Deterministic tool router (keyword/regex) — 8 tools:",
    "   lookup_order · process_refund · cancel_order · get_account_info",
    "   search_product_knowledge · search_policy_knowledge",
    "   escalate_to_human · send_customer_email",
    "Identity verification gate — email/order-ID required before PII access",
    "Two-layer safety net: stall detection + data-presence → template fallback",
    "Docker Compose stack: api · postgres · qdrant · mailhog",
])

# Row 2 Col 0 — Fine-Tuning
bullets(c, x0, y, COL_W, R2 - (y - y2), "5.  Model Selection & Fine-Tuning", [
    "Evaluated 4 open-weight models on 20 qualitative prompts:",
    "  LLaMA-3.2-1B · SmolLM2-1.7B · Qwen2.5-1.5B · Phi-4-mini-instruct",
    "Selected: Phi-4-mini-instruct (3.8 B) — best instruction following & tone",
    "Fine-tune: QLoRA 4-bit (NF4 quantization) on Google Colab A100",
    "2,400 Q&A pairs · 3 epochs · batch size 4 · learning rate 2e-4",
    "Adapter deployed to HuggingFace Hub; served via A10G Inference Endpoint",
    "Avg first-token latency: ~3–5 s (warm) · cold-start: 30–60 s",
])

# Row 3 Col 0 — Future Work
y = bullets(c, x0, y3, COL_W, 4.55, "12.  Future Work & Limitations", [
    "Deploy no-RAG baseline for direct ablation comparison",
    "Add cross-encoder re-ranking over Qdrant top-k results",
    "Streaming responses (SSE) to reduce perceived latency",
    "Stronger identity: OTP or OAuth2 instead of email-only",
    "Multi-turn context compression for conversations > 10 turns",
    "Cancellation flow refinement — communicate post-delivery return path",
    "Mobile-responsive frontend & persistent session storage",
])

# Row 3 Col 0 — References
section(c, x0, y, COL_W, R3 - (y - y3), "References", [
    "[1] Dettmers et al. QLoRA: Efficient Finetuning of Quantized LLMs. NeurIPS 2023.",
    "[2] Lewis et al. Retrieval-Augmented Generation for NLP. NeurIPS 2020.",
    "[3] Liu et al. G-Eval: NLG Eval via GPT-4. EMNLP 2023.",
    "[4] Microsoft Phi-4-mini-instruct. HuggingFace Hub, 2024.",
    "[5] Amazon Product Reviews Dataset 2023. Kaggle.",
    "[6] sentence-transformers/all-MiniLM-L6-v2. HuggingFace, 2021.",
], body_size=8.5)

# ── COLUMN 1 ─────────────────────────────────────────────────────────
col = 1; x1 = COL_X0 + col*(COL_W + COL_GAP)

# Evaluation framework
y = bullets(c, x1, y1, COL_W, 4.85, "6.  Evaluation Framework", [
    "50 hand-crafted test cases — 8 intent categories:",
    "  order lookup · refund · cancellation · product recommendation",
    "  policy FAQ · escalation · account info · general greeting",
    "Metrics:",
    "  ROUGE-L — surface lexical overlap with reference answers",
    "  Task Success Rate — binary: correct tool call + grounded response",
    "  G-Eval (1–5) — Gemini 2.5 Flash judge across 5 dimensions:",
    "    Relevance · Faithfulness · Completeness · Tone & Empathy · Groundedness",
    "Identity-sensitive cases pre-seeded with customer_id to bypass ID gate",
])

# Results table
section(c, x1, y, COL_W, 4.50, "7.  Overall Evaluation Results", [
    "  Metric                              Score",
    "  ─────────────────────────────────────────────",
    "  ROUGE-L                              0.191",
    "  Task Success Rate                    72.3 %",
    "  G-Eval Average (1–5)                  3.79",
    "  ─────────────────────────────────────────────",
    "  Relevance                            4.24",
    "  Tone & Empathy                       4.18",
    "  Faithfulness                         3.78",
    "  Groundedness                         3.44",
    "  Completeness                         3.30",
    "  ─────────────────────────────────────────────",
    "  Best category:    Escalation         4.80",
    "  Weakest category: Cancellation       2.73",
], body_size=9.3)

# Row 2 Col 1 — radar chart
image_section(c, x1, y2, COL_W, 4.85, "8.  G-Eval Dimension Radar", CH["radar"])

# Heatmap
image_section(c, x1, y2 + 4.85 + 0.08, COL_W,
              R2 - 4.85 - 0.08, "9.  G-Eval Heatmap: Dimension × Category", CH["heatmap"])

# Row 3 Col 1 — category bar
image_section(c, x1, y3, COL_W, R3 * 0.52, "10.  G-Eval Average by Intent Category", CH["cat_bar"])

# Conclusions
conc_y = y3 + R3 * 0.52 + 0.12
bullets(c, x1, conc_y, COL_W, R3 - R3 * 0.52 - 0.12,
        "13.  Conclusions", [
    "Fine-tuned Phi-4-mini + RAG achieves 72.3 % task success on e-commerce support",
    "Deterministic routing + fallback templates are critical — ROUGE-L alone misleads",
    "Escalation best handled (4.80 G-Eval); cancellation most problematic (2.73)",
    "Identity gate prevents data leakage with minimal friction for legitimate users",
    "RAG grounding reduces hallucination; re-ranking would further improve precision",
])

# ── COLUMN 2 ─────────────────────────────────────────────────────────
col = 2; x2 = COL_X0 + col*(COL_W + COL_GAP)

# Row 1 Col 2 — dual metric
image_section(c, x2, y1, COL_W, 4.65, "11.  Task Success Rate & G-Eval by Category", CH["dual"])

# Scatter plot
image_section(c, x2, y1 + 4.65 + 0.1, COL_W,
              R1 - 4.65 - 0.10, "12.  Task Success vs G-Eval Per Test Case", CH["scatter"])

# Row 2 Col 2 — summary dashboard (full row height)
image_section(c, x2, y2, COL_W, R2, "Summary Dashboard — All Metrics Overview", CH["summary"])

# Row 3 Col 2 — distribution + ack
image_section(c, x2, y3, COL_W, R3 * 0.50, "G-Eval Score Distribution", CH["dist"])

ack_y = y3 + R3 * 0.50 + 0.12
section(c, x2, ack_y, COL_W, R3 - R3 * 0.50 - 0.12, "Acknowledgements", [
    "We thank Prof. Simon Shim for his guidance throughout DATA 298B.",
    "Google Colab provided A100 GPU access for fine-tuning experiments.",
    "HuggingFace Inference Endpoints (A10G) hosted the production model.",
    "Source code: github.com/Ch-Suharsha/teammate-rag",
])

# ── Footer ────────────────────────────────────────────────────────────
ftr_top = H/inch - FTR_H
filled_rect(c, GUTTER, ftr_top, W/inch - 2*GUTTER, FTR_H, NAVY)
text_block(c, GUTTER + 0.3, ftr_top + 0.1, W/inch - 2*GUTTER - 0.6, FTR_H - 0.1,
           "DATA 298B Master’s Project  ·  San José State University  ·  Spring 2025  "
           "·  Advisor: Prof. Simon Shim  ·  github.com/Ch-Suharsha/teammate-rag",
           size=11, color=GOLD, align="center")

# ── Save ──────────────────────────────────────────────────────────────
c.save()
print(f"Saved → {pdf_path}")
