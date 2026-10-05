"""Render a built report to PDF: Jinja2 block templates -> HTML -> WeasyPrint."""
from __future__ import annotations

import base64
from io import BytesIO
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.ticker import FuncFormatter  # noqa: E402
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape  # noqa: E402

from backend.reports.definitions import Report  # noqa: E402

TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "templates" / "reports"
INCREASE, DECREASE = "#c8432f", "#1f8a5b"


class ReportLayoutError(RuntimeError):
    pass


def inr(value, decimals: int = 0) -> str:
    """₹ with Indian digit grouping (₹12,34,567). Formatting only — no arithmetic on report figures."""
    if value is None:
        return "–"
    text = f"{abs(value):.{decimals}f}"
    whole, _, frac = text.partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        whole = ",".join(([head] if head else []) + groups + [tail])
    return ("-" if value < 0 else "") + "₹" + whole + ("." + frac if frac else "")


def pct(value, signed: bool = False) -> str:
    if value is None:
        return "–"
    return f"{value:+.1f}%" if signed else f"{value:.1f}%"


def rate(value) -> str:
    """Interest rate as given (8.35%, 32%), without rounding away precision."""
    return "–" if value is None else f"{value:.2f}".rstrip("0").rstrip(".") + "%"


ENV = Environment(loader=FileSystemLoader(TEMPLATE_DIR), autoescape=select_autoescape(("html",)),
                  undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
ENV.filters.update(inr=inr, pct=pct, rate=rate)


def change_chart(rows: list[dict]) -> str:
    """Horizontal bars of category change vs the prior average; increases and decreases in different colours."""
    rows = rows[:10][::-1]
    fig, ax = plt.subplots(figsize=(7.4, 0.32 * max(len(rows), 3) + 0.5), dpi=150)
    if rows:
        values = [r["change"] for r in rows]
        ax.barh([r["category"] for r in rows], values, color=[INCREASE if v > 0 else DECREASE for v in values],
                height=0.62)
        ax.axvline(0, color="#7a7f87", linewidth=0.8)
        ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: inr(v)))
        ax.set_xlabel("Change vs previous-months average", fontsize=7, color="#4b5563")
        ax.tick_params(labelsize=7, colors="#374151", length=0)
        ax.spines[["top", "right", "left"]].set_visible(False)
        ax.grid(axis="x", alpha=0.25)
        ax.set_axisbelow(True)
    else:
        ax.text(0.5, 0.5, "No spending to compare", ha="center", va="center", fontsize=8)
        ax.set_axis_off()
    fig.tight_layout()
    buf = BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def render_html(report: Report) -> str:
    sections = report.ordered("pdf")
    for s in sections:
        if s["name"] == "what_changed" and s["data"]["available"]:
            s["data"] = {**s["data"], "chart": change_chart(s["data"]["rows"])}
    return ENV.get_template("base.html").render(ctx=report.ctx, sections=sections)


def render_pdf(report: Report) -> bytes:
    from weasyprint import HTML  # imported lazily: needs system Pango libraries

    document = HTML(string=render_html(report), base_url=str(TEMPLATE_DIR)).render()
    limit = report.definition.max_pages
    if limit and len(document.pages) > limit:
        raise ReportLayoutError(f"{report.definition.title} rendered {len(document.pages)} pages (limit {limit}).")
    return document.write_pdf()
