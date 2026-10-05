"""Render the CA pack as an Excel workbook.

The Summary sheet holds live formulas over the other sheets (no pasted totals), so a CA
can edit a ledger/income cell and see the summary update.
"""
from __future__ import annotations

from datetime import date
from io import BytesIO

import xlsxwriter
from xlsxwriter.utility import xl_rowcol_to_cell

from backend.reports.definitions import Report

# ₹ with Indian digit grouping: ₹1,23,45,678.00
INR_FMT = '[>=10000000]"₹"#\\,##\\,##\\,##0.00;[>=100000]"₹"#\\,##\\,##0.00;"₹"#,##0.00'
SOURCES = ("Salary", "Bonuses", "Consulting", "Other")


def render_xlsx(report: Report) -> bytes:
    s = report.sections
    ctx = report.ctx
    buf = BytesIO()
    wb = xlsxwriter.Workbook(buf, {"in_memory": True})
    wb.set_properties({"title": f"{ctx.title} · {ctx.period_heading}", "comments": f"Report {ctx.report_id}"})
    f = {
        "head": wb.add_format({"bold": True, "bg_color": "#E8EEF6", "bottom": 1, "text_wrap": True, "valign": "top"}),
        "inr": wb.add_format({"num_format": INR_FMT}),
        "inr_b": wb.add_format({"num_format": INR_FMT, "bold": True, "top": 1}),
        "pct": wb.add_format({"num_format": "0.0%"}),
        "date": wb.add_format({"num_format": "dd-mmm-yyyy"}),
        "bold": wb.add_format({"bold": True}),
        "title": wb.add_format({"bold": True, "font_size": 14, "font_color": "#2563A8"}),
        "muted": wb.add_format({"font_color": "#6B7280", "italic": True}),
        "wrap": wb.add_format({"text_wrap": True, "valign": "top"}),
    }

    def sheet(name: str, headers: list[str], widths: list[int]):
        ws = wb.add_worksheet(name)
        ws.write_row(0, 0, headers, f["head"])
        ws.freeze_panes(1, 0)
        for col, width in enumerate(widths):
            ws.set_column(col, col, width)
        return ws

    summary = wb.add_worksheet("Summary")  # first tab; filled once the source sheets exist

    # ---- Income: month x source
    inc = s["income_by_source"]
    ws = sheet("Income", ["Month", *SOURCES, "Total"], [12, 16, 16, 16, 14, 16])
    for r, m in enumerate(inc["months"], start=1):
        ws.write(r, 0, m["month"])
        for c, src in enumerate(SOURCES, start=1):
            ws.write_number(r, c, m[src], f["inr"])
        ws.write_formula(r, 5, f"=SUM(B{r + 1}:E{r + 1})", f["inr"], m["total"])
    n_inc = len(inc["months"])
    total_row = n_inc + 1
    ws.write(total_row, 0, "Total", f["bold"])
    for c, key in enumerate((*SOURCES, "total"), start=1):
        col = xl_rowcol_to_cell(0, c)[:-1]
        ws.write_formula(total_row, c, f"=SUM({col}2:{col}{n_inc + 1})", f["inr_b"],
                         sum(m[key] for m in inc["months"]))
    if inc["bonus_credits"]:
        ws.write(total_row + 2, 0, inc["method"], f["muted"])

    # ---- Expenses by category (+ investments and debt, tagged by kind)
    exp = s["expense_summary"]
    ws = sheet("Expenses", ["Category", "Amount", "Transactions", "Kind"], [34, 16, 13, 10])
    rows = [(i["name"], i["amount"], i["transactions"], "Spending") for i in exp["items"]]
    rows += [(m["name"], m["amount"], None, m["kind"]) for m in exp["memo"]]
    for r, (name, amount, count, kind) in enumerate(rows, start=1):
        ws.write(r, 0, name)
        ws.write_number(r, 1, amount, f["inr"])
        if count is not None:
            ws.write_number(r, 2, count)
        ws.write(r, 3, kind)
    n_exp = len(rows)

    # ---- Balance sheet
    bs = s["balance_sheet"]
    ws = sheet("Balance sheet", ["Section", "Item", "Value / outstanding", "Interest rate", "EMI", "Next due"],
               [11, 20, 19, 13, 14, 13])
    r = 1
    for a in bs["assets"]:
        ws.write_row(r, 0, ["Asset", a["name"]])
        ws.write_number(r, 2, a["value"], f["inr"])
        r += 1
    for item in bs["liabilities"]:
        ws.write_row(r, 0, ["Liability", item["type"]])
        ws.write_number(r, 2, item["outstanding"], f["inr"])
        ws.write_number(r, 3, item["rate_pct"] / 100, f["pct"])
        ws.write_number(r, 4, item["emi"], f["inr"])
        ws.write_datetime(r, 5, date.fromisoformat(item["next_due"]), f["date"])
        r += 1
    n_bs = r - 1
    ws.write(r + 1, 0, f"Snapshot as of {bs['as_of']}", f["muted"])

    # ---- Review items: one row per flagged transaction (or per item when details are hidden)
    ws = sheet("Review items", ["Item", "txn_id", "Date", "Description", "Amount", "Note for CA"],
               [34, 11, 13, 26, 15, 80])
    r = 1
    for item in s["ca_review_items"]["items"]:
        txns = item["transactions"]
        if txns is None:
            ws.write_row(r, 0, [item["item"], "(hidden)", "", f"{item['count']} transaction(s)"])
            ws.write_number(r, 4, item["total"], f["inr"])
            ws.write(r, 5, item["note"], f["wrap"])
            r += 1
            continue
        for t in txns:
            ws.write(r, 0, item["item"])
            ws.write(r, 1, t["txn_id"])
            ws.write_datetime(r, 2, date.fromisoformat(t["date"]), f["date"])
            ws.write(r, 3, t["description"])
            ws.write_number(r, 4, t["amount"], f["inr"])
            ws.write(r, 5, item["note"])
            r += 1
    n_rev = r - 1
    if n_rev:
        ws.autofilter(0, 0, n_rev, 5)
    ws.write(r + 1, 0, "Flagged for review only — no tax has been calculated.", f["muted"])

    # ---- Ledger
    ws = sheet("Ledger", ["txn_id", "Cleaned id", "Date", "Category", "Description", "Type", "Kind", "Amount", "Flags"],
               [11, 12, 13, 15, 28, 10, 11, 15, 30])
    led = s["ledger"]["rows"]
    if led is None:
        ws.write(1, 0, "Transaction details hidden for sharing.", f["muted"])
    else:
        for r, t in enumerate(led, start=1):
            ws.write_row(r, 0, [t["original_txn_id"], t["txn_id"]])
            ws.write_datetime(r, 2, date.fromisoformat(t["date"]), f["date"])
            ws.write_row(r, 3, [t["category"], t["description"], t["type"], t["kind"]])
            ws.write_number(r, 7, t["amount"], f["inr"])
            ws.write(r, 8, ", ".join(t["flags"]))
        if led:
            ws.autofilter(0, 0, len(led), 8)

    # ---- Quality log
    dq = s["data_quality"]
    if dq["rows"] is None:
        ws = sheet("Quality log", ["Issue", "Rows", "Decision"], [44, 8, 52])
        for r, q in enumerate(dq["summary"], start=1):
            ws.write_row(r, 0, [q["issue"], q["count"], q["action"]])
    else:
        ws = sheet("Quality log", ["txn_id", "Date", "Issue", "Severity", "Decision", "Reason / detail", "Excluded"],
                   [11, 13, 40, 9, 46, 56, 9])
        for r, q in enumerate(dq["rows"], start=1):
            ws.write(r, 0, q["txn_id"])
            if q["date"]:
                ws.write_datetime(r, 1, date.fromisoformat(q["date"]), f["date"])
            ws.write_row(r, 2, [q["issue"], q["severity"], q["action"], q["detail"], "Yes" if q["excluded"] else "No"])
    if not dq["count"]:
        ws.write(1, 0, dq["empty_message"], f["muted"])

    # ---- Summary: live formulas over the sheets above
    ws = summary
    ws.set_column(0, 0, 42)
    ws.set_column(1, 1, 20)
    ws.write(0, 0, ctx.title, f["title"])
    info = [("Financial year", ctx.period_heading), ("Data covered", ctx.period_label),
            ("Report ID", ctx.report_id), ("Generated", ctx.generated_at)]
    if ctx.name:
        info.insert(0, ("Name", ctx.name))
    for r, (k, v) in enumerate(info, start=1):
        ws.write(r, 0, k, f["muted"])
        ws.write(r, 1, v)
    top = len(info) + 2
    ws.write_row(top, 0, ["Metric", "Amount"], f["head"])
    ws.freeze_panes(top + 1, 0)
    inc_rng = lambda col: f"Income!{col}2:{col}{n_inc + 1}"  # noqa: E731
    kind_sum = lambda kind: f'=SUMIF(Expenses!D2:D{n_exp + 1},"{kind}",Expenses!B2:B{n_exp + 1})'  # noqa: E731
    bs_sum = lambda sec: f"=SUMIF('Balance sheet'!A2:A{n_bs + 1},\"{sec}\",'Balance sheet'!C2:C{n_bs + 1})"  # noqa: E731
    first = top + 2  # Excel row number of the first metric
    # Each formula also gets its result as the cached value: Excel recalculates on open anyway, but
    # viewers that don't (previews, LibreOffice's default) would otherwise show 0.
    by_src = {x["source"]: x["amount"] for x in inc["sources"]}
    memo = {m["kind"]: m["amount"] for m in exp["memo"]}
    total_income = inc["total"]
    metrics = [
        ("Salary income", f"=SUM({inc_rng('B')})", "inr", by_src.get("Salary", 0)),
        ("Bonuses", f"=SUM({inc_rng('C')})", "inr", by_src.get("Bonuses", 0)),
        ("Consulting income", f"=SUM({inc_rng('D')})", "inr", by_src.get("Consulting", 0)),
        ("Other income", f"=SUM({inc_rng('E')})", "inr", by_src.get("Other", 0)),
        ("Total income", f"=SUM(B{first}:B{first + 3})", "inr_b", total_income),
        ("Spending (excl. investments and debt)", kind_sum("Spending"), "inr", exp["spending_total"]),
        ("Investments (counted as saving)", kind_sum("Saving"), "inr", memo["Saving"]),
        ("Debt repayments", kind_sum("Debt"), "inr", memo["Debt"]),
        ("Savings rate", f"=IF(B{first + 4}=0,0,(B{first + 4}-B{first + 5}-B{first + 7})/B{first + 4})", "pct",
         (total_income - exp["spending_total"] - memo["Debt"]) / total_income if total_income else 0),
        ("Total flagged for CA review", f"=SUM('Review items'!E2:E{max(n_rev, 1) + 1})", "inr",
         sum(i["total"] for i in s["ca_review_items"]["items"])),
        ("Total assets", bs_sum("Asset"), "inr", bs["asset_total"]),
        ("Total liabilities", bs_sum("Liability"), "inr", bs["liability_total"]),
        ("Net worth", f"=B{first + 10}-B{first + 11}", "inr_b", bs["asset_total"] - bs["liability_total"]),
    ]
    for i, (label, formula, fmt, cached) in enumerate(metrics):
        ws.write(top + 1 + i, 0, label, f["bold"] if fmt == "inr_b" else None)
        ws.write_formula(top + 1 + i, 1, formula, f[fmt], cached)
    ws.write(top + len(metrics) + 2, 0, s["disclaimer"]["text"], f["muted"])
    if ctx.partial:
        ws.write(top + len(metrics) + 3, 0, "Partial period: data does not cover the full financial year.", f["muted"])

    wb.close()
    return buf.getvalue()
