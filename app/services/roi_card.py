"""Feature 5 — the shareable ROI proof card (1200x628 PNG).

TWO RENDERERS, ONE OUTPUT. The card is authored once, as the Jinja2 template
at app/templates/roi_card.html.

  weasyprint   the renderer the feature specification names. It is HTML ->
               PDF; WeasyPrint dropped PNG output in v53 (`write_png` was
               removed), so the PDF's single page is rastered to PNG with
               pypdfium2, a pure wheel with no system dependencies.
  pillow       a direct draw of the same layout, used when WeasyPrint (or its
               native Pango/cairo libraries, or pypdfium2) is not importable.

WHY BOTH. WeasyPrint needs system libraries that a slim container image and a
Windows dev machine routinely do not have, and it fails at IMPORT time when
they are missing. Without the second path this endpoint would 500 on a
perfectly healthy deployment, and the founder's proof card -- a retention
artefact whose entire job is to be forwarded -- would be the one thing that
does not work. The output of both paths is the same size, the same six tiles
and the same wording; the Pillow one is plainer.

`render_card_png` reports which path ran in the logs and through
`last_renderer()`, so "why does my card look different" has an answer.
"""

import io
import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger(__name__)

CARD_WIDTH = 1200
CARD_HEIGHT = 628
TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
TEMPLATE_NAME = "roi_card.html"

_last_renderer: str | None = None


def last_renderer() -> str | None:
    """Which path rendered the most recent card: "weasyprint" or "pillow"."""
    return _last_renderer


# --------------------------------------------------------------------------
# Presentation
# --------------------------------------------------------------------------


def _money(value) -> str:
    """A Decimal as a compact, readable amount: $12.4k, $1.2M, $840."""
    try:
        amount = Decimal(str(value or 0))
    except Exception:  # noqa: BLE001
        amount = Decimal(0)
    negative = amount < 0
    amount = abs(amount)
    if amount >= 1_000_000:
        text = f"${amount / Decimal(1_000_000):.1f}M"
    elif amount >= 1_000:
        text = f"${amount / Decimal(1_000):.1f}k"
    else:
        text = f"${amount:.0f}"
    return ("-" + text) if negative else text


def _hours(value) -> str:
    try:
        hours = float(value or 0)
    except (TypeError, ValueError):
        hours = 0.0
    return f"{hours:.0f}h" if hours >= 10 else f"{hours:.1f}h"


def card_context(metrics: dict, campaign_name: str, date_from: date,
                 date_to: date) -> dict:
    """Everything the template renders, already formatted for display.

    Kept separate from rendering so both renderers show identical text and so
    the formatting can be asserted without producing an image. Never raises:
    a missing metric formats as its zero.
    """
    return {
        "campaign_name": (campaign_name or "Campaign")[:60],
        "date_from": date_from.strftime("%d %b %Y"),
        "date_to": date_to.strftime("%d %b %Y"),
        "day_count": (date_to - date_from).days + 1,
        "meetings_booked": str(int(metrics.get("meetings_booked") or 0)),
        "pipeline_value": _money(metrics.get("pipeline_value")),
        "messages_sent": f"{int(metrics.get('messages_sent') or 0):,}",
        "reply_rate": f"{float(metrics.get('reply_rate') or 0):.1f}%",
        "time_saved_hours": _hours(metrics.get("time_saved_hours")),
        "revenue_attributed": _money(metrics.get("revenue_attributed")),
        "generated_on": datetime.now(timezone.utc).strftime("%d %b %Y"),
    }


def render_card_html(context: dict) -> str:
    """The card as HTML. Raises if the template is missing or malformed."""
    from jinja2 import Environment, FileSystemLoader, select_autoescape  # noqa: PLC0415

    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        # The campaign name is user-supplied and goes into the markup.
        autoescape=select_autoescape(["html"]),
    )
    return env.get_template(TEMPLATE_NAME).render(**context)


# --------------------------------------------------------------------------
# Renderers
# --------------------------------------------------------------------------


def _render_weasyprint(context: dict) -> bytes:
    """HTML -> PDF (WeasyPrint) -> PNG (pypdfium2). Raises when unavailable."""
    import pypdfium2  # noqa: PLC0415
    from weasyprint import HTML  # noqa: PLC0415

    pdf = HTML(string=render_card_html(context),
               base_url=str(TEMPLATE_DIR)).write_pdf()
    document = pypdfium2.PdfDocument(pdf)
    try:
        page = document[0]
        # The @page box is declared in CSS pixels, which WeasyPrint writes as
        # points at 96dpi; scale back up so the PNG is exactly 1200x628.
        scale = CARD_WIDTH / page.get_width()
        image = page.render(scale=scale).to_pil().convert("RGB")
        if image.size != (CARD_WIDTH, CARD_HEIGHT):
            image = image.resize((CARD_WIDTH, CARD_HEIGHT))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        document.close()


_INK = (16, 24, 40)
_MUTED = (102, 112, 133)
_FAINT = (152, 162, 179)
_LINE = (228, 231, 236)

_TILES = (
    ("meetings_booked", "MEETINGS BOOKED", None),
    ("pipeline_value", "PIPELINE VALUE", "open + won, as of today"),
    ("messages_sent", "MESSAGES SENT", None),
    ("reply_rate", "REPLY RATE", None),
    ("time_saved_hours", "HOURS SAVED", None),
    ("revenue_attributed", "REVENUE ATTRIBUTED", None),
)


def _font(size: int, bold: bool = False):
    """A real sans face when the platform has one, else Pillow's bitmap default."""
    from PIL import ImageFont  # noqa: PLC0415

    candidates = (["segoeuib.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf",
                   "Helvetica-Bold.ttf"] if bold else
                  ["segoeui.ttf", "arial.ttf", "DejaVuSans.ttf", "Helvetica.ttf"])
    for name in candidates:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _render_pillow(context: dict) -> bytes:
    """Draw the same card directly. No system libraries beyond Pillow."""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), "white")
    draw = ImageDraw.Draw(image)
    pad_x, pad_y = 52, 44

    # ---- header ----
    draw.rounded_rectangle([pad_x, pad_y, pad_x + 52, pad_y + 52], radius=10,
                           outline=_INK, width=2)
    draw.text((pad_x + 13, pad_y + 14), "LP", font=_font(24, bold=True), fill=_INK)
    draw.text((pad_x + 70, pad_y + 4), "OUTBOUND RESULTS", font=_font(13, bold=True),
              fill=_MUTED)
    draw.text((pad_x + 70, pad_y + 22), context["campaign_name"][:40],
              font=_font(30, bold=True), fill=_INK)

    span = f"{context['date_from']} - {context['date_to']}"
    days = f"{context['day_count']} day" + ("" if context["day_count"] == 1 else "s")
    right = CARD_WIDTH - pad_x
    span_font, days_font = _font(15, bold=True), _font(14)
    draw.text((right - draw.textlength(span, font=span_font), pad_y + 4), span,
              font=span_font, fill=_INK)
    draw.text((right - draw.textlength(days, font=days_font), pad_y + 26), days,
              font=days_font, fill=_MUTED)

    header_y = pad_y + 72
    draw.line([pad_x, header_y, right, header_y], fill=_INK, width=2)

    # ---- 2 x 3 tiles ----
    grid_top, grid_bottom = header_y + 22, CARD_HEIGHT - pad_y - 58
    gap = 16
    tile_w = (right - pad_x - gap * 2) / 3
    tile_h = (grid_bottom - grid_top - gap) / 2
    value_font, label_font, note_font = (_font(42, bold=True), _font(13, bold=True),
                                         _font(11))

    for index, (key, label, note) in enumerate(_TILES):
        column, row = index % 3, index // 3
        x0 = pad_x + column * (tile_w + gap)
        y0 = grid_top + row * (tile_h + gap)
        draw.rounded_rectangle([x0, y0, x0 + tile_w, y0 + tile_h], radius=12,
                               outline=_LINE, width=1)
        # Centre the whole text block, not just its first line: a tile with a
        # note is one line taller and would otherwise sit higher than the
        # tiles beside it.
        block_h = 42 + 10 + 16 + (20 if note else 0)
        text_x, text_y = x0 + 22, y0 + (tile_h - block_h) / 2
        draw.text((text_x, text_y), context[key], font=value_font, fill=_INK)
        draw.text((text_x, text_y + 52), label, font=label_font, fill=_MUTED)
        if note:
            draw.text((text_x, text_y + 72), note, font=note_font, fill=_FAINT)

    # ---- footer ----
    footer_y = CARD_HEIGHT - pad_y - 34
    draw.line([pad_x, footer_y, right, footer_y], fill=_LINE, width=1)
    footer_font, brand_font = _font(13), _font(13, bold=True)
    draw.text((pad_x, footer_y + 12), f"Generated {context['generated_on']}",
              font=footer_font, fill=_MUTED)
    brand = "Powered by Vectorae"
    draw.text((right - draw.textlength(brand, font=brand_font), footer_y + 12),
              brand, font=brand_font, fill=_INK)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def render_card_png(metrics: dict, campaign_name: str, date_from: date,
                    date_to: date) -> bytes:
    """The proof card as PNG bytes, 1200x628.

    WHAT IT DOES. Formats the metrics, renders the shared Jinja2 template
    through WeasyPrint when it is importable, and falls back to the Pillow
    renderer otherwise. Records which path ran (see last_renderer()).

    WHAT IT RETURNS. PNG bytes. Always -- there is no "no card" outcome short
    of Pillow itself being broken.

    WHAT IT NEVER RAISES. Anything WeasyPrint raises. A missing native
    library, a template error or a raster failure falls through to Pillow,
    logged at WARNING. Only a failure of the fallback itself propagates, and
    that is a broken install rather than a runtime condition.
    """
    global _last_renderer

    context = card_context(metrics, campaign_name, date_from, date_to)
    try:
        png = _render_weasyprint(context)
        _last_renderer = "weasyprint"
        return png
    except Exception as exc:  # noqa: BLE001 -- see the docstring
        logger.warning("ROI card: WeasyPrint path unavailable (%s: %s) -- "
                       "drawing the card with Pillow instead",
                       type(exc).__name__, exc)
    png = _render_pillow(context)
    _last_renderer = "pillow"
    return png
