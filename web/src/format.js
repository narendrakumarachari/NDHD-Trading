// web/src/format.js - small, dependency-free formatting helpers.

export function money(value, opts = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const { signed = false } = opts;
  const abs = Math.abs(value);
  const formatted = abs.toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (!signed) return formatted;
  return value < 0 ? `-${formatted}` : `+${formatted}`;
}

export function pct(value, opts = {}) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const { signed = false, digits = 2 } = opts;
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function num(value, digits = 2) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toFixed(digits);
}

export function timeAgo(iso) {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const seconds = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (seconds < 5) return "just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return new Date(iso).toLocaleDateString();
}

export function clock(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleTimeString();
}
