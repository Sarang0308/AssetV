"""Ingest + clean the three CSVs.

Every fix or quarantine is logged as an issue so the agent can explain exactly
what was done to the data (the dataset contains intentional defects).

Policy:
  * fixable problems (bad date format, missing/wrong category, duplicate IDs) are
    repaired in place and logged;
  * rows we cannot trust (future dates, negative expenses, type/category conflicts,
    extreme data-entry outliers) are kept but marked `excluded=True`, so core
    metrics ignore them while anomaly/quality tools can still show them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[2] / "dataset"

INCOME_CATEGORIES = {"Salary", "Other Income"}
# Outflows that are not consumption: building wealth / servicing debt.
INVESTMENT_CATEGORIES = {"Investments"}
DEBT_CATEGORIES = {"Debt Payment"}
ESSENTIAL_CATEGORIES = {"Housing", "Utilities", "Insurance", "Healthcare", "Food", "Transport", "Education"}

LIQUID_ASSET_TYPES = {"Savings Account", "Current Account", "Fixed Deposit"}
ASSET_CLASS = {
    "Savings Account": "Cash & Deposits",
    "Current Account": "Cash & Deposits",
    "Fixed Deposit": "Cash & Deposits",
    "Mutual Funds": "Market Investments",
    "Equity Portfolio": "Market Investments",
    "Gold": "Gold",
    "Vehicle": "Depreciating Assets",
    "Property": "Real Estate",
}

# A single transaction this many times its description's median is treated as
# a data-entry error rather than real spending.
DATA_ENTRY_OUTLIER_MULTIPLE = 20


@dataclass
class Dataset:
    transactions: pd.DataFrame          # all rows, with `excluded` + `flags`
    assets: pd.DataFrame
    liabilities: pd.DataFrame
    issues: list[dict] = field(default_factory=list)
    as_of: pd.Timestamp = None           # snapshot date of the balance sheet
    window_start: pd.Timestamp = None
    window_end: pd.Timestamp = None

    @property
    def txns(self) -> pd.DataFrame:
        """Clean transactions used for every core metric."""
        return self.transactions[~self.transactions["excluded"]]


def _issue(issues, kind, severity, action, txn_id=None, detail=""):
    issues.append({"txn_id": txn_id, "issue": kind, "severity": severity, "action": action, "detail": detail})


def _parse_dates(df: pd.DataFrame, issues: list) -> None:
    raw = df["date"].astype(str).str.strip()
    parsed = pd.to_datetime(raw, format="%Y-%m-%d", errors="coerce")
    bad = parsed.isna()
    for idx in df.index[bad]:
        alt = pd.to_datetime(raw[idx].replace("/", "-"), errors="coerce")
        if pd.notna(alt):
            parsed[idx] = alt
            _issue(issues, "Non-standard date format", "low", "Normalised to ISO date",
                   df.at[idx, "txn_id"], f"'{raw[idx]}' -> {alt.date()}")
        else:
            _issue(issues, "Unparseable date", "high", "Excluded", df.at[idx, "txn_id"], raw[idx])
    df["date"] = parsed


def _build_description_map(df: pd.DataFrame) -> dict[str, str]:
    """Majority category for each description, learned from the data itself."""
    valid = df[df["category"].notna() & (df["category"] != "")]
    return valid.groupby("description")["category"].agg(lambda s: s.value_counts().index[0]).to_dict()


def load_dataset(data_dir: Path = DATA_DIR) -> Dataset:
    issues: list[dict] = []
    tx = pd.read_csv(data_dir / "transactions.csv", dtype=str, keep_default_na=False)
    assets = pd.read_csv(data_dir / "assets.csv")
    liabilities = pd.read_csv(data_dir / "liabilities.csv")

    tx = tx.apply(lambda c: c.str.strip())
    tx["flags"] = [[] for _ in range(len(tx))]
    tx["excluded"] = False

    # 1. exact duplicate rows
    dup_mask = tx.duplicated(subset=["txn_id", "date", "category", "description", "amount", "type"], keep="first")
    for idx in tx.index[dup_mask]:
        _issue(issues, "Exact duplicate row", "medium", "Removed", tx.at[idx, "txn_id"],
               f"{tx.at[idx, 'description']} {tx.at[idx, 'amount']} on {tx.at[idx, 'date']}")
    tx = tx[~dup_mask].copy()

    # 2. duplicate IDs with different content -> keep both, make IDs unique
    for txn_id, grp in tx.groupby("txn_id"):
        if len(grp) > 1:
            for n, idx in enumerate(grp.index[1:], start=1):
                new_id = f"{txn_id}-{chr(ord('A') + n)}"
                _issue(issues, "Duplicate transaction ID (different transactions)", "medium",
                       f"Re-keyed as {new_id}", txn_id, tx.at[idx, "description"])
                tx.at[idx, "txn_id"] = new_id

    # 3. dates
    _parse_dates(tx, issues)

    # 4. amounts
    tx["amount"] = pd.to_numeric(tx["amount"], errors="coerce")

    # 5. categories / descriptions
    desc_map = _build_description_map(tx)
    known = set(tx["category"].value_counts()[lambda s: s >= 5].index) - {""}
    for idx, row in tx.iterrows():
        cat, desc = row["category"], row["description"]
        if not desc:
            tx.at[idx, "description"] = "Unspecified"
            tx.at[idx, "flags"].append("missing_description")
            _issue(issues, "Missing description", "low", "Kept as 'Unspecified'", row["txn_id"], f"category {cat}")
        if not cat:
            inferred = desc_map.get(desc)
            tx.at[idx, "category"] = inferred or "Other"
            tx.at[idx, "flags"].append("category_inferred")
            _issue(issues, "Missing category", "low", f"Inferred '{inferred or 'Other'}' from description",
                   row["txn_id"], desc)
        elif cat not in known or (desc in desc_map and desc_map[desc] != cat and cat not in INCOME_CATEGORIES):
            fixed = desc_map.get(desc, cat)
            if fixed != cat:
                tx.at[idx, "category"] = fixed
                tx.at[idx, "flags"].append("category_corrected")
                _issue(issues, "Category does not match description", "medium",
                       f"Corrected '{cat}' -> '{fixed}'", row["txn_id"], desc)

    window_end = pd.Timestamp(assets["as_of_date"].max())
    valid_dates = tx["date"].dropna()
    window_start = valid_dates[valid_dates <= window_end].min().to_period("M").to_timestamp()

    def exclude(idx, flag, kind, severity, detail):
        tx.at[idx, "excluded"] = True
        tx.at[idx, "flags"].append(flag)
        _issue(issues, kind, severity, "Excluded from core metrics (shown in anomalies)", tx.at[idx, "txn_id"], detail)

    for idx, row in tx.iterrows():
        if pd.isna(row["date"]):
            tx.at[idx, "excluded"] = True
        elif row["date"] > window_end:
            exclude(idx, "future_date", "Date after balance-sheet snapshot", "high",
                    f"{row['description']} dated {row['date'].date()} (as-of {window_end.date()})")
        if pd.isna(row["amount"]):
            exclude(idx, "invalid_amount", "Non-numeric amount", "high", row["description"])
        elif row["amount"] < 0:
            exclude(idx, "negative_amount", "Negative amount on an expense", "high",
                    f"{row['description']} {row['amount']:,.0f}")
        elif row["amount"] == 0:
            tx.at[idx, "flags"].append("zero_amount")
            _issue(issues, "Zero-value payment", "medium", "Kept; flagged as possible missed payment",
                   row["txn_id"], f"{row['description']} on {row['date'].date() if pd.notna(row['date']) else '?'}")
        if row["category"] in INCOME_CATEGORIES and row["type"] == "expense":
            exclude(idx, "type_conflict", "Income category recorded as expense", "high",
                    f"{row['description']} {row['amount']:,.0f} — likely a reversal entry")
        elif row["type"] not in ("income", "expense"):
            exclude(idx, "invalid_type", "Unknown transaction type", "high", row["type"])

    # 6. data-entry outliers: amount wildly above that description's typical value
    clean = tx[~tx["excluded"]]
    medians = clean.groupby("description")["amount"].median()
    for idx, row in clean.iterrows():
        med = medians.get(row["description"])
        if med and med > 0 and row["amount"] > DATA_ENTRY_OUTLIER_MULTIPLE * med:
            exclude(idx, "data_entry_outlier", "Extreme outlier (likely data-entry error)", "high",
                    f"{row['description']} ₹{row['amount']:,.0f} vs typical ₹{med:,.0f} "
                    f"({row['amount'] / med:.0f}x)")

    tx["month"] = tx["date"].dt.to_period("M")
    tx["kind"] = tx.apply(_kind, axis=1)
    tx = tx.sort_values("date").reset_index(drop=True)

    assets["as_of_date"] = pd.to_datetime(assets["as_of_date"])
    assets["asset_class"] = assets["type"].map(ASSET_CLASS).fillna("Other")
    assets["liquid"] = assets["type"].isin(LIQUID_ASSET_TYPES)
    liabilities["due_date"] = pd.to_datetime(liabilities["due_date"])

    return Dataset(tx, assets, liabilities, issues, window_end, window_start, window_end)


def _kind(row) -> str:
    if row["type"] == "income":
        return "income"
    if row["category"] in INVESTMENT_CATEGORIES:
        return "investment"
    if row["category"] in DEBT_CATEGORIES:
        return "debt"
    return "spending"


@lru_cache(maxsize=1)
def get_dataset() -> Dataset:
    return load_dataset()
