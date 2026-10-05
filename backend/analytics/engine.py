"""Deterministic financial analytics — the single source of truth for every number.

Every public function takes plain JSON-able arguments and returns a plain dict,
so it can be exposed directly as an LLM tool.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from backend.analytics.periods import resolve_period
from backend.data.loader import ESSENTIAL_CATEGORIES, Dataset, get_dataset

# Assumed annual returns for the net-worth projection (stated to the user).
GROWTH_ASSUMPTIONS = {
    "Savings Account": 0.03, "Current Account": 0.0, "Fixed Deposit": 0.07,
    "Mutual Funds": 0.11, "Equity Portfolio": 0.12, "Gold": 0.08,
    "Vehicle": -0.12, "Property": 0.05,
}
FD_RATE = GROWTH_ASSUMPTIONS["Fixed Deposit"]


# ---------------------------------------------------------------- helpers
def _ds() -> Dataset:
    return get_dataset()


def _r(x, nd=0):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return None
    return round(float(x), nd) if nd else int(round(float(x)))


def _bounds(ds: Dataset):
    return ds.window_start.to_period("M"), (ds.window_end - pd.Timedelta(days=1)).to_period("M")


def _slice(period: str | None, ds: Dataset | None = None, include_excluded=False):
    ds = ds or _ds()
    first, last = _bounds(ds)
    a, b, label = resolve_period(period, first, last)
    df = ds.transactions if include_excluded else ds.txns
    df = df[(df["month"] >= a) & (df["month"] <= b)]
    return df, a, b, label


def _apply_filters(df, categories=None, exclude_categories=None, exclude_anomalies=False, min_amount=None,
                   max_amount=None):
    if categories:
        df = df[df["category"].str.lower().isin([c.lower() for c in categories])]
    if exclude_categories:
        df = df[~df["category"].str.lower().isin([c.lower() for c in exclude_categories])]
    if exclude_anomalies:
        # only one-off *outflows*; bonus income is not an "expense anomaly"
        flagged = {a["txn_id"] for a in _transaction_anomalies(_ds()) if a["category"] != "Salary"}
        df = df[~df["txn_id"].isin(flagged)]
    if min_amount is not None:
        df = df[df["amount"] >= min_amount]
    if max_amount is not None:
        df = df[df["amount"] <= max_amount]
    return df


def _months(a: pd.Period, b: pd.Period):
    return pd.period_range(a, b, freq="M")


def _monthly(df, a, b):
    """Monthly totals by kind, zero-filled."""
    piv = df.pivot_table(index="month", columns="kind", values="amount", aggfunc="sum", fill_value=0)
    piv = piv.reindex(_months(a, b), fill_value=0)
    for k in ("income", "spending", "debt", "investment"):
        if k not in piv:
            piv[k] = 0.0
    return piv


# ---------------------------------------------------------------- core metrics
def compute_metrics(months: int = 12) -> dict:
    """Core ratios over the trailing window; reused by health score + scenarios."""
    ds = _ds()
    df, a, b, _ = _slice(f"last_{months}_months", ds)
    m = _monthly(df, a, b)
    n = len(m)
    income = m["income"].sum() / n
    spending = m["spending"].sum() / n
    debt_paid = m["debt"].sum() / n
    invest = m["investment"].sum() / n
    essential = df[(df["kind"] == "spending") & df["category"].isin(ESSENTIAL_CATEGORIES)]["amount"].sum() / n

    liab = ds.liabilities
    assets = ds.assets
    total_assets = assets["value"].sum()
    total_liab = liab["outstanding"].sum()
    liquid = assets.loc[assets["liquid"], "value"].sum()
    emi = liab["emi"].sum()
    hi = liab[liab["interest_rate"] >= 15]

    # Spending trend: latest 6 months vs the 6 before.
    df12, a12, b12, _ = _slice("last_12_months", ds)
    s12 = _monthly(df12, a12, b12)["spending"]
    prev6, last6 = s12.iloc[:6].mean(), s12.iloc[6:].mean()

    return {
        "window_months": n,
        "avg_monthly_income": income,
        "avg_monthly_spending": spending,
        "avg_monthly_essential_spending": essential,
        "avg_monthly_debt_payments": debt_paid,
        "avg_monthly_investments": invest,
        "monthly_surplus": income - spending - debt_paid - invest,
        "savings_rate": (income - spending - debt_paid) / income if income else 0,
        "investment_rate": invest / income if income else 0,
        "debt_to_income": emi / income if income else 0,
        "total_emi": emi,
        "liquid_assets": liquid,
        "emergency_fund_months": liquid / (spending + emi) if spending + emi else 0,
        "total_assets": total_assets,
        "total_liabilities": total_liab,
        "net_worth": total_assets - total_liab,
        "debt_to_asset": total_liab / total_assets if total_assets else 0,
        "high_interest_debt": hi["outstanding"].sum(),
        "high_interest_rate": float(hi["interest_rate"].max()) if len(hi) else 0.0,
        "spending_growth_6m": (last6 - prev6) / prev6 if prev6 else 0,
    }


# ---------------------------------------------------------------- tools
def get_financial_summary() -> dict:
    m = compute_metrics(12)
    ds = _ds()
    return {
        "basis": "Trailing 12 months (Oct 2025 – Sep 2026); balance sheet as of " + str(ds.as_of.date()),
        "avg_monthly_income": _r(m["avg_monthly_income"]),
        "avg_monthly_spending": _r(m["avg_monthly_spending"]),
        "avg_monthly_debt_payments": _r(m["avg_monthly_debt_payments"]),
        "avg_monthly_investments": _r(m["avg_monthly_investments"]),
        "avg_monthly_surplus_after_investing": _r(m["monthly_surplus"]),
        "savings_rate_pct": _r(m["savings_rate"] * 100, 1),
        "debt_to_income_pct": _r(m["debt_to_income"] * 100, 1),
        "emergency_fund_months": _r(m["emergency_fund_months"], 1),
        "net_worth": _r(m["net_worth"]),
        "total_assets": _r(m["total_assets"]),
        "total_liabilities": _r(m["total_liabilities"]),
        "spending_growth_last6_vs_prev6_pct": _r(m["spending_growth_6m"] * 100, 1),
        "definitions": {
            "spending": "consumption outflows (excludes investments and debt repayments)",
            "savings_rate": "(income - spending - debt payments) / income; investments count as savings",
        },
    }


def get_cashflow(period: str = "last_12_months", granularity: str = "month", exclude_categories=None,
                 exclude_anomalies: bool = False) -> dict:
    df, a, b, label = _slice(period)
    df = _apply_filters(df, exclude_categories=exclude_categories, exclude_anomalies=exclude_anomalies)
    m = _monthly(df, a, b)
    if granularity in ("quarter", "year"):
        freq = "Q" if granularity == "quarter" else "Y"
        m = m.groupby(m.index.asfreq(freq)).sum()
    rows = []
    for idx, r in m.iterrows():
        net = r["income"] - r["spending"] - r["debt"] - r["investment"]
        rows.append({
            "period": idx.strftime("%b %Y") if granularity == "month" else str(idx),
            "income": _r(r["income"]), "spending": _r(r["spending"]), "debt_payments": _r(r["debt"]),
            "investments": _r(r["investment"]), "net_cash_flow": _r(net),
            "savings_rate_pct": _r((r["income"] - r["spending"] - r["debt"]) / r["income"] * 100, 1) if r["income"] else None,
        })
    tot = m.sum()
    return {
        "period": label, "granularity": granularity, "rows": rows,
        "totals": {"income": _r(tot["income"]), "spending": _r(tot["spending"]), "debt_payments": _r(tot["debt"]),
                   "investments": _r(tot["investment"]),
                   "net_cash_flow": _r(tot["income"] - tot["spending"] - tot["debt"] - tot["investment"]),
                   "savings_rate_pct": _r((tot["income"] - tot["spending"] - tot["debt"]) / tot["income"] * 100, 1)
                   if tot["income"] else None},
    }


def get_spending_breakdown(period: str = "last_12_months", group_by: str = "category", categories=None,
                           exclude_categories=None, include_investments_and_debt: bool = False,
                           exclude_anomalies: bool = False, top_n: int = 12) -> dict:
    df, a, b, label = _slice(period)
    kinds = ["spending", "debt", "investment"] if include_investments_and_debt else ["spending"]
    df = df[df["kind"].isin(kinds)]
    df = _apply_filters(df, categories, exclude_categories, exclude_anomalies)
    key = "description" if group_by in ("description", "merchant", "subcategory") else "category"
    g = df.groupby(key)["amount"].agg(["sum", "count"]).sort_values("sum", ascending=False)
    total = g["sum"].sum()
    items = [{"name": k, "amount": _r(v["sum"]), "share_pct": _r(v["sum"] / total * 100, 1) if total else 0,
              "transactions": int(v["count"])} for k, v in g.iterrows()]
    if len(items) > top_n:
        rest = items[top_n:]
        items = items[:top_n] + [{"name": f"Other ({len(rest)})", "amount": sum(i["amount"] for i in rest),
                                  "share_pct": _r(sum(i["share_pct"] for i in rest), 1),
                                  "transactions": sum(i["transactions"] for i in rest)}]
    n_months = len(_months(a, b))
    return {"period": label, "group_by": key, "total": _r(total), "monthly_average": _r(total / n_months),
            "items": items}


def compare_periods(period_a: str, period_b: str, group_by: str = "category", normalize_monthly: bool = True,
                    exclude_categories=None) -> dict:
    out = {}
    for tag, p in (("a", period_a), ("b", period_b)):
        df, a, b, label = _slice(p)
        df = _apply_filters(df[df["kind"] != "income"], exclude_categories=exclude_categories)
        key = "description" if group_by == "description" else "category"
        n = len(_months(a, b)) if normalize_monthly else 1
        inc = _slice(p)[0]
        out[tag] = {"label": label, "months": len(_months(a, b)),
                    "by_group": (df.groupby(key)["amount"].sum() / n).to_dict(),
                    "income": inc[inc["kind"] == "income"]["amount"].sum() / n,
                    "outflow": df["amount"].sum() / n}
    groups = sorted(set(out["a"]["by_group"]) | set(out["b"]["by_group"]),
                    key=lambda g: -(out["b"]["by_group"].get(g, 0)))
    rows = []
    for g in groups:
        va, vb = out["a"]["by_group"].get(g, 0), out["b"]["by_group"].get(g, 0)
        rows.append({"name": g, "a": _r(va), "b": _r(vb), "change": _r(vb - va),
                     "change_pct": _r((vb - va) / va * 100, 1) if va else None})
    return {
        "basis": "average per month" if normalize_monthly else "period totals",
        "period_a": out["a"]["label"], "period_b": out["b"]["label"],
        "income": {"a": _r(out["a"]["income"]), "b": _r(out["b"]["income"])},
        "total_outflow": {"a": _r(out["a"]["outflow"]), "b": _r(out["b"]["outflow"])},
        "rows": rows,
        "biggest_increases": sorted([r for r in rows if r["change"] > 0], key=lambda r: -r["change"])[:3],
        "biggest_decreases": sorted([r for r in rows if r["change"] < 0], key=lambda r: r["change"])[:3],
    }


def get_category_trend(categories: list[str] | None = None, period: str = "last_12_months",
                       group_by: str = "category") -> dict:
    df, a, b, label = _slice(period)
    df = df[df["kind"] != "income"]
    key = "description" if group_by == "description" else "category"
    if categories:
        df = df[df[key].str.lower().isin([c.lower() for c in categories])]
    else:
        top = df.groupby(key)["amount"].sum().nlargest(5).index
        df = df[df[key].isin(top)]
    piv = df.pivot_table(index="month", columns=key, values="amount", aggfunc="sum", fill_value=0)
    piv = piv.reindex(_months(a, b), fill_value=0)
    series = []
    for col in piv.columns:
        vals = piv[col].values
        half = len(vals) // 2
        growth = (vals[half:].mean() - vals[:half].mean()) / vals[:half].mean() * 100 if half and vals[:half].mean() else None
        series.append({"name": col, "values": [_r(v) for v in vals], "average": _r(vals.mean()),
                       "second_half_vs_first_half_pct": _r(growth, 1)})
    return {"period": label, "months": [p.strftime("%b %Y") for p in piv.index], "series": series}


def get_spending_heatmap(period: str = "last_12_months") -> dict:
    df, a, b, label = _slice(period)
    df = df[df["kind"] == "spending"]
    piv = df.pivot_table(index="category", columns="month", values="amount", aggfunc="sum", fill_value=0)
    piv = piv.reindex(columns=_months(a, b), fill_value=0)
    piv = piv.loc[piv.sum(axis=1).sort_values(ascending=False).index]
    return {"period": label, "months": [p.strftime("%b %y") for p in piv.columns],
            "categories": list(piv.index), "values": [[_r(v) for v in row] for row in piv.values]}


def get_assets(exclude_types=None, min_value: float | None = None, group_by: str = "type") -> dict:
    ds = _ds()
    a = ds.assets.copy()
    if exclude_types:
        low = [t.lower() for t in exclude_types]
        a = a[~a["type"].str.lower().isin(low) & ~a["asset_class"].str.lower().isin(low)]
    if min_value is not None:
        a = a[a["value"] >= min_value]
    key = "asset_class" if group_by in ("class", "asset_class") else "type"
    g = a.groupby(key)["value"].sum().sort_values(ascending=False)
    total = g.sum()
    items = [{"name": k, "value": _r(v), "share_pct": _r(v / total * 100, 1)} for k, v in g.items()]
    liquid = a.loc[a["liquid"], "value"].sum()
    return {
        "as_of": str(ds.as_of.date()), "group_by": key, "total": _r(total), "items": items,
        "largest": items[0] if items else None,
        "liquid_assets": _r(liquid), "liquid_share_pct": _r(liquid / total * 100, 1) if total else 0,
        "notes": ["Vehicle is a depreciating asset", "Property is illiquid"],
    }


def get_liabilities() -> dict:
    ds = _ds()
    m = compute_metrics(12)
    items = []
    for _, l in ds.liabilities.sort_values("interest_rate", ascending=False).iterrows():
        monthly_rate = l["interest_rate"] / 100 / 12
        monthly_interest = l["outstanding"] * monthly_rate
        principal = l["emi"] - monthly_interest
        if principal > 0:
            n = -np.log(1 - monthly_rate * l["outstanding"] / l["emi"]) / np.log(1 + monthly_rate)
        else:
            n = None
        items.append({
            "id": l["liability_id"], "type": l["type"], "outstanding": _r(l["outstanding"]),
            "interest_rate_pct": float(l["interest_rate"]), "emi": _r(l["emi"]),
            "next_due_date": str(l["due_date"].date()),
            "annual_interest_cost_now": _r(monthly_interest * 12),
            "months_to_payoff_at_current_emi": _r(n) if n else None,
        })
    # payment history check: months where a scheduled EMI was missing or zero
    tx = ds.transactions
    missed = []
    for desc in ("Home loan EMI", "Car loan EMI"):
        rows = tx[tx["description"] == desc]
        for idx, r in rows.iterrows():
            if r["amount"] <= 0:
                missed.append({"txn_id": r["txn_id"], "loan": desc, "month": r["month"].strftime("%b %Y"),
                               "recorded_amount": _r(r["amount"])})
    return {
        "total_outstanding": _r(ds.liabilities["outstanding"].sum()),
        "total_emi": _r(m["total_emi"]),
        "debt_to_income_pct": _r(m["debt_to_income"] * 100, 1),
        "weighted_avg_rate_pct": _r((ds.liabilities["outstanding"] * ds.liabilities["interest_rate"]).sum()
                                    / ds.liabilities["outstanding"].sum(), 2),
        "items": items,
        "irregular_payments": missed,
        "upcoming_dues": sorted([{"type": i["type"], "emi": i["emi"], "due": i["next_due_date"]} for i in items],
                                key=lambda d: d["due"]),
    }


# ---------------------------------------------------------------- debt alerts
LOAN_PAYMENT_DESCRIPTION = {"Home Loan": "Home loan EMI", "Car Loan": "Car loan EMI",
                            "Credit Card": "Credit card payment"}
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 11 <= n % 100 <= 13 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


def get_debt_alerts(today: str | None = None, horizon_days: int = 10) -> dict:
    """Debt payment alerts: upcoming/overdue EMIs, cash to cover them, late or missed payments in the
    history, underpaid credit card bills and expensive debt. `today` defaults to the real date (never earlier
    than the data snapshot), so the demo shows what is due right now."""
    ds = _ds()
    as_of = ds.as_of.normalize()
    ref = pd.Timestamp(today).normalize() if today else max(pd.Timestamp.today().normalize(), as_of)
    liab = ds.liabilities
    tx = ds.transactions                      # all rows, including quarantined ones (a reversal is still a missed EMI)
    alerts = []

    def add(severity, kind, loan, title, detail, action, amount=None, due=None, days=None):
        alerts.append({"severity": severity, "type": kind, "loan": loan, "title": title, "detail": detail,
                       "action": action, "amount": _r(amount) if amount is not None else None,
                       "due_date": str(due.date()) if due is not None else None, "days_left": days})

    # 1. Upcoming and overdue EMIs (due dates roll forward monthly)
    upcoming_total = 0
    schedule = []
    for _, l in liab.iterrows():
        due = l["due_date"]
        while due < ref - pd.Timedelta(days=31):            # stale due date -> move to the current cycle
            due = due + pd.DateOffset(months=1)
        days = int((due - ref).days)
        schedule.append({"loan": l["type"], "emi": _r(l["emi"]), "due_date": str(due.date()), "days_left": days})
        if days < 0:
            add("critical", "overdue", l["type"], f"{l['type']} EMI overdue by {-days} day{'s' * (days != -1)}",
                f"₹{l['emi']:,.0f} was due on {due:%d %b}.", "Pay today to avoid late fees and a credit-score hit.",
                l["emi"], due, days)
        elif days <= horizon_days:
            when = "today" if days == 0 else "tomorrow" if days == 1 else f"in {days} days"
            sev = "critical" if days == 0 else "high" if days <= 3 else "medium"
            add(sev, "due_soon", l["type"], f"{l['type']} EMI of ₹{l['emi']:,.0f} due {when}",
                f"Due on {due:%d %b %Y}.", "Keep the amount in the paying account; set up auto-debit.",
                l["emi"], due, days)
            upcoming_total += l["emi"]

    # 2. Can the bank balance cover what is due soon?
    cash = ds.assets.loc[ds.assets["type"].isin(["Savings Account", "Current Account"]), "value"].sum()
    if upcoming_total:
        if cash < upcoming_total:
            add("critical", "cash_shortfall", None, "Not enough cash for upcoming EMIs",
                f"₹{upcoming_total:,.0f} due in the next {horizon_days} days vs ₹{cash:,.0f} in bank accounts.",
                "Move money from the FD or delay discretionary spending.", upcoming_total - cash)
        else:
            add("info", "cash_ok", None, f"Bank balance covers the next {horizon_days} days of EMIs",
                f"₹{upcoming_total:,.0f} due vs ₹{cash:,.0f} in savings and current accounts.",
                "No action needed.", upcoming_total)

    # 3. Payment history: missed, reversed or late payments
    for _, l in liab.iterrows():
        desc = LOAN_PAYMENT_DESCRIPTION.get(l["type"])
        rows = tx[tx["description"] == desc]
        if desc is None or rows.empty:
            continue
        bad = rows[rows["amount"] <= 0]
        for _, r in bad.iterrows():
            what = "zero payment" if r["amount"] == 0 else f"reversed payment (₹{r['amount']:,.0f})"
            add("high", "missed_payment", l["type"], f"Missed {l['type']} EMI in {r['date']:%b %Y}",
                f"{r['txn_id']} recorded a {what} instead of ₹{l['emi']:,.0f}.",
                "Check with the lender for penalties; confirm the EMI is now current.", l["emi"], r["date"])
        paid = rows[rows["amount"] > 0]
        due_day = int(l["due_date"].day)
        late = paid[paid["date"].dt.day > due_day]
        if len(late) >= 3:
            days_late = int((late["date"].dt.day - due_day).median())
            add("high" if days_late > 7 else "medium", "habitually_late", l["type"],
                f"{l['type']} paid late {len(late)} of {len(paid)} times",
                f"Due on the {_ordinal(due_day)}, usually paid on the {_ordinal(int(late['date'].dt.day.median()))} "
                f"(~{days_late} day{'s' * (days_late != 1)} late).",
                f"Schedule auto-debit before the {_ordinal(due_day)} to avoid late fees.")
        if l["type"] == "Credit Card":
            short = paid[paid["amount"] < l["emi"]]
            if len(short):
                add("high", "underpaid", l["type"],
                    f"Credit card underpaid in {len(short)} of {len(paid)} months",
                    f"Payments below the ₹{l['emi']:,.0f} due (lowest ₹{short['amount'].min():,.0f}) leave a "
                    f"balance revolving at {l['interest_rate']:g}%.",
                    "Pay the full due every month, or clear the card from savings.",
                    (l["emi"] - short["amount"]).sum())

    # 4. Expensive debt
    for _, l in liab[liab["interest_rate"] > 15].iterrows():
        yearly = l["outstanding"] * l["interest_rate"] / 100
        add("high", "high_interest", l["type"], f"{l['type']} at {l['interest_rate']:g}% interest",
            f"₹{l['outstanding']:,.0f} outstanding costs about ₹{yearly:,.0f} a year in interest.",
            "Clear it first (avalanche method): it is the most expensive debt.", yearly)

    alerts.sort(key=lambda a: (_SEVERITY_RANK[a["severity"]], a["days_left"] if a["days_left"] is not None else 99))
    counts = {s: sum(a["severity"] == s for a in alerts) for s in _SEVERITY_RANK}
    return {"reference_date": str(ref.date()), "horizon_days": horizon_days,
            "due_in_horizon": _r(upcoming_total), "bank_balance": _r(cash),
            "schedule": sorted(schedule, key=lambda d: d["days_left"]),
            "counts": {k: v for k, v in counts.items() if v}, "alerts": alerts}


def get_net_worth() -> dict:
    ds = _ds()
    a, l = ds.assets, ds.liabilities
    return {
        "as_of": str(ds.as_of.date()),
        "total_assets": _r(a["value"].sum()), "total_liabilities": _r(l["outstanding"].sum()),
        "net_worth": _r(a["value"].sum() - l["outstanding"].sum()),
        "debt_to_asset_pct": _r(l["outstanding"].sum() / a["value"].sum() * 100, 1),
        "net_worth_excl_property_and_vehicle": _r(a.loc[~a["type"].isin(["Property", "Vehicle"]), "value"].sum()
                                                  - l.loc[~l["type"].isin(["Home Loan", "Car Loan"]), "outstanding"].sum()),
        "assets": [{"name": r["type"], "value": _r(r["value"])} for _, r in a.sort_values("value", ascending=False).iterrows()],
        "liabilities": [{"name": r["type"], "value": _r(r["outstanding"])} for _, r in l.iterrows()],
    }


# ---------------------------------------------------------------- anomalies
def _transaction_anomalies(ds: Dataset, z_threshold: float = 3.5) -> list[dict]:
    df = ds.txns
    out = []
    for desc, g in df[df["kind"] != "income"].groupby("description"):
        if len(g) < 6:
            continue
        med = g["amount"].median()
        mad = (g["amount"] - med).abs().median() or (med * 0.1) or 1
        for _, r in g.iterrows():
            z = 0.6745 * (r["amount"] - med) / mad
            if z > z_threshold:
                out.append({"txn_id": r["txn_id"], "date": str(r["date"].date()), "category": r["category"],
                            "description": desc, "amount": _r(r["amount"]), "typical_amount": _r(med),
                            "times_typical": _r(r["amount"] / med, 1), "type": "unusually_high_transaction",
                            "severity": "high" if z > 2 * z_threshold else "medium"})
    sal = df[df["category"] == "Salary"]
    med = sal["amount"].median()
    for _, r in sal[sal["amount"] > 1.25 * med].iterrows():
        out.append({"txn_id": r["txn_id"], "date": str(r["date"].date()), "category": "Salary",
                    "description": r["description"], "amount": _r(r["amount"]), "typical_amount": _r(med),
                    "times_typical": _r(r["amount"] / med, 2), "type": "income_spike_likely_bonus", "severity": "info"})
    return out


def detect_anomalies(period: str = "all", sensitivity: str = "normal", include_data_quality: bool = True) -> dict:
    ds = _ds()
    z = {"low": 5.0, "normal": 3.5, "high": 2.5}.get(sensitivity, 3.5)
    _, a, b, label = _slice(period)
    in_range = lambda d: a <= pd.Period(d, "M") <= b
    tx_anoms = [x for x in _transaction_anomalies(ds, z) if in_range(x["date"])]

    # category-month spikes vs that category's trailing 6-month average
    df = ds.txns[ds.txns["kind"] == "spending"]
    first, last = _bounds(ds)
    piv = df.pivot_table(index="month", columns="category", values="amount", aggfunc="sum", fill_value=0)
    piv = piv.reindex(_months(first, last), fill_value=0)
    spikes = []
    for cat in piv.columns:
        s = piv[cat]
        base = s.shift(1).rolling(6, min_periods=3).mean()
        for month, v in s.items():
            bv = base.get(month)
            if bv and bv > 0 and v > 1.6 * bv and v - bv > 5000 and a <= month <= b:
                spikes.append({"month": month.strftime("%b %Y"), "category": cat, "amount": _r(v),
                               "trailing_6m_avg": _r(bv), "times_avg": _r(v / bv, 2), "type": "category_spike"})

    dq = []
    if include_data_quality:
        ex = ds.transactions[ds.transactions["excluded"]]
        for _, r in ex.iterrows():
            dq.append({"txn_id": r["txn_id"], "date": str(r["date"].date()) if pd.notna(r["date"]) else None,
                       "description": r["description"], "amount": _r(r["amount"]),
                       "flags": r["flags"], "type": "data_quality_excluded"})
        for _, r in ds.transactions[ds.transactions["flags"].apply(lambda f: "zero_amount" in f)].iterrows():
            dq.append({"txn_id": r["txn_id"], "date": str(r["date"].date()), "description": r["description"],
                       "amount": 0, "flags": r["flags"], "type": "possible_missed_payment"})

    clean = _slice(period)[0]
    scatter = clean[clean["kind"] == "spending"][["txn_id", "date", "amount", "description"]]
    return {
        "period": label, "sensitivity": sensitivity,
        "method": "Robust z-score (median/MAD) per description; category-month spikes >1.6x trailing 6-month "
                  "average; salary >1.25x median flagged as bonus; data-quality quarantine.",
        "transaction_anomalies": sorted(tx_anoms, key=lambda x: -x["amount"]),
        "category_spikes": sorted(spikes, key=lambda x: -x["times_avg"])[:10],
        "data_quality_flags": dq,
        "_scatter": [{"x": str(r["date"].date()), "y": _r(r["amount"]), "label": r["description"],
                      "flagged": r["txn_id"] in {t["txn_id"] for t in tx_anoms}} for _, r in scatter.iterrows()],
    }


# ---------------------------------------------------------------- health score
def _lin(x, bad, good):
    """Map x linearly from bad->0 to good->1, clamped."""
    if good == bad:
        return 1.0
    return float(min(1, max(0, (x - bad) / (good - bad))))


def score_from_metrics(m: dict) -> dict:
    components = [
        ("Savings rate", 25, _lin(m["savings_rate"], 0.0, 0.30), f"{m['savings_rate'] * 100:.1f}%", "Target ≥ 30% of income"),
        ("Debt-to-income (EMI)", 20, _lin(m["debt_to_income"], 0.50, 0.20), f"{m['debt_to_income'] * 100:.1f}%", "Target ≤ 20%, risky > 40%"),
        ("Emergency fund", 20, _lin(m["emergency_fund_months"], 0, 6), f"{m['emergency_fund_months']:.1f} months", "Target ≥ 6 months of outflows in liquid assets"),
        ("Leverage (debt-to-asset)", 15, _lin(m["debt_to_asset"], 0.80, 0.30), f"{m['debt_to_asset'] * 100:.1f}%", "Target ≤ 30%"),
        ("High-interest debt", 10, _lin(m["high_interest_debt"] / max(m["avg_monthly_income"], 1), 1.0, 0.0),
         f"₹{m['high_interest_debt']:,.0f}", "Target: no debt above 15% interest"),
        ("Spending discipline", 10, _lin(m["spending_growth_6m"], 0.20, 0.0), f"{m['spending_growth_6m'] * 100:+.1f}% (6m vs prior 6m)",
         "Target: spending flat or falling"),
    ]
    comps = [{"name": n, "weight": w, "points": round(w * s, 1), "score_pct": round(s * 100), "value": v, "benchmark": bm}
             for n, w, s, v, bm in components]
    total = round(sum(c["points"] for c in comps))
    grade = "Excellent" if total >= 85 else "Good" if total >= 70 else "Fair" if total >= 50 else "Needs attention"
    return {"score": total, "grade": grade, "components": comps}


def get_health_score() -> dict:
    m = compute_metrics(12)
    res = score_from_metrics(m)
    comps = sorted(res["components"], key=lambda c: c["score_pct"])
    res["weakest_areas"] = [c["name"] for c in comps[:2] if c["score_pct"] < 100]
    res["strongest_areas"] = [c["name"] for c in comps[::-1][:2]]
    res["basis"] = "Trailing 12 months cash flow + balance sheet as of " + str(_ds().as_of.date())
    return res


# ---------------------------------------------------------------- recommendations
def get_recommendations() -> dict:
    ds = _ds()
    m = compute_metrics(12)
    recs = []
    liab = ds.liabilities
    savings_acct = float(ds.assets.loc[ds.assets["type"] == "Savings Account", "value"].sum())

    for _, l in liab[liab["interest_rate"] >= 15].iterrows():
        saved = l["outstanding"] * (l["interest_rate"] / 100 - GROWTH_ASSUMPTIONS["Savings Account"])
        recs.append({"title": f"Clear the {l['type'].lower()} (₹{l['outstanding']:,.0f} at {l['interest_rate']:.0f}%)",
                     "why": f"It is your most expensive debt; your savings account (₹{savings_acct:,.0f}) can cover it.",
                     "impact": f"≈ ₹{saved:,.0f}/year saved in net interest; frees ₹{l['emi']:,.0f}/month EMI",
                     "impact_value": saved, "category": "debt"})

    rising = [r for r in compare_periods("2025-10:2026-03", "2026-04:2026-09")["rows"]
              if r["change"] > 1000 and r["name"] not in ("Investments", "Debt Payment", "Travel")]  # travel is seasonal
    if rising:
        top = sorted(rising, key=lambda r: -r["change"])[:3]
        monthly = sum(r["change"] for r in top)
        names = ", ".join(f"{r['name']} (+₹{r['change']:,.0f}/mo)" for r in top)
        recs.append({"title": "Cap the fastest-growing discretionary spend",
                     "why": f"Spending rose {m['spending_growth_6m'] * 100:.0f}% in the last 6 months, led by {names}.",
                     "impact": f"Returning these to the prior run-rate saves ≈ ₹{monthly:,.0f}/month (₹{monthly * 12:,.0f}/year)",
                     "impact_value": monthly * 12, "category": "spending"})

    target = 6 * (m["avg_monthly_spending"] + m["total_emi"])
    gap = target - m["liquid_assets"]
    if gap > 0:
        recs.append({"title": "Top up the emergency fund to 6 months",
                     "why": f"Liquid assets cover {m['emergency_fund_months']:.1f} months of outflows.",
                     "impact": f"Add ≈ ₹{gap:,.0f}; e.g. ₹{gap / 6:,.0f}/month for 6 months from surplus",
                     "impact_value": gap * 0.2, "category": "safety"})

    missed = get_liabilities()["irregular_payments"]
    if missed:
        recs.append({"title": "Put loan EMIs on auto-debit",
                     "why": "Irregular EMI entries found: " + ", ".join(f"{x['loan']} {x['month']} (₹{x['recorded_amount']:,})" for x in missed),
                     "impact": "Avoids late fees and credit-score damage", "impact_value": 5000, "category": "debt"})

    prop_share = ds.assets.loc[ds.assets["type"] == "Property", "value"].sum() / ds.assets["value"].sum()
    if prop_share > 0.5:
        recs.append({"title": "Diversify away from property concentration",
                     "why": f"Property is {prop_share * 100:.0f}% of total assets and is illiquid.",
                     "impact": "Direct new surplus into market investments to improve liquidity",
                     "impact_value": 1000, "category": "allocation"})

    car = liab[liab["type"] == "Car Loan"]
    if len(car) and float(car["interest_rate"].iloc[0]) > FD_RATE * 100:
        c = car.iloc[0]
        recs.append({"title": "Consider prepaying the car loan from the FD",
                     "why": f"Car loan costs {c['interest_rate']}% vs ~{FD_RATE * 100:.0f}% FD return on a depreciating asset.",
                     "impact": f"≈ ₹{c['outstanding'] * (c['interest_rate'] / 100 - FD_RATE):,.0f}/year net saving",
                     "impact_value": c["outstanding"] * (c["interest_rate"] / 100 - FD_RATE), "category": "debt"})

    recs.sort(key=lambda r: -r["impact_value"])
    for r in recs:
        r.pop("impact_value")
    return {"top_3": recs[:3], "additional": recs[3:], "health_score": get_health_score()["score"]}


# ---------------------------------------------------------------- what-if
def simulate_scenario(spending_cut_pct: float = 0, category_cuts: dict | None = None,
                      extra_monthly_investment: float = 0, pay_off_liabilities: list[str] | None = None,
                      income_change_pct: float = 0) -> dict:
    ds = _ds()
    base = compute_metrics(12)
    m = dict(base)
    notes = []
    m["avg_monthly_income"] *= 1 + income_change_pct / 100
    spend = m["avg_monthly_spending"] * (1 - spending_cut_pct / 100)
    if category_cuts:
        df, a, b, _ = _slice("last_12_months", ds)
        n = len(_months(a, b))
        for cat, pct in category_cuts.items():
            cat_avg = df[(df["kind"] == "spending") & (df["category"].str.lower() == cat.lower())]["amount"].sum() / n
            spend -= cat_avg * pct / 100 * (1 - spending_cut_pct / 100)
            notes.append(f"{cat}: -{pct}% ≈ ₹{cat_avg * pct / 100:,.0f}/month")
    m["avg_monthly_spending"] = spend

    interest_saved = 0
    if pay_off_liabilities:
        liab = ds.liabilities
        for lid in pay_off_liabilities:
            row = liab[(liab["liability_id"].str.lower() == lid.lower()) | (liab["type"].str.lower() == lid.lower())]
            if row.empty:
                notes.append(f"Unknown liability '{lid}' ignored")
                continue
            r = row.iloc[0]
            m["total_emi"] -= r["emi"]
            m["avg_monthly_debt_payments"] = max(0, m["avg_monthly_debt_payments"] - r["emi"])
            m["liquid_assets"] -= r["outstanding"]
            m["total_liabilities"] -= r["outstanding"]
            m["total_assets"] -= r["outstanding"]
            if r["interest_rate"] >= 15:
                m["high_interest_debt"] -= r["outstanding"]
            interest_saved += r["outstanding"] * r["interest_rate"] / 100
            notes.append(f"Paid off {r['type']} ₹{r['outstanding']:,.0f} from liquid assets")
    inc = m["avg_monthly_income"]
    m["avg_monthly_investments"] += extra_monthly_investment
    m["savings_rate"] = (inc - spend - m["avg_monthly_debt_payments"]) / inc
    m["debt_to_income"] = m["total_emi"] / inc
    m["emergency_fund_months"] = m["liquid_assets"] / (spend + m["total_emi"])
    m["debt_to_asset"] = m["total_liabilities"] / m["total_assets"]
    m["monthly_surplus"] = inc - spend - m["avg_monthly_debt_payments"] - m["avg_monthly_investments"]
    if spending_cut_pct or category_cuts:
        m["spending_growth_6m"] = min(base["spending_growth_6m"], (spend - base["avg_monthly_spending"]) / base["avg_monthly_spending"] + base["spending_growth_6m"])

    def view(x):
        return {"monthly_surplus": _r(x["monthly_surplus"]), "savings_rate_pct": _r(x["savings_rate"] * 100, 1),
                "debt_to_income_pct": _r(x["debt_to_income"] * 100, 1),
                "emergency_fund_months": _r(x["emergency_fund_months"], 1),
                "debt_to_asset_pct": _r(x["debt_to_asset"] * 100, 1),
                "health_score": score_from_metrics(x)["score"]}

    before, after = view(base), view(m)
    return {"before": before, "after": after, "changes": notes,
            "annual_interest_avoided": _r(interest_saved),
            "extra_monthly_investment": _r(extra_monthly_investment),
            "inputs": {"spending_cut_pct": spending_cut_pct, "category_cuts": category_cuts or {},
                       "income_change_pct": income_change_pct, "pay_off_liabilities": pay_off_liabilities or []}}


# ---------------------------------------------------------------- forecast
def forecast(months: int = 12) -> dict:
    ds = _ds()
    months = int(max(1, min(months, 60)))
    m = compute_metrics(6)
    assets = {r["type"]: float(r["value"]) for _, r in ds.assets.iterrows()}
    liabs = [{"type": r["type"], "bal": float(r["outstanding"]), "rate": r["interest_rate"] / 100 / 12, "emi": float(r["emi"])}
             for _, r in ds.liabilities.iterrows()]
    surplus = m["monthly_surplus"]
    invest = m["avg_monthly_investments"]
    start = pd.Period(ds.as_of, "M")
    rows = []
    for i in range(1, months + 1):
        for k in assets:
            assets[k] *= (1 + GROWTH_ASSUMPTIONS.get(k, 0)) ** (1 / 12)
        assets["Mutual Funds"] += invest
        assets["Savings Account"] += surplus
        for l in liabs:
            if l["bal"] > 0:
                l["bal"] = max(0.0, l["bal"] * (1 + l["rate"]) - l["emi"])
        ta, tl = sum(assets.values()), sum(l["bal"] for l in liabs)
        rows.append({"month": (start + i - 1).strftime("%b %Y"), "assets": _r(ta), "liabilities": _r(tl), "net_worth": _r(ta - tl)})
    nw0 = get_net_worth()["net_worth"]
    return {"horizon_months": months, "starting_net_worth": nw0, "projected_net_worth": rows[-1]["net_worth"],
            "change": rows[-1]["net_worth"] - nw0, "rows": rows,
            "assumptions": {"monthly_surplus_added_to_savings": _r(surplus), "monthly_investment_continued": _r(invest),
                            "annual_growth_rates": GROWTH_ASSUMPTIONS,
                            "basis": "Last 6 months average cash flow; EMIs continue as scheduled"}}


# ---------------------------------------------------------------- raw query
def search_transactions(period: str = "all", categories=None, description_contains: str | None = None,
                        min_amount: float | None = None, max_amount: float | None = None, txn_type: str | None = None,
                        sort_by: str = "amount_desc", limit: int = 15, include_excluded: bool = False) -> dict:
    df, a, b, label = _slice(period, include_excluded=include_excluded)
    df = _apply_filters(df, categories, min_amount=min_amount, max_amount=max_amount)
    if description_contains:
        df = df[df["description"].str.contains(description_contains, case=False, na=False)]
    if txn_type in ("income", "expense"):
        df = df[df["type"] == txn_type]
    order = {"amount_desc": ("amount", False), "amount_asc": ("amount", True),
             "date_desc": ("date", False), "date_asc": ("date", True)}.get(sort_by, ("amount", False))
    df = df.sort_values(order[0], ascending=order[1])
    limit = int(max(1, min(limit, 100)))
    rows = [{"txn_id": r["txn_id"], "date": str(r["date"].date()), "category": r["category"],
             "description": r["description"], "amount": _r(r["amount"], 2), "type": r["type"],
             "flags": ", ".join(r["flags"])} for _, r in df.head(limit).iterrows()]
    return {"period": label, "matches": int(len(df)), "total_amount": _r(df["amount"].sum()), "rows": rows}


def get_data_quality_report() -> dict:
    ds = _ds()
    issues = ds.issues
    by_sev = pd.Series([i["severity"] for i in issues]).value_counts().to_dict()
    return {
        "rows_after_dedup": int(len(ds.transactions)),
        "rows_used_in_metrics": int(len(ds.txns)),
        "rows_excluded": int(ds.transactions["excluded"].sum()),
        "issues_found": len(issues), "by_severity": by_sev, "issues": issues,
        "coverage": f"{ds.window_start.strftime('%b %Y')} – {(ds.window_end - pd.Timedelta(days=1)).strftime('%b %Y')}",
    }
