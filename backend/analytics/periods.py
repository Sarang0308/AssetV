"""Turn natural period strings (as produced by the LLM) into month ranges."""
from __future__ import annotations

import re

import pandas as pd

PERIOD_HELP = (
    "Period string. One of: 'all', 'last_N_months' (e.g. last_6_months), 'latest_month', "
    "'previous_month', 'ytd', 'YYYY' (calendar year), 'FYYYYY-YY' (Indian financial year Apr-Mar, e.g. FY2025-26), "
    "'Qn-YYYY' (calendar quarter), 'YYYY-MM' (single month), or 'YYYY-MM:YYYY-MM' (inclusive range). "
    "Data covers Oct 2024 - Sep 2026; 'latest_month' is Sep 2026."
)


class PeriodError(ValueError):
    pass


def resolve_period(period: str | None, first: pd.Period, last: pd.Period) -> tuple[pd.Period, pd.Period, str]:
    p = (period or "all").strip().lower().replace(" ", "_")

    def clamp(a: pd.Period, b: pd.Period):
        a, b = max(a, first), min(b, last)
        if a > b:
            raise PeriodError(f"Period '{period}' is outside the data range {first} to {last}.")
        return a, b

    if p in ("all", "", "all_time", "overall"):
        a, b = first, last
    elif m := re.fullmatch(r"last_(\d+)_months?", p):
        n = int(m.group(1))
        a, b = last - (n - 1), last
    elif p in ("latest_month", "last_month", "this_month", "current_month"):
        a = b = last
    elif p == "previous_month":
        a = b = last - 1
    elif p in ("ytd", "this_year", "year_to_date"):
        a, b = pd.Period(f"{last.year}-01", "M"), last
    elif p == "last_year":
        a, b = pd.Period(f"{last.year - 1}-01", "M"), pd.Period(f"{last.year - 1}-12", "M")
    elif re.fullmatch(r"\d{4}", p):
        a, b = pd.Period(f"{p}-01", "M"), pd.Period(f"{p}-12", "M")
    elif m := re.fullmatch(r"fy_?(\d{2,4})(?:-(\d{2,4}))?", p):
        start = int(m.group(1))
        start = start + 2000 if start < 100 else start
        if not m.group(2):            # "FY26" conventionally means FY2025-26
            start -= 1
        a, b = pd.Period(f"{start}-04", "M"), pd.Period(f"{start + 1}-03", "M")
    elif m := re.fullmatch(r"q([1-4])[-_]?(\d{4})", p):
        q, y = int(m.group(1)), int(m.group(2))
        a = pd.Period(f"{y}-{3 * q - 2:02d}", "M")
        b = a + 2
    elif re.fullmatch(r"\d{4}-\d{2}", p):
        a = b = pd.Period(p, "M")
    elif m := re.fullmatch(r"(\d{4}-\d{2}):(\d{4}-\d{2})", p):
        a, b = pd.Period(m.group(1), "M"), pd.Period(m.group(2), "M")
    else:
        raise PeriodError(f"Unrecognised period '{period}'. {PERIOD_HELP}")

    a, b = clamp(a, b)
    label = a.strftime("%b %Y") if a == b else f"{a.strftime('%b %Y')} – {b.strftime('%b %Y')}"
    return a, b, label
