// One chat bubble. User messages are plain text; assistant replies can have
// agent steps, charts, an answer (Markdown) and a token-usage note.
import { useState } from "react";
import Markdown from "react-markdown";
import ChartCard from "./ChartCard.jsx";

export default function Message({ message, sessionId }) {
  if (message.role === "user") {
    return <div className="bubble-user">{message.text}</div>;
  }

  return (
    <div className="reply">
      <AgentSteps steps={message.steps} loading={message.loading} />

      {message.charts.length > 0 && (
        <div className="charts">
          {message.charts.map((spec, i) => (
            <ChartCard key={i} spec={spec} sessionId={sessionId} />
          ))}
        </div>
      )}

      {message.text && (
        <div className="answer">
          <Markdown>{message.text}</Markdown>
        </div>
      )}

      {message.error && <div className="error">⚠ {message.error}</div>}

      {message.usage && message.usage.total_tokens > 0 && (
        <div className="usage">
          Used {message.usage.total_tokens.toLocaleString()} AI tokens in {message.usage.calls} model calls
        </div>
      )}
    </div>
  );
}

// Shows which AI agents worked on the answer, e.g. "Financial Agent → get_cashflow".
// Collapsed by default so beginners see a short summary, with details one click away.
function AgentSteps({ steps, loading }) {
  const [open, setOpen] = useState(false);

  if (loading && steps.length === 0) return <div className="muted small">The supervisor agent is planning…</div>;
  if (steps.length === 0) return null;

  return (
    <div className="steps">
      <button className="link" onClick={() => setOpen(!open)}>
        {loading ? "Working: " : "Answered by: "}
        {steps.map((s) => s.agent).join(", ")} {open ? "▲" : "▼"}
      </button>
      {open && (
        <ul>
          {steps.map((s, i) => (
            <li key={i}>
              <b>{s.agent}</b> was asked: <i>{s.task}</i>
              <br />
              <span className="muted small">Used: {s.tools.join(", ") || "…"}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
