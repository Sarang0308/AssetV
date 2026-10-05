// Small helpers for showing numbers the Indian way.

// Short money format: ₹1.27 L (lakh), ₹3.69 Cr (crore), ₹68.0k
export function formatMoney(value) {
  if (value === null || value === undefined || isNaN(value)) return "–";
  const sign = value < 0 ? "-" : "";
  const amount = Math.abs(value);
  if (amount >= 1e7) return `${sign}₹${(amount / 1e7).toFixed(2)} Cr`;
  if (amount >= 1e5) return `${sign}₹${(amount / 1e5).toFixed(2)} L`;
  if (amount >= 1e3) return `${sign}₹${(amount / 1e3).toFixed(1)}k`;
  return `${sign}₹${Math.round(amount)}`;
}

// Full money format with Indian digit grouping: ₹1,27,245
export function formatMoneyFull(value) {
  if (value === null || value === undefined || isNaN(value)) return "–";
  return (value < 0 ? "-" : "") + "₹" + Math.round(Math.abs(value)).toLocaleString("en-IN");
}

// Format a value according to its unit ("INR", "%", or anything else).
export function formatValue(value, unit) {
  if (unit === "INR") return formatMoney(value);
  if (unit === "%") return `${value}%`;
  if (typeof value === "number") return value.toLocaleString("en-IN");
  return value ?? "–";
}

// One fixed color per series, in a colorblind-checked order.
// Series 1 always gets blue, series 2 orange, and so on.
export const COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"];

// Colors with meaning: good / okay / bad.
export const STATUS = { good: "#0ca30c", warning: "#e0a000", bad: "#d03b3b" };

// Pick a status color from a 0..1 score.
export function statusColor(ratio) {
  if (ratio >= 0.8) return STATUS.good;
  if (ratio >= 0.5) return STATUS.warning;
  return STATUS.bad;
}

// Color for the overall 0-100 health score (matches the grade bands: 70+ is "Good").
export function scoreColor(score) {
  if (score >= 70) return STATUS.good;
  if (score >= 50) return STATUS.warning;
  return STATUS.bad;
}
