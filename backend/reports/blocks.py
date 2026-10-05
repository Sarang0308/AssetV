"""Report section blocks.

Each block takes the ReportContext and returns plain data built from the shared
analytics engine. Templates and the Excel writer only lay that data out; they do
no arithmetic. Blocks are shared between reports wherever the content overlaps
(cover, balance sheet, data quality, recommendations).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from backend.analytics import engine

DISCLAIMER = "Summary of transaction data for review by a qualified professional. Not tax advice."
# Committed monthly outflows, left out of "biggest transactions" so discretionary spend stands out.
FIXED_CATEGORIES = ["Housing", "Debt Payment", "Investments", "Insurance"]
COMPARE_MONTHS = 3


@dataclass
class ReportContext:
    report_type: str
    title: str
    period: str                # engine period string, e.g. "FY2025-26" or "2026-09"
    period_heading: str        # what the user asked for, e.g. "FY 2025-26" or "September 2026"
    period_label: str          # months actually covered by data, e.g. "Apr 2025 – Mar 2026"
    report_id: str
    generated_at: str
    partial: bool = False
    hide_details: bool = False
    name: str | None = None
    pan: str | None = field(default=None, repr=False)  # transient: printed on the cover only, never logged


# ---------------------------------------------------------------- shared blocks
def cover(ctx: ReportContext) -> dict:
    return {"title": ctx.title, "period_heading": ctx.period_heading, "period_label": ctx.period_label,
            "partial": ctx.partial, "generated_at": ctx.generated_at, "report_id": ctx.report_id,
            "transaction_count": engine.search_transactions(ctx.period, limit=1)["matches"],
            "name": ctx.name, "pan": ctx.pan, "hide_details": ctx.hide_details,
            "compact": ctx.report_type == "monthly"}


def balance_sheet(ctx: ReportContext) -> dict:
    assets, liab = engine.get_assets(), engine.get_liabilities()
    return {"as_of": assets["as_of"], "assets": assets["items"], "asset_total": assets["total"],
            "liabilities": [{"type": i["type"], "outstanding": i["outstanding"], "rate_pct": i["interest_rate_pct"],
                             "emi": i["emi"], "next_due": i["next_due_date"]} for i in liab["items"]],
            "liability_total": liab["total_outstanding"], "total_emi": liab["total_emi"],
            "net_worth": engine.get_net_worth()["net_worth"]}


def data_quality(ctx: ReportContext) -> dict:
    report = engine.get_data_quality_report(ctx.period)
    rows = [{"txn_id": i["txn_id"], "date": i["date"], "issue": i["issue"], "severity": i["severity"],
             "action": i["action"], "detail": i["detail"], "excluded": i["excluded"]} for i in report["issues"]]
    summary = pd.Series([r["issue"] for r in rows], dtype=object).value_counts()
    return {"count": len(rows),
            "rows": None if ctx.hide_details else rows,
            "summary": [{"issue": k, "count": int(v), "action": next(r["action"] for r in rows if r["issue"] == k)}
                        for k, v in summary.items()],
            "empty_message": "No data issues this month." if ctx.report_type == "monthly"
            else "No cleaning decisions affected rows in this financial year."}


def recommendations(ctx: ReportContext) -> dict:
    recs = engine.get_recommendations()
    return {"items": recs["top_3"], "health_score": recs["health_score"]}


# ---------------------------------------------------------------- CA pack
def income_by_source(ctx: ReportContext) -> dict:
    inc = engine.get_income_by_source(ctx.period)
    return {"sources": inc["sources"], "total": inc["total"], "months": inc["months"], "method": inc["method"],
            "bonus_credits": None if ctx.hide_details else inc["bonus_credits"]}


def ca_review_items(ctx: ReportContext) -> dict:
    items = engine.get_tax_review_items(ctx.period)["items"]
    if ctx.hide_details:
        items = [{**i, "transactions": None} for i in items]
    return {"items": items}


def expense_summary(ctx: ReportContext) -> dict:
    spend = engine.get_spending_breakdown(ctx.period, top_n=100)
    totals = engine.get_cashflow(ctx.period)["totals"]
    return {"items": spend["items"], "spending_total": spend["total"], "monthly_average": spend["monthly_average"],
            "memo": [{"name": "Investments (counted as saving)", "kind": "Saving", "amount": totals["investments"]},
                     {"name": "Debt repayments (EMIs, credit card)", "kind": "Debt", "amount": totals["debt_payments"]}]}


def ledger(ctx: ReportContext) -> dict:
    return {"rows": None if ctx.hide_details else engine.get_report_transactions(ctx.period)}


def disclaimer(ctx: ReportContext) -> dict:
    return {"text": DISCLAIMER}


# ---------------------------------------------------------------- monthly review
def month_numbers(ctx: ReportContext) -> dict:
    row = engine.get_cashflow(ctx.period)["rows"][0]
    return {"income": row["income"], "spending": row["spending"], "investments": row["investments"],
            "debt_payments": row["debt_payments"], "net_cash_flow": row["net_cash_flow"],
            "savings_rate_pct": row["savings_rate_pct"]}


def what_changed(ctx: ReportContext) -> dict:
    month = pd.Period(ctx.period, "M")
    first = pd.Period(engine.get_report_periods()["first_month"], "M")
    start = max(month - COMPARE_MONTHS, first)
    if start >= month:
        return {"available": False, "months_compared": 0, "rows": []}
    prior = f"{start}:{month - 1}"
    cmp = engine.compare_periods(prior, ctx.period, exclude_categories=["Investments", "Debt Payment"])
    rows = sorted(({"category": r["name"], "this_month": r["b"], "prior_average": r["a"], "change": r["change"],
                    "change_pct": r["change_pct"]} for r in cmp["rows"] if r["a"] or r["b"]),
                  key=lambda r: -abs(r["change"]))
    total = cmp["total_outflow"]
    return {"available": True, "months_compared": len(pd.period_range(start, month - 1, freq="M")),
            "prior_label": cmp["period_a"], "rows": rows,
            "total": {"this_month": total["b"], "prior_average": total["a"], "change": total["b"] - total["a"],
                      "change_pct": round((total["b"] - total["a"]) / total["a"] * 100, 1) if total["a"] else None}}


def verdict(ctx: ReportContext) -> dict:
    """One-line verdict from fixed rules over the month's metrics."""
    n, changed, dues = month_numbers(ctx), what_changed(ctx), engine.get_upcoming_payments()
    rate = n["savings_rate_pct"]
    if not n["income"]:
        tone, text = "warning", "No income was recorded this month"
    elif n["net_cash_flow"] < 0:
        tone, text = "critical", f"Cash-negative month: outflows exceeded income, savings rate {rate}%"
    elif rate >= 30:
        tone, text = "good", f"Strong month: you kept {rate}% of income (target 30%)"
    elif rate >= 15:
        tone, text = "warning", f"Steady month: savings rate {rate}%, below the 30% target"
    else:
        tone, text = "critical", f"Tight month: savings rate only {rate}%"
    pct = changed["total"]["change_pct"] if changed["available"] else None
    if pct is not None and abs(pct) >= 5:
        direction = "above" if pct > 0 else "below"
        text += f"; spending ran {abs(pct)}% {direction} the previous {changed['months_compared']}-month average"
        ups = [r for r in changed["rows"] if r["change"] > 0]
        if pct > 0 and ups:
            text += f", led by {ups[0]['category']}"
    elif pct is not None:
        text += "; spending was in line with recent months"
    if not dues["covered"]:
        text += ". Upcoming dues exceed the current-account balance"
    return {"text": text + ".", "tone": tone}


def biggest_transactions(ctx: ReportContext) -> dict:
    found = engine.search_transactions(ctx.period, txn_type="expense", exclude_categories=FIXED_CATEGORIES, limit=5)
    return {"rows": None if ctx.hide_details else found["rows"], "excluded_categories": FIXED_CATEGORIES}


def upcoming_payments(ctx: ReportContext) -> dict:
    return engine.get_upcoming_payments(days=10)


BLOCKS = {f.__name__: f for f in (
    cover, balance_sheet, data_quality, recommendations, income_by_source, ca_review_items, expense_summary, ledger,
    disclaimer, verdict, month_numbers, what_changed, biggest_transactions, upcoming_payments)}
