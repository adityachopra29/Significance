"use client";

import { useCallback, useEffect, useState } from "react";
import {
  CompanyAdmin,
  FeedItem,
  Stats,
  getCompanies,
  getEventTypes,
  getFeed,
  getStats,
} from "@/lib/api";
import { prettyEventType, scoreColor, timeAgo } from "@/lib/format";
import DetailDrawer from "@/components/DetailDrawer";
import ManageStocks from "@/components/ManageStocks";
import CompanySearch from "@/components/CompanySearch";

export default function Dashboard() {
  const [items, setItems] = useState<FeedItem[]>([]);
  const [total, setTotal] = useState(0);
  const [stats, setStats] = useState<Stats | null>(null);
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const [companies, setCompanies] = useState<CompanyAdmin[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [managing, setManaging] = useState(false);

  const [days, setDays] = useState(7);
  const [minScore, setMinScore] = useState(0);
  const [eventType, setEventType] = useState("");
  const [direction, setDirection] = useState("");
  const [companyId, setCompanyId] = useState("");
  const [sortBy, setSortBy] = useState<"score" | "recency">("score");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [feed, s] = await Promise.all([
        getFeed({
          days,
          min_score: minScore,
          event_type: eventType || undefined,
          direction: direction || undefined,
          company_id: companyId ? Number(companyId) : undefined,
          sort_by: sortBy,
        }),
        getStats(),
      ]);
      setItems(feed.items);
      setTotal(feed.total);
      setStats(s);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [days, minScore, eventType, direction, companyId, sortBy]);

  const loadCompanies = useCallback(() => {
    getCompanies().then(setCompanies).catch(() => {});
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    getEventTypes().then(setEventTypes).catch(() => {});
    loadCompanies();
  }, [loadCompanies]);

  // Auto-refresh every 60s.
  useEffect(() => {
    const t = setInterval(load, 60000);
    return () => clearInterval(t);
  }, [load]);

  return (
    <div className="container">
      <div className="header">
        <div>
          <div className="title">Significance</div>
          <div className="subtitle">
            Ranked BSE corporate-announcement signals · material impact for traders
          </div>
        </div>
        <div className="statbar">
          {stats && (
            <>
              <span>
                <b>{stats.analyzed}</b> analyzed
              </span>
              <span>
                <b>{stats.pending}</b> pending
              </span>
              <span>
                <b>{stats.errors}</b> errors
              </span>
              <span>
                <b>{stats.companies}</b> companies
              </span>
              <span>updated {timeAgo(stats.last_announcement_at)}</span>
            </>
          )}
          <button className="btn" onClick={() => setManaging(true)}>
            Manage stocks
          </button>
        </div>
      </div>
      <div className="disclaimer">For research and analysis only — not investment advice.</div>
      {stats && !stats.llm_configured && (
        <div className="error-banner">
          LLM is not configured: {stats.llm_error || "set LLM_PROVIDER and LLM_API_KEY."}
        </div>
      )}

      <div className="filters">
        <label>
          Sort by
          <select
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as "score" | "recency")}
          >
            <option value="score">Relevance score</option>
            <option value="recency">Latest first</option>
          </select>
        </label>
        <label>
          Window
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={1}>Today</option>
            <option value={3}>3 days</option>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
          </select>
        </label>
        <label>
          Min score: {minScore}
          <input
            type="range"
            min={0}
            max={80}
            step={5}
            value={minScore}
            onChange={(e) => setMinScore(Number(e.target.value))}
          />
        </label>
        <label>
          Event type
          <select value={eventType} onChange={(e) => setEventType(e.target.value)}>
            <option value="">All</option>
            {eventTypes.map((t) => (
              <option key={t} value={t}>
                {prettyEventType(t)}
              </option>
            ))}
          </select>
        </label>
        <label>
          Direction
          <select value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="">All</option>
            <option value="bullish">Bullish</option>
            <option value="bearish">Bearish</option>
            <option value="neutral">Neutral</option>
          </select>
        </label>
        <label className="filter-company">
          Company
          <CompanySearch
            companies={companies}
            value={companyId}
            onChange={setCompanyId}
          />
        </label>
      </div>

      {error && <div className="empty">Could not reach the API.<br />{error}</div>}
      {loading && items.length === 0 && <div className="loading">Loading feed…</div>}
      {!loading && !error && items.length === 0 && (
        <div className="empty">
          No analyzed announcements yet.
          <br />
          Make sure the worker is running and the BSE 500 master is loaded.
        </div>
      )}

      <div className="list">
        {items.map((it) => (
          <div className="card" key={it.id} onClick={() => setSelected(it.id)}>
            <div className="card-top">
              <div className="score" style={{ color: scoreColor(it.composite_score) }}>
                {it.composite_score?.toFixed(0) ?? "-"}
              </div>
              <div className="card-main">
                <div className="card-headline">
                  {it.company?.name ?? it.bse_scrip_code ?? "Unknown"}
                </div>
                <div className="card-meta">
                  {it.company?.nse_symbol && <span className="chip">{it.company.nse_symbol}</span>}
                  {it.event_type && <span className="chip">{prettyEventType(it.event_type)}</span>}
                  {it.company?.sector && <span>{it.company.sector}</span>}
                  {it.direction && <span className={`dir ${it.direction}`}>{it.direction}</span>}
                  <span>{timeAgo(it.announced_at)}</span>
                </div>
              </div>
            </div>
            <div className="summary">{it.summary || it.headline}</div>
            {it.company?.chart_url && (
              <a
                className="card-link"
                href={it.company.chart_url}
                target="_blank"
                rel="noreferrer"
                onClick={(e) => e.stopPropagation()}
              >
                View stock chart →
              </a>
            )}
          </div>
        ))}
      </div>

      {total > items.length && (
        <div className="loading">
          Showing {items.length} of {total}. Refine filters to narrow results.
        </div>
      )}

      {selected !== null && (
        <DetailDrawer id={selected} onClose={() => setSelected(null)} />
      )}

      {managing && (
        <ManageStocks
          onClose={() => setManaging(false)}
          onChanged={() => {
            loadCompanies();
            load();
          }}
        />
      )}
    </div>
  );
}
