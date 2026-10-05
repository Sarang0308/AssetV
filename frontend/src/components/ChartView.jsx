// Draws a chart from a "spec" sent by the backend.
//
// A spec is plain data, for example:
//   { type: "bar", title: "...", unit: "INR",
//     labels: ["Jan", "Feb"], series: [{ name: "Spending", data: [100, 120] }] }
//
// The backend decides the numbers; this file only decides how they look.
// Charts use the Recharts library: https://recharts.org
import {
  Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Legend, Line, LineChart, Pie, PieChart,
  PolarAngleAxis, PolarGrid, Radar, RadarChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis,
} from "recharts";
import { COLORS, formatMoney, formatMoneyFull, formatValue, scoreColor, statusColor } from "../format.js";

const HEIGHT = 280;
const GRID = "#ececec";
const AXIS = { fontSize: 11, fill: "#6b6b6b" };
// Shared legend settings: keep the data's order (not alphabetical) and small text.
const LEGEND = { itemSorter: null, wrapperStyle: { fontSize: 12 } };

export default function ChartView({ spec }) {
  switch (spec.type) {
    case "line":
    case "area":
      return <LineOrArea spec={spec} />;
    case "bar":
    case "hbar":
    case "grouped_bar":
    case "stacked_bar":
      return <Bars spec={spec} />;
    case "combo":
      return <Combo spec={spec} />;
    case "pie":
    case "doughnut":
      return <PieOrDonut spec={spec} />;
    case "gauge":
      return <Gauge spec={spec} />;
    case "radar":
      return <RadarView spec={spec} />;
    case "scatter":
      return <Dots spec={spec} />;
    case "waterfall":
      return <Waterfall spec={spec} />;
    case "heatmap":
      return <Heatmap spec={spec} />;
    case "table":
      return <Table spec={spec} />;
    case "kpi":
      return <Tiles items={spec.items} />;
    default:
      return <p className="muted">Unknown chart type: {spec.type}</p>;
  }
}

// ---------- helpers ----------

// Recharts wants one object per x-axis point: [{ label: "Jan", Spending: 100, Income: 200 }, ...]
function toRows(spec) {
  return spec.labels.map((label, i) => {
    const row = { label };
    spec.series.forEach((s) => (row[s.name] = s.data[i]));
    return row;
  });
}

// Axis tick text: money in short form (₹1.2 L), percentages with %.
function tick(unit) {
  return (value) => (unit === "INR" ? formatMoney(value) : unit === "%" ? `${value}%` : value);
}

function tooltipFormatter(unit) {
  return (value) => (unit === "INR" ? formatMoneyFull(value) : formatValue(value, unit));
}

const showLegend = (spec) => spec.series.length > 1;

// ---------- chart types ----------

function LineOrArea({ spec }) {
  const Chart = spec.type === "area" ? AreaChart : LineChart;
  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <Chart data={toRows(spec)}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="label" tick={AXIS} />
        <YAxis tick={AXIS} tickFormatter={tick(spec.unit)} width={70} />
        <Tooltip formatter={tooltipFormatter(spec.unit)} cursor={{ fill: "#f5f5f5" }} />
        {showLegend(spec) && <Legend {...LEGEND} />}
        {spec.series.map((s, i) =>
          spec.type === "area" ? (
            <Area key={s.name} dataKey={s.name} stroke={COLORS[i]} fill={COLORS[i]} fillOpacity={0.15} strokeWidth={2} />
          ) : (
            <Line key={s.name} dataKey={s.name} stroke={COLORS[i]} strokeWidth={2} dot={false} />
          )
        )}
      </Chart>
    </ResponsiveContainer>
  );
}

function Bars({ spec }) {
  const horizontal = spec.type === "hbar";
  const stacked = spec.type === "stacked_bar";

  // Pick the color of each single bar:
  //  - "diverging" charts: red for increases, blue for decreases
  //  - score charts (spec.max): green / amber / red by score
  //  - otherwise one color per series
  function barColor(seriesIndex, value) {
    if (spec.diverging) return value >= 0 ? COLORS[7] : COLORS[0];
    if (spec.max) return statusColor(value / spec.max);
    return COLORS[seriesIndex];
  }

  const height = horizontal ? Math.max(HEIGHT, spec.labels.length * 28) : HEIGHT;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={toRows(spec)} layout={horizontal ? "vertical" : "horizontal"}>
        <CartesianGrid stroke={GRID} vertical={horizontal} horizontal={!horizontal} />
        {horizontal ? (
          <>
            <XAxis type="number" tick={AXIS} tickFormatter={tick(spec.unit)} domain={spec.max ? [0, spec.max] : undefined} />
            <YAxis type="category" dataKey="label" tick={AXIS} width={150} />
          </>
        ) : (
          <>
            <XAxis dataKey="label" tick={AXIS} interval={0} angle={spec.labels.length > 6 ? -30 : 0}
                   textAnchor={spec.labels.length > 6 ? "end" : "middle"} height={spec.labels.length > 6 ? 70 : 30} />
            <YAxis tick={AXIS} tickFormatter={tick(spec.unit)} width={70} />
          </>
        )}
        <Tooltip formatter={tooltipFormatter(spec.unit)} cursor={{ fill: "#f5f5f5" }} />
        {showLegend(spec) && <Legend {...LEGEND} />}
        {spec.series.map((s, i) => (
          <Bar key={s.name} dataKey={s.name} stackId={stacked ? "stack" : undefined} fill={COLORS[i]}
               radius={stacked ? 0 : horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={36}>
            {(spec.diverging || spec.max) && s.data.map((value, j) => <Cell key={j} fill={barColor(i, value)} />)}
          </Bar>
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

// Bars for money going out (stacked) + lines for income and net cash flow, on ONE axis.
function Combo({ spec }) {
  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <ComposedChart data={toRows(spec)}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="label" tick={AXIS} />
        <YAxis tick={AXIS} tickFormatter={tick(spec.unit)} width={70} />
        <Tooltip formatter={tooltipFormatter(spec.unit)} cursor={{ fill: "#f5f5f5" }} />
        <Legend {...LEGEND} />
        {spec.series.map((s, i) =>
          s.kind === "line" ? (
            <Line key={s.name} dataKey={s.name} stroke={i === 0 ? COLORS[0] : COLORS[6]} strokeWidth={2} dot={{ r: 2 }} />
          ) : (
            <Bar key={s.name} dataKey={s.name} stackId="out" fill={COLORS[i]} maxBarSize={28} />
          )
        )}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function PieOrDonut({ spec }) {
  const values = spec.series[0].data;
  const total = values.reduce((a, b) => a + b, 0);
  const rows = spec.labels.map((label, i) => ({ name: label, value: values[i] }));
  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <PieChart>
        <Pie data={rows} dataKey="value" nameKey="name" innerRadius={spec.type === "doughnut" ? "55%" : 0}
             outerRadius="85%" stroke="#fff" strokeWidth={2}>
          {rows.map((_, i) => <Cell key={i} fill={COLORS[i % COLORS.length]} />)}
        </Pie>
        <Tooltip formatter={(v) => `${tooltipFormatter(spec.unit)(v)} (${((v / total) * 100).toFixed(1)}%)`} />
        <Legend {...LEGEND} layout="vertical" align="right" verticalAlign="middle"
                formatter={(name, entry) => `${name} ${((entry.payload.value / total) * 100).toFixed(1)}%`} />
      </PieChart>
    </ResponsiveContainer>
  );
}

// Half-circle score meter + the list of score components.
function Gauge({ spec }) {
  const color = scoreColor(spec.value);
  const rows = [{ value: spec.value }, { value: spec.max - spec.value }];
  return (
    <div className="gauge">
      <div className="gauge-dial">
        <ResponsiveContainer width="100%" height={150}>
          <PieChart>
            <Pie data={rows} dataKey="value" startAngle={180} endAngle={0} cy="90%" innerRadius="70%" outerRadius="100%" stroke="none">
              <Cell fill={color} />
              <Cell fill="#eeeeee" />
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        <div className="gauge-number">
          <b style={{ color }}>{spec.value}</b>
          <span className="muted">/100 · {spec.grade}</span>
        </div>
      </div>
      <div>
        {spec.breakdown.map((item) => (
          <div key={item.label} className="score-bar">
            <div className="row small">
              <span>{item.label} <span className="muted">· {item.value}</span></span>
              <span>{item.points}/{item.weight}</span>
            </div>
            <div className="bar">
              <div style={{ width: `${(item.points / item.weight) * 100}%`, background: statusColor(item.points / item.weight) }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RadarView({ spec }) {
  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <RadarChart data={toRows(spec)} outerRadius="75%">
        <PolarGrid stroke={GRID} />
        <PolarAngleAxis dataKey="label" tick={AXIS} />
        <Radar dataKey={spec.series[0].name} stroke={COLORS[0]} fill={COLORS[0]} fillOpacity={0.2} />
        <Tooltip />
      </RadarChart>
    </ResponsiveContainer>
  );
}

// Every transaction as a dot (date vs amount); flagged ones in red.
function Dots({ spec }) {
  const toPoints = (s) => s.data.map((p) => ({ x: Date.parse(p.x), y: p.y, label: p.label, date: p.x }));
  const dateTick = (t) => new Date(t).toLocaleDateString("en-IN", { month: "short", year: "2-digit" });
  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <ScatterChart>
        <CartesianGrid stroke={GRID} />
        <XAxis dataKey="x" type="number" domain={["auto", "auto"]} tick={AXIS} tickFormatter={dateTick} />
        <YAxis dataKey="y" tick={AXIS} tickFormatter={tick("INR")} width={70} />
        <Tooltip content={({ payload }) => {
          const p = payload?.[0]?.payload;
          return p ? <div className="tooltip">{p.label} · {p.date}<br /><b>{formatMoneyFull(p.y)}</b></div> : null;
        }} />
        <Legend {...LEGEND} />
        <Scatter name={spec.series[0].name} data={toPoints(spec.series[0])} fill={COLORS[0]} fillOpacity={0.4} />
        {spec.series[1] && <Scatter name={spec.series[1].name} data={toPoints(spec.series[1])} fill={COLORS[7]} />}
      </ScatterChart>
    </ResponsiveContainer>
  );
}

// Assets add up, debts subtract, final bar = net worth.
// Trick: each bar sits on an invisible "base" bar so it floats at the right height.
function Waterfall({ spec }) {
  let running = 0;
  const rows = spec.steps.map((step) => {
    const start = running;
    running += step.value;
    return { label: step.label, base: Math.min(start, running), amount: Math.abs(step.value), raw: step.value };
  });
  rows.push({ label: spec.total_label, base: 0, amount: running, raw: running, total: true });

  return (
    <ResponsiveContainer width="100%" height={HEIGHT}>
      <BarChart data={rows}>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis dataKey="label" tick={AXIS} interval={0} angle={-30} textAnchor="end" height={70} />
        <YAxis tick={AXIS} tickFormatter={tick("INR")} width={70} />
        <Tooltip formatter={(_, name, item) => (name === "amount" ? formatMoneyFull(item.payload.raw) : null)} />
        <Bar dataKey="base" stackId="w" fill="transparent" />
        <Bar dataKey="amount" stackId="w" radius={[4, 4, 0, 0]} maxBarSize={40}>
          {rows.map((r, i) => <Cell key={i} fill={r.total ? COLORS[0] : r.raw >= 0 ? COLORS[2] : COLORS[7]} />)}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// Category × month grid; darker blue = more spending. Hover a cell for the amount.
function Heatmap({ spec }) {
  const max = Math.max(...spec.matrix.flat()) || 1;
  return (
    <div className="table-scroll">
      <table className="heatmap">
        <thead>
          <tr>
            <th />
            {spec.labels.map((m) => <th key={m}>{m}</th>)}
          </tr>
        </thead>
        <tbody>
          {spec.rows_labels.map((category, r) => (
            <tr key={category}>
              <th className="row-label">{category}</th>
              {spec.matrix[r].map((value, c) => (
                <td key={c} title={`${category} · ${spec.labels[c]}: ${formatMoneyFull(value)}`}
                    style={{ background: `rgba(42, 120, 214, ${0.06 + (value / max) * 0.94})` }} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="muted small">Lighter = less spending, darker = more. Max cell: {formatMoneyFull(max)}</div>
    </div>
  );
}

function Table({ spec }) {
  // Show money columns as ₹ amounts. We guess from the column name.
  const isMoney = (column) =>
    /amount|income|spending|value|outstanding|emi|cost|net|assets|liabilities|investments|debt|typical|change$|20\d\d|–/i.test(column) &&
    !/pct|%|rate|month|txn|score/i.test(column);

  return (
    <div className="table-scroll">
      <table className="table">
        <thead>
          <tr>{spec.columns.map((c) => <th key={c}>{String(c).replaceAll("_", " ")}</th>)}</tr>
        </thead>
        <tbody>
          {spec.rows.map((row, r) => (
            <tr key={r}>
              {row.map((cell, c) => (
                <td key={c} className={typeof cell === "number" ? "num" : ""}>
                  {cell === null || cell === "" ? "–"
                    : typeof cell === "number" ? (isMoney(spec.columns[c]) ? formatMoneyFull(cell) : cell.toLocaleString("en-IN"))
                    : cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Tiles({ items }) {
  return (
    <div className="tiles">
      {items.map((item) => (
        <div className="tile" key={item.label}>
          <div className="tile-value">
            {formatValue(item.value, item.unit)}
            {item.unit && !["INR", "%"].includes(item.unit) && <span className="muted small"> {item.unit}</span>}
          </div>
          <div className="tile-label">{item.label}</div>
          {item.delta != null && (
            <div className="small" style={{ color: item.delta >= 0 ? "#0ca30c" : "#d03b3b" }}>
              {item.delta >= 0 ? "▲" : "▼"} {Math.abs(item.delta)}
            </div>
          )}
        </div>
      ))}
    </div>
  );
}
