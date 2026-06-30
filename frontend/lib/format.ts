export function scoreColor(score?: number | null): string {
  const s = score ?? 0;
  if (s >= 60) return "#2ec27e";
  if (s >= 40) return "#e6b800";
  if (s >= 20) return "#e08a3c";
  return "#8a98ac";
}

export function fmtPct(v?: number | null): string {
  if (v === null || v === undefined) return "-";
  return `${(v * 100).toFixed(2)}%`;
}

export function fmtNum(v?: number | null, digits = 2): string {
  if (v === null || v === undefined) return "-";
  return v.toFixed(digits);
}

export function timeAgo(iso?: string | null): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const diff = Math.max(0, Date.now() - then);
  const mins = Math.floor(diff / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

export function formatAnnouncedAt(iso?: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleString("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "numeric",
    month: "short",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });
}

export function prettyEventType(t?: string | null): string {
  if (!t) return "";
  return t.replace(/_/g, " ");
}
