# Asset Vantage — Multi-Agent Financial Analyst

A chatbot that answers *"Are we financially healthy?"* from raw CSVs. It follows the hackathon journey
**Ingest → Calculate → Detect → Score → Recommend**, built as a Gemini function-calling multi-agent system:
the LLM plans and explains, and a deterministic analytics engine computes every number.

```
                         USER  ──►  Chat UI (streams agent steps + charts)
                                        │  POST /api/chat (NDJSON events)
                               ┌────────▼─────────┐
                               │ Supervisor Agent │  conversation memory, planning,
                               │   (Gemini)       │  render_chart, final answer
                               └──┬─────┬──────┬──┘
                delegate (parallel)   │      │
        ┌─────────────────┐  ┌────────▼─────┐  ┌───────────────────────┐
        │ Financial Agent │  │ Anomaly Agent│  │ Recommendation Agent  │
        │ cashflow, spend,│  │ outliers,    │  │ health score, actions,│
        │ assets, debt,   │  │ spikes, dupes│  │ what-if scenarios     │
        │ net worth, fcst │  │ data quality │  │                       │
        └────────┬────────┘  └──────┬───────┘  └──────────┬────────────┘
                 └──────────── tool calls ────────────────┘
                               ┌────────▼─────────┐
                               │ Analytics engine │  pandas — single source of truth
                               └────────┬─────────┘
                               ┌────────▼─────────┐
                               │ Clean dataset    │  dedupe, re-key, fix, quarantine
                               └──────────────────┘
```

**Key design rule:** numbers never pass through an LLM. Data tools return a `result_id`. The supervisor
calls `render_chart(result_id, chart_type)`, and the backend builds the chart spec from the cached result.

## Run

```bash
pip install -r requirements.txt
cp .env.example .env        # add GEMINI_API_KEY
python -m uvicorn backend.main:app --port 8000
```

Open http://localhost:8000. A Gemini API key is required — without one, the dashboard rail still loads but chat
replies with a configuration error. Models are set by `GEMINI_MODEL` (supervisor) and `GEMINI_WORKER_MODEL` (specialists).

## Data cleaning (`backend/data/loader.py`)

All 12 intentional defects are detected and logged (`get_data_quality_report`):

| Issue | Rows | Handling |
|---|---|---|
| Exact duplicate row | T0410 | removed |
| Duplicate ID, different txns | T0031, T0760 | re-keyed `-B` |
| Non-ISO date `2026/06/15` | T0643 | normalised |
| Missing description / category | T0138 / T0702 | kept as "Unspecified" / inferred from description |
| Category ≠ description (`Foods` / Car loan EMI) | T0202 | corrected to Debt Payment |
| Negative EMI (-4,500) | T0089 | quarantined |
| Zero EMI | T0343 | kept, flagged as possible missed payment |
| Date after snapshot (2026-11-15) | T0277 | quarantined |
| Salary "correction" typed as expense (₹2.05 L) | T0556 | quarantined (reversal) |
| ₹1,85,000 "Mobile" bill (206× typical) | T0488 | quarantined as data-entry outlier |

Quarantined rows are excluded from metrics but still surface in anomaly answers.

## Analytics tools (`backend/analytics/engine.py`)

| Tool | Agent | Default chart |
|---|---|---|
| get_financial_summary | Financial / Recommendation | KPI tiles |
| get_cashflow (period, granularity, exclusions) | Financial | combo (stacked outflows + income line) |
| get_spending_breakdown (by category/description, filters) | Financial | doughnut |
| compare_periods (any two periods) | Financial / Anomaly | grouped bar |
| get_category_trend | Financial / Anomaly | line |
| get_spending_heatmap | Financial | heatmap |
| get_assets (exclude types, min value, by class) | Financial | doughnut |
| get_liabilities (rates, payoff, missed EMIs, dues) | Financial / Recommendation | horizontal bar |
| get_net_worth | Financial | waterfall |
| forecast (N months) | Financial | area |
| search_transactions | Financial / Anomaly | table |
| detect_anomalies (robust z-score, spikes, bonus, quality) | Anomaly | scatter |
| get_data_quality_report | Anomaly | table |
| get_health_score (0–100, 6 weighted components) | Recommendation | gauge |
| get_recommendations (₹-quantified, ranked) | Recommendation | table |
| simulate_scenario (what-if: cuts, payoffs, income) | Recommendation | before/after bars |

Periods accept `last_6_months`, `ytd`, `2025`, `FY2025-26` (Indian FY), `Q2-2026`, `2026-03`, and
`2025-10:2026-03`. Users can switch any chart to another valid type from its dropdown (`/api/rechart`),
which needs no LLM call.

### Health score (trailing 12 months)

| Component | Weight | Full marks at |
|---|---|---|
| Savings rate | 25 | ≥ 30% |
| EMI / income | 20 | ≤ 20% |
| Emergency fund | 20 | ≥ 6 months of outflows |
| Debt-to-asset | 15 | ≤ 30% |
| High-interest debt | 10 | none above 15% |
| Spending discipline | 10 | spending not growing |

The current result is **79/100 (Good)**. Savings (35%) and EMI load (17%) are strong. The weak spots are spending,
up 20% over the last 6 months, and leverage at 47.5%.

## Demo script

1. "Am I financially healthy?" → gauge + KPIs
2. "Why?" → what changed + score radar
3. "Show my income and expenses for the last 6 months and where I spend the most" → two charts in parallel
4. "Exclude my one-time expenses" → same analysis re-run with filters
5. "Are there unusual transactions?" → scatter + table, including quarantined rows
6. "What if I pay off my credit card and cut shopping by 30%?" → score 79 → 83
7. "Okay, what should I do next?" → 3 ranked, ₹-quantified actions

## Layout

```
backend/
  main.py            FastAPI: /api/chat (stream), /api/overview, /api/rechart
  data/loader.py     ingest + clean + issue log
  analytics/         periods.py · engine.py · charts.py (chart specs)
  agent/             agent.py (supervisor + specialists) · tools.py (schemas, dispatch)
frontend/            index.html · styles.css · app.js (Chart.js renderer, light/dark)
dataset/             the three CSVs
```
