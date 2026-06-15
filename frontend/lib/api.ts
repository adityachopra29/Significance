export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface Company {
  id: number;
  name: string;
  bse_scrip_code: string;
  nse_symbol?: string | null;
  sector?: string | null;
  market_cap_cr?: number | null;
}

export interface FeedItem {
  id: number;
  headline: string;
  company?: Company | null;
  bse_scrip_code?: string | null;
  category?: string | null;
  event_type?: string | null;
  direction?: string | null;
  sentiment?: number | null;
  summary?: string | null;
  composite_score?: number | null;
  announced_at?: string | null;
  attachment_url?: string | null;
  model_provider?: string | null;
}

export interface Factors {
  event_type?: number | null;
  materiality?: number | null;
  surprise?: number | null;
  sentiment?: number | null;
  price_reaction?: number | null;
  liquidity?: number | null;
  confidence?: number | null;
  time_decay?: number | null;
}

export interface EventStudy {
  alpha?: number | null;
  beta?: number | null;
  ar_day0?: number | null;
  car_t1?: number | null;
  car_t5?: number | null;
  car_t20?: number | null;
  abnormal_volume?: number | null;
  t_stat?: number | null;
}

export interface FeedItemDetail extends FeedItem {
  factors: Factors;
  extracted?: Record<string, unknown> | null;
  event_study?: EventStudy | null;
}

export interface FeedResponse {
  total: number;
  items: FeedItem[];
}

export interface Stats {
  companies: number;
  announcements_total: number;
  analyzed: number;
  pending: number;
  last_announcement_at?: string | null;
}

export interface CompanyAdmin {
  id: number;
  name: string;
  bse_scrip_code: string;
  nse_symbol?: string | null;
  sector?: string | null;
  market_cap_cr?: number | null;
  active: boolean;
  announcement_count: number;
  analyzed_count: number;
  backfill_new?: number | null;
  backfill_cached?: number | null;
}

export async function getFeed(params: {
  days: number;
  min_score: number;
  event_type?: string;
  direction?: string;
  company_id?: number;
  limit?: number;
}): Promise<FeedResponse> {
  const q = new URLSearchParams();
  q.set("days", String(params.days));
  q.set("min_score", String(params.min_score));
  q.set("limit", String(params.limit ?? 100));
  if (params.event_type) q.set("event_type", params.event_type);
  if (params.direction) q.set("direction", params.direction);
  if (params.company_id) q.set("company_id", String(params.company_id));
  const res = await fetch(`${API_URL}/api/feed?${q.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Feed request failed: ${res.status}`);
  return res.json();
}

export async function getCompanies(q?: string): Promise<CompanyAdmin[]> {
  const params = new URLSearchParams({ active_only: "true" });
  if (q) params.set("q", q);
  const res = await fetch(`${API_URL}/api/companies?${params.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Companies request failed: ${res.status}`);
  return res.json();
}

export async function addCompany(body: {
  scrip_code?: string;
  nse_symbol?: string;
}): Promise<CompanyAdmin> {
  const res = await fetch(`${API_URL}/api/companies`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Add failed: ${res.status}`);
  }
  return res.json();
}

export async function deleteCompany(id: number, purge = false): Promise<void> {
  const res = await fetch(`${API_URL}/api/companies/${id}?purge=${purge}`, {
    method: "DELETE",
  });
  if (!res.ok) throw new Error(`Delete failed: ${res.status}`);
}

export async function getDetail(id: number): Promise<FeedItemDetail> {
  const res = await fetch(`${API_URL}/api/announcements/${id}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Detail request failed: ${res.status}`);
  return res.json();
}

export async function getStats(): Promise<Stats> {
  const res = await fetch(`${API_URL}/api/stats`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Stats request failed: ${res.status}`);
  return res.json();
}

export async function getEventTypes(): Promise<string[]> {
  const res = await fetch(`${API_URL}/api/event-types`, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
}
