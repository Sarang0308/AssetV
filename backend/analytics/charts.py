"""Build frontend chart specs from cached tool results.

The LLM only chooses *which* result to plot and *how* (chart type / title);
the numbers are copied here straight from the analytics output, never through
the model.

Spec shape (rendered by frontend/app.js):
  {type, title, subtitle?, unit, labels: [...], series: [{name, data: [...], kind?}],
   rows?/columns? (table), value?/max?/bands? (gauge), items? (kpi), matrix? (heatmap)}
"""
from __future__ import annotations

CHART_TYPES = ["line", "area", "bar", "hbar", "stacked_bar", "grouped_bar", "combo", "pie", "doughnut",
               "gauge", "waterfall", "scatter", "radar", "heatmap", "table", "kpi"]

# tool -> (default chart, allowed charts)
CHART_OPTIONS: dict[str, tuple[str, list[str]]] = {
    "get_financial_summary": ("kpi", ["kpi", "table"]),
    "get_cashflow": ("combo", ["combo", "line", "area", "bar", "stacked_bar", "grouped_bar", "table"]),
    "get_spending_breakdown": ("doughnut", ["doughnut", "pie", "bar", "hbar", "table"]),
    "compare_periods": ("grouped_bar", ["grouped_bar", "hbar", "bar", "table"]),
    "get_category_trend": ("line", ["line", "area", "stacked_bar", "grouped_bar", "table"]),
    "get_spending_heatmap": ("heatmap", ["heatmap", "table"]),
    "get_assets": ("doughnut", ["doughnut", "pie", "bar", "hbar", "table"]),
    "get_liabilities": ("hbar", ["hbar", "bar", "doughnut", "table"]),
    "get_net_worth": ("waterfall", ["waterfall", "bar", "table", "kpi"]),
    "detect_anomalies": ("scatter", ["scatter", "table"]),
    "get_health_score": ("gauge", ["gauge", "radar", "hbar", "table"]),
    "get_recommendations": ("table", ["table"]),
    "simulate_scenario": ("grouped_bar", ["grouped_bar", "table", "kpi"]),
    "forecast": ("area", ["area", "line", "table"]),
    "search_transactions": ("table", ["table", "bar", "hbar"]),
    "get_data_quality_report": ("table", ["table", "doughnut"]),
}


class ChartError(ValueError):
    pass


def _items_chart(t, title, items, label_key, value_key, unit="INR", extra_cols=()):
    if t == "table":
        cols = [label_key, value_key, *extra_cols]
        return {"type": "table", "title": title, "columns": cols, "rows": [[i.get(c) for c in cols] for i in items]}
    return {"type": t, "title": title, "unit": unit, "labels": [i[label_key] for i in items],
            "series": [{"name": title, "data": [i[value_key] for i in items]}]}


def build_chart(tool: str, data: dict, chart_type: str | None = None, title: str | None = None,
                series_filter: list[str] | None = None) -> dict:
    default, allowed = CHART_OPTIONS.get(tool, (None, []))
    if not allowed:
        raise ChartError(f"Tool '{tool}' has no chart representation.")
    t = chart_type or default
    if t not in allowed:
        raise ChartError(f"Chart type '{t}' not supported for {tool}. Allowed: {allowed}")
    spec = _build(tool, data, t, title)
    if series_filter and spec.get("series"):
        keep = [s for s in spec["series"] if s["name"].lower() in [f.lower() for f in series_filter]]
        if keep:
            spec["series"] = keep
    return spec


def _build(tool, d, t, title):  # noqa: C901 — one branch per tool keeps this readable
    if tool == "get_financial_summary":
        kpis = [("Net worth", d["net_worth"], "INR"), ("Avg monthly income", d["avg_monthly_income"], "INR"),
                ("Avg monthly spending", d["avg_monthly_spending"], "INR"), ("Savings rate", d["savings_rate_pct"], "%"),
                ("Debt-to-income", d["debt_to_income_pct"], "%"), ("Emergency fund", d["emergency_fund_months"], "months")]
        if t == "table":
            return {"type": "table", "title": title or "Financial summary", "columns": ["metric", "value"],
                    "rows": [[k, v] for k, v, _ in kpis]}
        return {"type": "kpi", "title": title or "Financial snapshot", "subtitle": d["basis"],
                "items": [{"label": k, "value": v, "unit": u} for k, v, u in kpis]}

    if tool == "get_cashflow":
        rows = d["rows"]
        labels = [r["period"] for r in rows]
        title = title or f"Cash flow · {d['period']}"
        if t == "table":
            cols = ["period", "income", "spending", "debt_payments", "investments", "net_cash_flow", "savings_rate_pct"]
            return {"type": "table", "title": title, "columns": cols, "rows": [[r[c] for c in cols] for r in rows]}
        series = [{"name": "Income", "data": [r["income"] for r in rows]},
                  {"name": "Spending", "data": [r["spending"] for r in rows]},
                  {"name": "Debt payments", "data": [r["debt_payments"] for r in rows]},
                  {"name": "Investments", "data": [r["investments"] for r in rows]},
                  {"name": "Net cash flow", "data": [r["net_cash_flow"] for r in rows]}]
        if t == "combo":
            for s in series:
                s["kind"] = "line" if s["name"] in ("Income", "Net cash flow") else "bar"
            series = [series[0], series[1], series[2], series[3], series[4]]
            return {"type": "combo", "title": title, "unit": "INR", "labels": labels, "series": series, "stacked_bars": True}
        if t == "stacked_bar":
            series = series[1:4]
        return {"type": t, "title": title, "unit": "INR", "labels": labels, "series": series}

    if tool == "get_spending_breakdown":
        return _items_chart(t, title or f"Spending by {d['group_by']} · {d['period']}", d["items"], "name", "amount",
                            extra_cols=("share_pct", "transactions"))

    if tool == "compare_periods":
        rows = [r for r in d["rows"] if r["a"] or r["b"]]
        title = title or f"{d['period_a']} vs {d['period_b']} ({d['basis']})"
        if t == "table":
            cols = ["name", "a", "b", "change", "change_pct"]
            return {"type": "table", "title": title, "columns": ["category", d["period_a"], d["period_b"], "change", "change %"],
                    "rows": [[r[c] for c in cols] for r in rows]}
        if t == "hbar":
            return {"type": "hbar", "title": title + " · change", "unit": "INR", "labels": [r["name"] for r in rows],
                    "series": [{"name": "Change", "data": [r["change"] for r in rows]}], "diverging": True}
        return {"type": "grouped_bar" if t != "bar" else "bar", "title": title, "unit": "INR",
                "labels": [r["name"] for r in rows],
                "series": [{"name": d["period_a"], "data": [r["a"] for r in rows]},
                           {"name": d["period_b"], "data": [r["b"] for r in rows]}]}

    if tool == "get_category_trend":
        title = title or f"Monthly trend · {d['period']}"
        if t == "table":
            return {"type": "table", "title": title, "columns": ["month", *[s["name"] for s in d["series"]]],
                    "rows": [[m, *[s["values"][i] for s in d["series"]]] for i, m in enumerate(d["months"])]}
        return {"type": t, "title": title, "unit": "INR", "labels": d["months"],
                "series": [{"name": s["name"], "data": s["values"]} for s in d["series"]]}

    if tool == "get_spending_heatmap":
        title = title or f"Spending heatmap · {d['period']}"
        if t == "table":
            return {"type": "table", "title": title, "columns": ["category", *d["months"]],
                    "rows": [[c, *row] for c, row in zip(d["categories"], d["values"])]}
        return {"type": "heatmap", "title": title, "unit": "INR", "labels": d["months"], "rows_labels": d["categories"],
                "matrix": d["values"]}

    if tool == "get_assets":
        return _items_chart(t, title or f"Asset allocation · ₹{d['total']:,}", d["items"], "name", "value",
                            extra_cols=("share_pct",))

    if tool == "get_liabilities":
        items = d["items"]
        title = title or "Liabilities"
        if t == "table":
            cols = ["type", "outstanding", "interest_rate_pct", "emi", "next_due_date", "annual_interest_cost_now",
                    "months_to_payoff_at_current_emi"]
            return {"type": "table", "title": title, "columns": cols, "rows": [[i[c] for c in cols] for i in items]}
        if t == "doughnut":
            return {"type": "doughnut", "title": title + " · outstanding", "unit": "INR",
                    "labels": [i["type"] for i in items], "series": [{"name": "Outstanding", "data": [i["outstanding"] for i in items]}]}
        return {"type": t, "title": title + " · outstanding vs annual interest", "unit": "INR",
                "labels": [f"{i['type']} ({i['interest_rate_pct']}%)" for i in items],
                "series": [{"name": "Outstanding", "data": [i["outstanding"] for i in items]},
                           {"name": "Annual interest", "data": [i["annual_interest_cost_now"] for i in items]}]}

    if tool == "get_net_worth":
        title = title or f"Net worth · ₹{d['net_worth']:,}"
        if t == "kpi":
            return {"type": "kpi", "title": title, "items": [
                {"label": "Total assets", "value": d["total_assets"], "unit": "INR"},
                {"label": "Total liabilities", "value": d["total_liabilities"], "unit": "INR"},
                {"label": "Net worth", "value": d["net_worth"], "unit": "INR"},
                {"label": "Debt-to-asset", "value": d["debt_to_asset_pct"], "unit": "%"}]}
        if t == "table":
            return {"type": "table", "title": title, "columns": ["item", "amount"],
                    "rows": [[a["name"], a["value"]] for a in d["assets"]] + [[l["name"], -l["value"]] for l in d["liabilities"]]
                    + [["Net worth", d["net_worth"]]]}
        if t == "bar":
            return {"type": "bar", "title": title, "unit": "INR", "labels": ["Assets", "Liabilities", "Net worth"],
                    "series": [{"name": "Amount", "data": [d["total_assets"], d["total_liabilities"], d["net_worth"]]}]}
        steps = [{"label": a["name"], "value": a["value"]} for a in d["assets"]] + \
                [{"label": l["name"], "value": -l["value"]} for l in d["liabilities"]]
        return {"type": "waterfall", "title": title, "unit": "INR", "steps": steps, "total_label": "Net worth"}

    if tool == "detect_anomalies":
        title = title or f"Anomalies · {d['period']}"
        if t == "table":
            rows = [[a["date"], a["description"], a["amount"], a.get("typical_amount"), a["type"]] for a in d["transaction_anomalies"]]
            rows += [[s["month"], s["category"], s["amount"], s["trailing_6m_avg"], "category_spike"] for s in d["category_spikes"]]
            rows += [[q["date"], q["description"], q["amount"], None, ", ".join(q["flags"])] for q in d["data_quality_flags"]]
            return {"type": "table", "title": title, "columns": ["date", "what", "amount", "typical", "type"], "rows": rows}
        pts = d.get("_scatter", [])
        return {"type": "scatter", "title": title + " · spending transactions", "unit": "INR",
                "series": [{"name": "Normal", "data": [p for p in pts if not p["flagged"]]},
                           {"name": "Flagged", "data": [p for p in pts if p["flagged"]]}]}

    if tool == "get_health_score":
        comps = d["components"]
        title = title or f"Financial health · {d['score']}/100 ({d['grade']})"
        if t == "gauge":
            return {"type": "gauge", "title": title, "value": d["score"], "max": 100, "grade": d["grade"],
                    "bands": [[50, "Needs attention"], [70, "Fair"], [85, "Good"], [100, "Excellent"]],
                    "breakdown": [{"label": c["name"], "points": c["points"], "weight": c["weight"], "value": c["value"]} for c in comps]}
        if t == "table":
            cols = ["name", "value", "benchmark", "points", "weight"]
            return {"type": "table", "title": title, "columns": cols, "rows": [[c[k] for k in cols] for c in comps]}
        return {"type": t, "title": title, "unit": "%", "labels": [c["name"] for c in comps],
                "series": [{"name": "Component score", "data": [c["score_pct"] for c in comps]}], "max": 100}

    if tool == "get_recommendations":
        recs = d["top_3"] + d["additional"]
        return {"type": "table", "title": title or "Recommended actions", "columns": ["#", "action", "why", "impact"],
                "rows": [[i + 1, r["title"], r["why"], r["impact"]] for i, r in enumerate(recs)]}

    if tool == "simulate_scenario":
        keys = [("savings_rate_pct", "Savings rate %"), ("debt_to_income_pct", "Debt-to-income %"),
                ("emergency_fund_months", "Emergency fund (months)"), ("debt_to_asset_pct", "Debt-to-asset %"),
                ("health_score", "Health score")]
        title = title or "What-if scenario: before vs after"
        if t == "table":
            return {"type": "table", "title": title, "columns": ["metric", "before", "after"],
                    "rows": [[lbl, d["before"][k], d["after"][k]] for k, lbl in keys + [("monthly_surplus", "Monthly surplus ₹")]]}
        if t == "kpi":
            return {"type": "kpi", "title": title, "items": [
                {"label": lbl, "value": d["after"][k], "unit": "", "delta": round(d["after"][k] - d["before"][k], 1)} for k, lbl in keys]}
        return {"type": "grouped_bar", "title": title, "unit": "", "labels": [lbl for _, lbl in keys],
                "series": [{"name": "Before", "data": [d["before"][k] for k, _ in keys]},
                           {"name": "After", "data": [d["after"][k] for k, _ in keys]}]}

    if tool == "forecast":
        rows = d["rows"]
        title = title or f"{d['horizon_months']}-month net worth projection"
        if t == "table":
            return {"type": "table", "title": title, "columns": ["month", "assets", "liabilities", "net_worth"],
                    "rows": [[r["month"], r["assets"], r["liabilities"], r["net_worth"]] for r in rows]}
        return {"type": t, "title": title, "unit": "INR", "labels": [r["month"] for r in rows],
                "series": [{"name": "Net worth", "data": [r["net_worth"] for r in rows]},
                           {"name": "Liabilities", "data": [r["liabilities"] for r in rows]}]}

    if tool == "search_transactions":
        rows = d["rows"]
        title = title or f"Transactions · {d['matches']} matches"
        if t == "table":
            cols = ["date", "category", "description", "amount", "type", "flags"]
            return {"type": "table", "title": title, "columns": cols, "rows": [[r[c] for c in cols] for r in rows]}
        return {"type": t, "title": title, "unit": "INR", "labels": [f"{r['description']} ({r['date']})" for r in rows],
                "series": [{"name": "Amount", "data": [r["amount"] for r in rows]}]}

    if tool == "get_data_quality_report":
        title = title or f"Data quality · {d['issues_found']} issues handled"
        if t == "doughnut":
            return {"type": "doughnut", "title": title, "unit": "", "labels": list(d["by_severity"].keys()),
                    "series": [{"name": "Issues", "data": list(d["by_severity"].values())}]}
        return {"type": "table", "title": title, "columns": ["txn_id", "issue", "severity", "action", "detail"],
                "rows": [[i["txn_id"], i["issue"], i["severity"], i["action"], i["detail"]] for i in d["issues"]]}

    raise ChartError(f"No chart builder for {tool}")
