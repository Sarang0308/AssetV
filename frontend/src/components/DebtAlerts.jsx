// "Debt payment alerts" card for the sidebar.
// Data comes from /api/overview -> debt_alerts (computed in engine.get_debt_alerts, no AI involved).
import { formatMoneyFull, STATUS } from "../format.js";

// One color per severity, most urgent first.
const SEVERITY_COLOR = {
  critical: STATUS.bad,
  high: "#ec835a",
  medium: STATUS.warning,
  low: "#888",
  info: STATUS.good,
};

// "due today", "tomorrow", "in 5 days", "3d overdue"
function whenText(daysLeft) {
  if (daysLeft < 0) return `${-daysLeft}d overdue`;
  if (daysLeft === 0) return "due today";
  if (daysLeft === 1) return "tomorrow";
  return `in ${daysLeft} days`;
}

export default function DebtAlerts({ data }) {
  if (!data) return null;

  const urgent = (data.counts.critical || 0) + (data.counts.high || 0);
  // Upcoming EMIs are already shown in the schedule above, so don't repeat them in the alert list.
  const otherAlerts = data.alerts.filter((a) => a.type !== "due_soon");

  return (
    <section className="card">
      <h3>
        Loan payment alerts{" "}
        <span className="small" style={{ color: urgent ? STATUS.bad : STATUS.good }}>
          {urgent ? `· ${urgent} need attention` : "· all clear"}
        </span>
      </h3>

      {/* Next EMI for each loan, with days left */}
      {data.schedule.map((s) => {
        const color = s.days_left <= 0 ? STATUS.bad : s.days_left <= 3 ? SEVERITY_COLOR.high : undefined;
        return (
          <div className="row" key={s.loan}>
            <span>{s.loan}</span>
            <span>
              {formatMoneyFull(s.emi)} ·{" "}
              <b className="small" style={{ color }}>{whenText(s.days_left)}</b>
            </span>
          </div>
        );
      })}

      {/* Missed / late / underpaid / expensive-debt alerts */}
      <ul className="alerts">
        {otherAlerts.map((a, i) => (
          <li key={i} className="alert" style={{ borderLeftColor: SEVERITY_COLOR[a.severity] }}>
            <div className="alert-title">
              <span className="sev" style={{ background: SEVERITY_COLOR[a.severity] }}>{a.severity}</span>
              {a.title}
            </div>
            <div className="muted small">{a.detail}</div>
            <div className="small">→ {a.action}</div>
          </li>
        ))}
      </ul>
    </section>
  );
}
