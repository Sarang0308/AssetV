"""Report definitions: each report is an ordered list of blocks, plus parameter validation."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

import pandas as pd

from backend.analytics import engine
from backend.reports.blocks import BLOCKS, ReportContext

PAN_RE = re.compile(r"[A-Z]{5}[0-9]{4}[A-Z]")


class ReportInputError(ValueError):
    """Bad request parameters; the message is safe to show to the user (never contains the PAN)."""


@dataclass(frozen=True)
class ReportDef:
    title: str
    pdf_blocks: tuple[str, ...]
    xlsx_blocks: tuple[str, ...] = ()
    max_pages: int | None = None

    @property
    def formats(self) -> tuple[str, ...]:
        return ("pdf", "xlsx") if self.xlsx_blocks else ("pdf",)


REPORTS = {
    "ca_pack": ReportDef(
        title="Tax / CA Review Pack",
        pdf_blocks=("cover", "income_by_source", "ca_review_items", "expense_summary", "balance_sheet",
                    "data_quality", "disclaimer"),
        xlsx_blocks=("cover", "income_by_source", "ca_review_items", "expense_summary", "balance_sheet", "ledger",
                     "data_quality", "disclaimer"),
    ),
    "monthly": ReportDef(
        title="Monthly Financial Review",
        pdf_blocks=("cover", "verdict", "month_numbers", "what_changed", "biggest_transactions",
                    "upcoming_payments", "recommendations", "data_quality"),
        max_pages=2,
    ),
}


@dataclass
class Report:
    ctx: ReportContext
    definition: ReportDef
    sections: dict[str, dict]       # block name -> data, in block order
    filename_stem: str

    def ordered(self, fmt: str) -> list[dict]:
        names = self.definition.pdf_blocks if fmt == "pdf" else self.definition.xlsx_blocks
        return [{"name": n, "data": self.sections[n]} for n in names]


# ---------------------------------------------------------------- validation
def _resolve_fy(fy: str | None) -> tuple[str, bool]:
    fy = (fy or "2025-26").strip().upper().removeprefix("FY").strip()
    options = {f["fy"]: f["partial"] for f in engine.get_report_periods()["financial_years"]}
    if fy not in options:
        raise ReportInputError(f"fy must be one of {', '.join(options)}.")
    return fy, options[fy]


def _resolve_month(month: str | None) -> str:
    periods = engine.get_report_periods()
    month = (month or periods["last_complete_month"]).strip()
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
        raise ReportInputError("month must use the YYYY-MM format, e.g. 2026-09.")
    target = pd.Period(month, "M")
    first, last = pd.Period(periods["first_month"], "M"), pd.Period(periods["last_month"], "M")
    if not first <= target <= last:
        raise ReportInputError(f"Month {month} is outside the data range {first} to {last}.")
    if target > pd.Period(periods["last_complete_month"], "M"):
        raise ReportInputError(f"Month {month} is not complete yet; the latest complete month is "
                               f"{periods['last_complete_month']}.")
    return month


def _clean_pan(pan: str | None) -> str | None:
    pan = (pan or "").strip().upper()
    if not pan:
        return None
    if not PAN_RE.fullmatch(pan):
        raise ReportInputError("PAN must look like AAAAA9999A (5 letters, 4 digits, 1 letter).")
    return pan


def _clean_name(name: str | None) -> str | None:
    name = " ".join((name or "").split())
    if len(name) > 80:
        raise ReportInputError("name must be 80 characters or fewer.")
    return name or None


# ---------------------------------------------------------------- build
def build_report(report_type: str, fmt: str = "pdf", fy: str | None = None, month: str | None = None,
                 name: str | None = None, pan: str | None = None, hide_details: bool = False) -> Report:
    if report_type not in REPORTS:
        raise ReportInputError(f"type must be one of {', '.join(REPORTS)}.")
    definition = REPORTS[report_type]
    if fmt not in definition.formats:
        raise ReportInputError(f"format={fmt} is not available for type={report_type}; "
                               f"use {' or '.join(definition.formats)}.")

    if report_type == "ca_pack":
        fy, partial = _resolve_fy(fy)
        period, heading, stem = f"FY{fy}", f"FY {fy}", f"FinSight_CA_Pack_FY{fy}"
        name, pan = _clean_name(name), _clean_pan(pan)
    else:
        month = _resolve_month(month)
        partial = False
        period, heading, stem = month, pd.Period(month, "M").strftime("%B %Y"), f"FinSight_Monthly_Review_{month}"
        name = pan = None

    ctx = ReportContext(
        report_type=report_type, title=definition.title, period=period, period_heading=heading,
        period_label=engine.get_cashflow(period)["period"], report_id=uuid4().hex[:10].upper(),
        generated_at=datetime.now().astimezone().strftime("%d %b %Y, %H:%M %Z").strip(),
        partial=partial, hide_details=hide_details, name=name, pan=pan)
    names = definition.pdf_blocks if fmt == "pdf" else definition.xlsx_blocks
    sections = {n: BLOCKS[n](ctx) for n in names}
    return Report(ctx, definition, sections, stem)
