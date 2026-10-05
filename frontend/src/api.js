// All calls to the Python backend live in this one file.

// Dashboard numbers for the sidebar (health score, key figures, upcoming dues...).
export async function fetchOverview() {
  const response = await fetch("/api/overview");
  return response.json();
}

// Ask the agents a question.
//
// The backend answers with a *stream*: one JSON object per line ("NDJSON"),
// sent as soon as each step happens — e.g. "Financial Agent started",
// "chart ready", "answer text". We call `onEvent(event)` for every line so the
// UI can update live instead of waiting for the whole answer.
export async function askAgents(message, sessionId, onEvent) {
  const response = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message, session_id: sessionId }),
  });

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Process every complete line; keep any half-received line in the buffer.
    const lines = buffer.split("\n");
    buffer = lines.pop();
    for (const line of lines) {
      if (line.trim()) onEvent(JSON.parse(line));
    }
  }
}

// Re-draw an existing chart as a different chart type (no AI call needed).
export async function changeChartType(sessionId, resultId, chartType) {
  const response = await fetch("/api/rechart", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: sessionId, result_id: resultId, chart_type: chartType }),
  });
  return response.json();
}

// Download a report (PDF / Excel) and save it with the filename the server suggests.
export async function downloadReport(params) {
  const response = await fetch(`/api/report?${new URLSearchParams(params)}`);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `Report download failed (${response.status}).`);
  }
  const blob = await response.blob();
  const disposition = response.headers.get("Content-Disposition") || "";
  const filename = disposition.match(/filename="?([^";]+)"?/i)?.[1] || `FinSight_Report.${params.format}`;
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}
