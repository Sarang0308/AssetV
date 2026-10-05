// A white card around one chart, with a dropdown to switch the chart type.
import { useState } from "react";
import { changeChartType } from "../api.js";
import ChartView from "./ChartView.jsx";

// Friendlier names for the dropdown.
const TYPE_NAMES = {
  line: "Line", area: "Area", bar: "Bars", hbar: "Horizontal bars", stacked_bar: "Stacked bars",
  grouped_bar: "Side-by-side bars", combo: "Bars + line", pie: "Pie", doughnut: "Donut", gauge: "Gauge",
  waterfall: "Waterfall", scatter: "Dots", radar: "Radar", heatmap: "Heatmap", table: "Table", kpi: "Number tiles",
};

export default function ChartCard({ spec: initialSpec, sessionId }) {
  const [spec, setSpec] = useState(initialSpec);
  const options = spec.source?.alternatives || [];

  async function onTypeChange(event) {
    const result = await changeChartType(sessionId, spec.source.result_id, event.target.value);
    if (result.ok) setSpec(result.spec);
  }

  // Tables, heatmaps and tiles need the full width; other charts sit side by side.
  const wide = ["table", "heatmap", "kpi", "gauge"].includes(spec.type) || (spec.labels?.length || 0) > 14;

  return (
    <div className={`chart-card ${wide ? "wide" : ""}`}>
      <div className="chart-header">
        <div className="chart-title">{spec.title}</div>
        {options.length > 1 && (
          <select value={spec.type} onChange={onTypeChange} title="Show this data as a different chart">
            {options.map((type) => (
              <option key={type} value={type}>{TYPE_NAMES[type] || type}</option>
            ))}
          </select>
        )}
      </div>
      {spec.subtitle && <div className="muted small">{spec.subtitle}</div>}
      <ChartView spec={spec} />
    </div>
  );
}
