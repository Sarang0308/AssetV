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
from backend.analytics.periods import PERIOD_HELP, PeriodError

CATEGORIES = ["Housing", "Food", "Transport", "Shopping", "Entertainment", "Healthcare", "Personal Care", "Utilities",
              "Insurance", "Education", "Travel", "Other", "Investments", "Debt Payment"]

_period = {"type": "string", "description": PERIOD_HELP}
_cats = {"type": "array", "items": {"type": "string"}, "description": f"Category names, e.g. {CATEGORIES}"}


def _tool(name, description, props=None, required=None):
    return {"name": name, "description": description,
            "input_schema": {"type": "object", "properties": props or {}, "required": required or []}}


TOOLS = [
    _tool("get_financial_summary",
          "Headline KPIs for the trailing 12 months: average monthly income, spending, debt payments, investments, "
          "surplus, savings rate, debt-to-income, emergency-fund months, net worth. Start here for broad questions."),
    _tool("get_cashflow",
          "Income, spending, debt payments, investments and net cash flow per month/quarter/year, with savings rate. "
          "Use for income/expense trends, 'how much did I save', 'income vs expenses'.",
          {"period": _period, "granularity": {"type": "string", "enum": ["month", "quarter", "year"]},
           "exclude_categories": _cats,
           "exclude_anomalies": {"type": "boolean", "description": "Drop statistically unusual one-off transactions"}}),
    _tool("get_spending_breakdown",
          "Where the money goes: spending grouped by category or by description (merchant/sub-category), with share %. "
          "Excludes investments and debt repayments unless include_investments_and_debt=true.",
          {"period": _period, "group_by": {"type": "string", "enum": ["category", "description"]},
           "categories": {**_cats, "description": "Only include these categories (useful with group_by=description)"},
           "exclude_categories": _cats, "include_investments_and_debt": {"type": "boolean"},
           "exclude_anomalies": {"type": "boolean"}, "top_n": {"type": "integer"}}),
    _tool("compare_periods",
          "Compare outflows by category (or description) between two periods, monthly-normalised by default, with "
          "biggest increases/decreases. Use for 'what changed', 'why did expenses go up', '2025 vs 2026'.",
          {"period_a": _period, "period_b": _period, "group_by": {"type": "string", "enum": ["category", "description"]},
           "normalize_monthly": {"type": "boolean"}, "exclude_categories": _cats},
          ["period_a", "period_b"]),
    _tool("get_category_trend",
          "Month-by-month series for chosen categories (or descriptions). Defaults to the top 5 categories.",
          {"categories": _cats, "period": _period, "group_by": {"type": "string", "enum": ["category", "description"]}}),
    _tool("get_spending_heatmap", "Category x month spending matrix — good for spotting seasonality.", {"period": _period}),
    _tool("get_assets",
          "Asset allocation snapshot (as of 01-Oct-2026), optionally filtered, grouped by type or asset class.",
          {"exclude_types": {"type": "array", "items": {"type": "string"},
                             "description": "Asset types or classes to drop, e.g. ['Property'] or ['Real Estate']"},
           "min_value": {"type": "number"}, "group_by": {"type": "string", "enum": ["type", "asset_class"]}}),
    _tool("get_liabilities",
          "All loans with rate, EMI, next due date, annual interest cost, payoff horizon, irregular/missed EMI "
          "payments and upcoming dues."),
    _tool("get_net_worth", "Assets minus liabilities, debt-to-asset ratio and the per-item build-up."),
    _tool("detect_anomalies",
          "Unusual transactions (robust z-score), category-month spikes, bonus-like income, and data-quality rows "
          "that were quarantined.",
          {"period": _period, "sensitivity": {"type": "string", "enum": ["low", "normal", "high"]},
           "include_data_quality": {"type": "boolean"}}),
    _tool("get_health_score",
          "0-100 financial health score with weighted components (savings rate, debt-to-income, emergency fund, "
          "leverage, high-interest debt, spending discipline), each with value and benchmark."),
    _tool("get_recommendations", "Top 3 prioritised actions with quantified ₹ impact, plus additional suggestions."),
    _tool("simulate_scenario",
          "What-if analysis: recompute key ratios and the health score after hypothetical changes.",
          {"spending_cut_pct": {"type": "number", "description": "Cut all spending by this %"},
           "category_cuts": {"type": "object", "additionalProperties": {"type": "number"},
                             "description": "Per-category % cuts, e.g. {\"Food\": 20, \"Shopping\": 30}"},
           "extra_monthly_investment": {"type": "number"},
           "pay_off_liabilities": {"type": "array", "items": {"type": "string"},
                                   "description": "Liability ids or types to pay off from liquid assets, e.g. ['L003'] / ['Credit Card']"},
           "income_change_pct": {"type": "number"}}),
    _tool("forecast", "Project assets, liabilities and net worth forward N months using recent cash flow and stated growth assumptions.",
          {"months": {"type": "integer", "description": "1-60"}}),
    _tool("search_transactions",
          "Look up individual transactions with filters. Use for 'show my biggest X', 'list transactions above ₹10k'.",
          {"period": _period, "categories": _cats, "description_contains": {"type": "string"},
           "min_amount": {"type": "number"}, "max_amount": {"type": "number"},
           "txn_type": {"type": "string", "enum": ["income", "expense"]},
           "sort_by": {"type": "string", "enum": ["amount_desc", "amount_asc", "date_desc", "date_asc"]},
           "limit": {"type": "integer"}, "include_excluded": {"type": "boolean"}}),
    _tool("get_data_quality_report",
          "What was wrong with the raw data (duplicates, missing fields, bad dates, outliers) and how each issue was handled."),
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


def _llm_view(data: dict) -> dict:
    """Strip bulky chart-only payloads before sending to the model."""
    return {k: v for k, v in data.items() if not k.startswith("_")}


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
        default_chart, allowed = CHART_OPTIONS.get(name, (None, []))
        payload = {"result_id": rid, "chartable_as": allowed, "default_chart": default_chart, "data": _llm_view(data)}
        return json.dumps(payload, default=str, ensure_ascii=False), None, False
    except (PeriodError, ChartError, TypeError, ValueError, KeyError) as e:
        return f"Error: {e}", None, True
