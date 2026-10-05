// Left column: always-visible summary of the household's finances.
// The numbers come from /api/overview and do NOT use the AI (so they cost no tokens).
import { useEffect, useState } from "react";
import { fetchOverview } from "../api.js";
import DebtAlerts from "./DebtAlerts.jsx";
import { formatMoney, formatMoneyFull, scoreColor, statusColor } from "../format.js";

export default function Sidebar() {
  const [overview, setOverview] = useState(null);

  // Load the data once when the page opens.
  useEffect(() => {
    fetchOverview().then(setOverview);
  }, []);

  if (!overview) return <aside className="sidebar">Loading…</aside>;

  const health = overview.health;         // gauge chart spec from the backend
  const summary = overview.summary;       // key numbers
  const quality = overview.quality;       // data-cleaning stats

  return (
    <aside className="sidebar">
      <div className="brand">
        <div className="logo">AV</div>
        <div>
          <div className="brand-name">Asset Vantage</div>
          <div className="muted small">Your AI financial analyst</div>
        </div>
      </div>

      <section className="card">
        <h3>Financial health</h3>
        <div className="score">
          <span className="score-number" style={{ color: scoreColor(health.value) }}>{health.value}</span>
          <span className="muted">/ 100 · {health.grade}</span>
        </div>
        <p className="help">Built from 6 checks. A full bar means that area is healthy.</p>
        {health.breakdown.map((item) => (
          <ScoreBar key={item.label} item={item} />
        ))}
      </section>

      <section className="card">
        <h3>Key numbers <span className="muted small">(monthly average, last 12 months)</span></h3>
        <div className="mini-grid">
          <Mini label="Net worth" value={formatMoney(summary.net_worth)} />
          <Mini label="Income" value={formatMoney(summary.avg_monthly_income)} />
          <Mini label="Spending" value={formatMoney(summary.avg_monthly_spending)} />
          <Mini label="Savings rate" value={`${summary.savings_rate_pct}%`} />
          <Mini label="Loan EMIs / income" value={`${summary.debt_to_income_pct}%`} />
          <Mini label="Emergency fund" value={`${summary.emergency_fund_months} months`} />
        </div>
      </section>

      <DebtAlerts data={overview.debt_alerts} />

      <section className="card">
        <h3>Data quality</h3>
        <p className="small">
          We found and fixed <b>{quality.issues_found}</b> problems in the raw data (duplicates, missing
          fields, a wrong date, extreme values). <b>{quality.rows_excluded}</b> untrustworthy rows are left out of
          the numbers. Ask <i>"What was wrong with the raw data?"</i> for details.
        </p>
      </section>

      <div className={`status ${overview.mode === "llm" ? "ok" : "warn"}`}>
        {overview.mode === "llm"
          ? `AI connected · ${overview.model}`
          : "AI not connected — add GEMINI_API_KEY to the .env file and restart the server"}
      </div>
    </aside>
  );
}

// One line in the health breakdown: name, value, and a colored progress bar.
function ScoreBar({ item }) {
  const ratio = item.points / item.weight;
  return (
    <div className="score-bar">
      <div className="row small">
        <span>{item.label}</span>
        <span className="muted">{item.value}</span>
      </div>
      <div className="bar">
        <div style={{ width: `${ratio * 100}%`, background: statusColor(ratio) }} />
      </div>
    </div>
  );
}

function Mini({ label, value }) {
  return (
    <div className="mini">
      <div className="mini-value">{value}</div>
      <div className="mini-label">{label}</div>
    </div>
  );
}
