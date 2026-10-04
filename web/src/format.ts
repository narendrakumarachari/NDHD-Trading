// web/src/format.ts - small, dependency-free formatting helpers.

type Num = number | null | undefined;

const missing = (value: Num): value is null | undefined => value === null || value === undefined || Number.isNaN(value);

export function money(value: Num, opts: { signed?: boolean } = {}): string {
  if (missing(value)) return "—";
  const formatted = Math.abs(value).toLocaleString("en-US", { style: "currency", currency: "USD" });
  if (!opts.signed) return value < 0 ? `-${formatted}` : formatted;
  return value < 0 ? `-${formatted}` : `+${formatted}`;
}

// Whole dollars, compact ($8K, $1.2M): for congressional ranges, which are estimates anyway.
export function moneyShort(value: Num): string {
  if (missing(value)) return "—";
  if (value >= 1e6) return `$${(value / 1e6).toFixed(value >= 1e7 ? 0 : 1)}M`;
  if (value >= 1e3) return `$${Math.round(value / 1e3)}K`;
  return `$${Math.round(value)}`;
}

export function pct(value: Num, opts: { signed?: boolean; digits?: number } = {}): string {
  if (missing(value)) return "—";
  const { signed = false, digits = 2 } = opts;
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function num(value: Num, digits = 2): string {
  if (missing(value)) return "—";
  return Number(value).toFixed(digits);
}

export function timeAgo(iso: string | null | undefined): string {
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

// "2026-09-28" -> "Sep 28". Date-only strings are read as UTC so they don't shift a day.
export function shortDate(isoDate: string | null | undefined): string {
  if (!isoDate) return "—";
  const d = new Date(`${isoDate}T12:00:00Z`);
  if (Number.isNaN(d.getTime())) return isoDate;
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}
