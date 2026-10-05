"""Tool registry: JSON schemas for the LLM + dispatch to the analytics engine.

Data tools return a `result_id`. The agent then calls `render_chart` with that
id to attach a visualization — the chart is built server-side from the cached
result, so the LLM never re-types numbers.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field

from backend.analytics import engine
from backend.analytics.charts import CHART_OPTIONS, CHART_TYPES, ChartError, build_chart
from backend.analytics.periods import PeriodError

CATEGORIES = ["Housing", "Food", "Transport", "Shopping", "Entertainment", "Healthcare", "Personal Care", "Utilities",
              "Insurance", "Education", "Travel", "Other", "Investments", "Debt Payment"]

# Kept terse on purpose: declarations are re-sent on every model call. The period
# grammar and category list are explained once in the agents' system prompts.
_period = {"type": "string", "description": "Period, e.g. last_6_months"}
_cats = {"type": "array", "items": {"type": "string"}}


def _tool(name, description, props=None, required=None):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = [
    _tool("get_financial_summary",
          "Trailing-12m KPIs: avg monthly income/spending/EMIs/investments, savings rate, DTI, emergency months, net worth."),
    _tool("get_cashflow",
          "Income, spending, debt payments, investments, net cash flow and savings rate per month/quarter/year.",
          {"period": _period, "granularity": {"type": "string", "enum": ["month", "quarter", "year"]},
           "exclude_categories": _cats,
           "exclude_anomalies": {"type": "boolean"}}),
    _tool("get_spending_breakdown",
          "Spending by category or description with share %. Excludes investments/EMIs unless include_investments_and_debt.",
          {"period": _period, "group_by": {"type": "string", "enum": ["category", "description"]},
           "categories": _cats,
           "exclude_categories": _cats, "include_investments_and_debt": {"type": "boolean"},
           "exclude_anomalies": {"type": "boolean"}, "top_n": {"type": "integer"}}),
    _tool("compare_periods",
          "Outflows by category between two periods (per-month averages), with biggest increases/decreases.",
          {"period_a": _period, "period_b": _period, "group_by": {"type": "string", "enum": ["category", "description"]},
           "normalize_monthly": {"type": "boolean"}, "exclude_categories": _cats},
          ["period_a", "period_b"]),
    _tool("get_category_trend",
          "Monthly series for categories (default top 5).",
          {"categories": _cats, "period": _period, "group_by": {"type": "string", "enum": ["category", "description"]}}),
    _tool("get_spending_heatmap", "Category x month spending matrix (seasonality).", {"period": _period}),
    _tool("get_assets",
          "Asset allocation, optionally filtered (types or classes, e.g. Property / Real Estate).",
          {"exclude_types": {"type": "array", "items": {"type": "string"}},
           "min_value": {"type": "number"}, "group_by": {"type": "string", "enum": ["type", "asset_class"]}}),
    _tool("get_liabilities",
          "Loans: rate, EMI, due date, interest cost, payoff months, missed EMIs."),
    _tool("get_debt_alerts",
          "Debt payment alerts by urgency: EMIs due/overdue, cash cover, missed or late EMIs, underpaid card.",
          {"today": {"type": "string", "description": "YYYY-MM-DD; omit for today"},
           "horizon_days": {"type": "integer"}}),
    _tool("get_net_worth", "Assets minus liabilities, debt-to-asset ratio."),
    _tool("detect_anomalies",
          "Outlier transactions, category spikes, bonus income, quarantined bad rows.",
          {"period": _period, "sensitivity": {"type": "string", "enum": ["low", "normal", "high"]},
           "include_data_quality": {"type": "boolean"}}),
    _tool("get_health_score",
          "0-100 health score with 6 weighted components vs benchmarks."),
    _tool("get_recommendations", "Ranked actions with ₹ impact."),
    _tool("simulate_scenario",
          "What-if: ratios and health score before/after changes.",
          {"spending_cut_pct": {"type": "number"},
           "category_cuts": {"type": "object", "additionalProperties": {"type": "number"},
                             "description": "e.g. {\"Food\": 20}"},
           "extra_monthly_investment": {"type": "number"},
           "pay_off_liabilities": {"type": "array", "items": {"type": "string"},
                                   "description": "e.g. ['Credit Card']"},
           "income_change_pct": {"type": "number"}}),
    _tool("forecast", "Net worth projection for N (1-60) months.", {"months": {"type": "integer"}}),
    _tool("search_transactions",
          "Find individual transactions by filters.",
          {"period": _period, "categories": _cats, "description_contains": {"type": "string"},
           "min_amount": {"type": "number"}, "max_amount": {"type": "number"},
           "txn_type": {"type": "string", "enum": ["income", "expense"]},
           "sort_by": {"type": "string", "enum": ["amount_desc", "amount_asc", "date_desc", "date_asc"]},
           "limit": {"type": "integer"}, "include_excluded": {"type": "boolean"}}),
    _tool("get_data_quality_report",
          "Raw-data issues found and how each was handled."),
    _tool("render_chart",
          "Attach a chart to your answer, built from an earlier tool result. Call once per chart you want shown. "
          "Allowed chart types per source tool: " + json.dumps({k: v[1] for k, v in CHART_OPTIONS.items()}),
          {"result_id": {"type": "string", "description": "result_id returned by a data tool in this conversation"},
           "chart_type": {"type": "string", "enum": CHART_TYPES,
                          "description": "Omit to use the source tool's default chart"},
           "title": {"type": "string"},
           "series": {"type": "array", "items": {"type": "string"},
                      "description": "Optional: only keep these named series (e.g. ['Income','Spending'])"}},
          ["result_id"]),
]

FUNCS = {t["name"]: getattr(engine, t["name"]) for t in TOOLS if t["name"] != "render_chart"}


@dataclass
class ToolContext:
    """Per-session cache of tool results so charts can reference them."""
    results: dict[str, tuple[str, dict]] = field(default_factory=dict)
    counter: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)  # specialists run in parallel

    def store(self, tool: str, data: dict) -> str:
        with self.lock:
            self.counter += 1
            rid = f"r{self.counter}"
            self.results[rid] = (tool, data)
        return rid


# Fields the model never needs (charts get the full data from the cache).
_DROP = {"method", "definitions", "notes", "basis", "assumptions", "_scatter"}
_LIST_CAP = 12


def _columnar(v):
    """[{a:1,b:2},...] -> {"cols":[a,b],"rows":[[1,2],...]}: far fewer tokens for tables."""
    if isinstance(v, list) and len(v) >= 3 and all(isinstance(x, dict) for x in v):
        cols = list(dict.fromkeys(k for x in v for k in x))
        out = {"cols": cols, "rows": [[_compact(x.get(c)) for c in cols] for x in v[:_LIST_CAP]]}
        if len(v) > _LIST_CAP:
            out["more_rows"] = len(v) - _LIST_CAP
        return out
    return None


def _compact(v):
    if isinstance(v, float):
        return round(v, 1)
    if isinstance(v, dict):
        return {k: _compact(x) for k, x in v.items() if k not in _DROP}
    if isinstance(v, list):
        return _columnar(v) or [_compact(x) for x in v]
    return v


def _llm_view(tool: str, data: dict) -> dict:
    """Compact, model-facing version of a result (the full result stays cached for charts)."""
    if tool == "get_spending_heatmap":  # send per-category total and peak instead of the matrix
        data = {"period": data["period"], "categories": [
            {"category": c, "total": sum(row), "peak_month": data["months"][row.index(max(row))], "peak": max(row)}
            for c, row in zip(data["categories"], data["values"])]}
    elif tool == "forecast":  # quarterly points are enough for narration
        rows = data["rows"]
        data = {**data, "rows": rows[2::3] if len(rows) > 6 else rows}
    elif tool == "get_category_trend":  # averages + growth; the monthly values go to the chart only
        data = {"period": data["period"],
                "series": [{k: v for k, v in s.items() if k != "values"} for s in data["series"]]}
    return _compact(data)


def run_tool(ctx: ToolContext, name: str, args: dict) -> tuple[str, dict | None, bool]:
    """Execute one tool. Returns (content_for_llm, chart_spec_or_None, is_error)."""
    try:
        if name == "render_chart":
            rid = args.get("result_id")
            if rid not in ctx.results:
                return f"Unknown result_id '{rid}'. Available: {list(ctx.results)}", None, True
            tool, data = ctx.results[rid]
            spec = build_chart(tool, data, args.get("chart_type"), args.get("title"), args.get("series"))
            spec["source"] = {"tool": tool, "result_id": rid, "alternatives": CHART_OPTIONS[tool][1]}
            return json.dumps({"rendered": spec["type"], "title": spec.get("title")}), spec, False
        if name not in FUNCS:
            return f"Unknown tool '{name}'", None, True
        data = FUNCS[name](**args)
        rid = ctx.store(name, data)
        payload = {"result_id": rid, "charts": CHART_OPTIONS.get(name, (None, []))[1], "data": _llm_view(name, data)}
        return json.dumps(payload, default=str, ensure_ascii=False, separators=(",", ":")), None, False
    except (PeriodError, ChartError, TypeError, ValueError, KeyError) as e:
        return f"Error: {e}", None, True
