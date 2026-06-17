export const API_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface Company {
  id: number;
  name: string;
  bse_scrip_code: string;
  nse_symbol?: string | null;
  sector?: string | null;
  market_cap_cr?: number | null;
  adv_cr?: number | null;
  chart_url?: string | null;
}

export type FeedView = "live" | "ranked";
export type LiveSort = "category" | "recency";
export type RankedSort = "score" | "recency";

export interface FeedItem {
  id: number;
  headline: string;
  company?: Company | null;
  bse_scrip_code?: string | null;
  category?: string | null;
  subcategory?: string | null;
  analysis_status?: string | null;
  triage_event_type?: string | null;
  triage_tier?: string | null;
  category_rank?: number | null;
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
  factors?: Factors | null;
  extracted?: Record<string, unknown> | null;
  event_study?: EventStudy | null;
}

export interface FeedResponse {
  total: number;
  items: FeedItem[];
  limit: number;
  offset: number;
  view: FeedView;
}

export interface Stats {
  universe_companies: number;
  watchlist_companies: number;
  companies: number;
  announcements_total: number;
  triage_passed: number;
  analyzed: number;
  pending: number;
  skipped: number;
  errors: number;
  llm_configured: boolean;
  llm_provider?: string | null;
  llm_error?: string | null;
  last_announcement_at?: string | null;
}

export interface CompanyAdmin {
  id: number;
  name: string;
  bse_scrip_code: string;
  nse_symbol?: string | null;
  sector?: string | null;
  market_cap_cr?: number | null;
  adv_cr?: number | null;
  active: boolean;
  announcement_count: number;
  analyzed_count: number;
  backfill_new?: number | null;
  backfill_cached?: number | null;
}

export async function getFeed(params: {
  view: FeedView;
  days: number;
  sort_by: string;
  limit?: number;
  offset?: number;
  min_score?: number;
  event_type?: string;
  direction?: string;
  company_id?: number;
}): Promise<FeedResponse> {
  const q = new URLSearchParams();
  q.set("view", params.view);
  q.set("days", String(params.days));
  q.set("sort_by", params.sort_by);
  q.set("limit", String(params.limit ?? 50));
  q.set("offset", String(params.offset ?? 0));
  if (params.min_score != null) q.set("min_score", String(params.min_score));
  if (params.event_type) q.set("event_type", params.event_type);
  if (params.direction) q.set("direction", params.direction);
  if (params.company_id) q.set("company_id", String(params.company_id));
  const res = await fetch(`${API_URL}/api/feed?${q.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`Feed request failed: ${res.status}`);
  return res.json();
}

export async function getCompanies(q?: string, watchlistOnly = false): Promise<CompanyAdmin[]> {
  const params = new URLSearchParams();
  if (watchlistOnly) {
    params.set("active_only", "true");
  } else {
    params.set("ingest_only", "true");
  }
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

export async function getEventTypes(view: FeedView): Promise<string[]> {
  const res = await fetch(`${API_URL}/api/event-types?view=${view}`, { cache: "no-store" });
  if (!res.ok) return [];
  return res.json();
}

export function subscribeFeedEvents(
  onEvent: (type: string, data: FeedItem) => void,
  onError?: () => void,
): () => void {
  const es = new EventSource(`${API_URL}/api/feed/events`);
  const handler = (type: string) => (ev: MessageEvent) => {
    try {
      onEvent(type, JSON.parse(ev.data));
    } catch {
      /* ignore parse errors */
    }
  };
  es.addEventListener("announcement_triaged", handler("announcement_triaged"));
  es.addEventListener("analysis_done", handler("analysis_done"));
  es.addEventListener("analysis_started", handler("analysis_started"));
  es.onerror = () => onError?.();
  return () => es.close();
}
