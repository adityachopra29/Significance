"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  FeedItem,
  FeedView,
  Stats,
  getEventTypes,
  getFeed,
  getStats,
  subscribeFeedEvents,
} from "@/lib/api";
import { prettyEventType, scoreColor, timeAgo } from "@/lib/format";
import DetailDrawer from "@/components/DetailDrawer";
import ManageStocks from "@/components/ManageStocks";
import CompanySearch from "@/components/CompanySearch";

const PAGE_SIZE = 50;

export default function Dashboard() {
  const [view, setView] = useState<FeedView>("live");
  const [items, setItems] = useState<FeedItem[]>([]);
  const [total, setTotal] = useState(0);
  const [offset, setOffset] = useState(0);
  const [stats, setStats] = useState<Stats | null>(null);
  const [eventTypes, setEventTypes] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [managing, setManaging] = useState(false);

  const [days, setDays] = useState(7);
  const [minScore, setMinScore] = useState(0);
  const [eventType, setEventType] = useState("");
  const [direction, setDirection] = useState("");
  const [companyId, setCompanyId] = useState("");
  const [liveSort, setLiveSort] = useState<"category" | "recency">("category");
  const [rankedSort, setRankedSort] = useState<"score" | "recency">("score");

  const sortBy = view === "live" ? liveSort : rankedSort;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [feed, s] = await Promise.all([
        getFeed({
          view,
          days,
          sort_by: sortBy,
          limit: PAGE_SIZE,
          offset,
          min_score: view === "ranked" ? minScore : undefined,
          event_type: eventType || undefined,
          direction: view === "ranked" ? direction || undefined : undefined,
          company_id: companyId ? Number(companyId) : undefined,
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
  }, [view, days, sortBy, offset, minScore, eventType, direction, companyId]);

  useEffect(() => {
    setOffset(0);
  }, [view, days, sortBy, minScore, eventType, direction, companyId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    getEventTypes(view).then(setEventTypes).catch(() => {});
  }, [view]);

  const upsertItem = useCallback(
    (eventType: string, incoming: FeedItem) => {
      if (eventType === "analysis_started") {
        const aid = incoming.id;
        setItems((prev) =>
          prev.map((x) =>
            x.id === aid ? { ...x, analysis_status: "processing" } : x,
          ),
        );
        return;
      }

      if (view === "live") {
        setItems((prev) => {
          const idx = prev.findIndex((x) => x.id === incoming.id);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = { ...next[idx], ...incoming };
            return next;
          }
          if (eventType === "announcement_triaged") {
            return [incoming, ...prev].slice(0, PAGE_SIZE);
          }
          return prev;
        });
        if (eventType === "announcement_triaged") {
          setTotal((t) => t + 1);
        }
        return;
      }

      if (
        view === "ranked" &&
        eventType === "analysis_done" &&
        incoming.composite_score != null
      ) {
        setItems((prev) => {
          const rest = prev.filter((x) => x.id !== incoming.id);
          const next = [...rest, incoming];
          if (rankedSort === "score") {
            next.sort(
              (a, b) => (b.composite_score ?? 0) - (a.composite_score ?? 0),
            );
          }
          return next.slice(0, PAGE_SIZE);
        });
        setTotal((t) => t + 1);
      }
    },
    [view, rankedSort],
  );

  useEffect(() => {
    const unsub = subscribeFeedEvents((type, data) => {
      if (
        type === "announcement_triaged" ||
        type === "analysis_done" ||
        type === "analysis_started"
      ) {
        upsertItem(type, data);
      }
      getStats().then(setStats).catch(() => {});
    });
    return unsub;
  }, [upsertItem]);

  const canLoadMore = offset + items.length < total;

  const subtitle = useMemo(
    () =>
      view === "live"
        ? "All triage-passed filings · sorted by event category or recency"
        : "LLM-analyzed filings · sorted by relevance score or recency",
    [view],
  );

  return (
    <div className="container">
      <div className="header">
        <div>
          <div className="title">Significance</div>
          <div className="subtitle">{subtitle}</div>
        </div>
        <div className="statbar">
          {stats && (
            <>
              <span>
                <b>{stats.triage_passed}</b> live
              </span>
              <span>
                <b>{stats.analyzed}</b> ranked
              </span>
              <span>
                <b>{stats.pending}</b> queued
              </span>
              <span>
                <b>{stats.universe_companies}</b> universe
              </span>
              <span>updated {timeAgo(stats.last_announcement_at)}</span>
            </>
          )}
          <button className="btn" onClick={() => setManaging(true)}>
            Watchlist
          </button>
        </div>
      </div>
      <div className="disclaimer">For research and analysis only — not investment advice.</div>
      {stats && !stats.llm_configured && (
        <div className="error-banner">
          LLM is not configured: {stats.llm_error || "set LLM_PROVIDER and LLM_API_KEY."}
        </div>
      )}

      <div className="view-toggle">
        <button
          className={view === "live" ? "view-btn active" : "view-btn"}
          onClick={() => setView("live")}
        >
          Live
        </button>
        <button
          className={view === "ranked" ? "view-btn active" : "view-btn"}
          onClick={() => setView("ranked")}
        >
          Ranked
        </button>
      </div>

      <div className="filters">
        <label>
          Sort by
          <select
            value={sortBy}
            onChange={(e) => {
              const v = e.target.value;
              if (view === "live") setLiveSort(v as "category" | "recency");
              else setRankedSort(v as "score" | "recency");
            }}
          >
            {view === "live" ? (
              <>
                <option value="category">Event category</option>
                <option value="recency">Latest first</option>
              </>
            ) : (
              <>
                <option value="score">Relevance score</option>
                <option value="recency">Latest first</option>
              </>
            )}
          </select>
        </label>
        <label>
          Window
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>Past 7 days</option>
            <option value={14}>Past 14 days</option>
            <option value={15}>Past 15 days</option>
          </select>
        </label>
        {view === "ranked" && (
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
        )}
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
        {view === "ranked" && (
          <label>
            Direction
            <select value={direction} onChange={(e) => setDirection(e.target.value)}>
              <option value="">All</option>
              <option value="bullish">Bullish</option>
              <option value="bearish">Bearish</option>
              <option value="neutral">Neutral</option>
            </select>
          </label>
        )}
        <label className="filter-company">
          Company
          <CompanySearch value={companyId} onChange={setCompanyId} />
        </label>
      </div>

      {error && <div className="empty">Could not reach the API.<br />{error}</div>}
      {loading && items.length === 0 && <div className="loading">Loading feed…</div>}
      {!loading && !error && items.length === 0 && (
        <div className="empty">
          No announcements in this view.
          <br />
          Load the universe and run the worker to ingest and analyze filings.
        </div>
      )}

      <div className="list">
        {items.map((it) => (
          <div className="card" key={it.id} onClick={() => setSelected(it.id)}>
            <div className="card-top">
              <div
                className="score"
                style={{
                  color:
                    view === "ranked" && it.composite_score != null
                      ? scoreColor(it.composite_score)
                      : "var(--muted)",
                }}
              >
                {view === "ranked" && it.composite_score != null
                  ? it.composite_score.toFixed(0)
                  : it.triage_tier || "—"}
              </div>
              <div className="card-main">
                <div className="card-headline">
                  {it.company?.name ?? it.bse_scrip_code ?? "Unknown"}
                </div>
                <div className="card-meta">
                  {it.company?.nse_symbol && <span className="chip">{it.company.nse_symbol}</span>}
                  <span className="chip">
                    {prettyEventType(it.triage_event_type || it.event_type)}
                  </span>
                  {it.triage_tier && view === "live" && (
                    <span className="chip tier">Tier {it.triage_tier}</span>
                  )}
                  {it.analysis_status === "pending" && (
                    <span className="chip status-pending">Queued</span>
                  )}
                  {it.analysis_status === "processing" && (
                    <span className="chip status-processing">Analyzing…</span>
                  )}
                  {it.analysis_status === "done" && view === "live" && (
                    <span className="chip status-done">Analyzed</span>
                  )}
                  {view === "ranked" && it.direction && (
                    <span className={`dir ${it.direction}`}>{it.direction}</span>
                  )}
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

      <div className="pagination">
        <span>
          Showing {offset + 1}–{offset + items.length} of {total}
        </span>
        <div className="pagination-btns">
          <button className="btn" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>
            Previous
          </button>
          <button
            className="btn"
            disabled={!canLoadMore}
            onClick={() => setOffset(offset + PAGE_SIZE)}
          >
            Load more
          </button>
        </div>
      </div>

      {selected !== null && (
        <DetailDrawer id={selected} onClose={() => setSelected(null)} />
      )}

      {managing && (
        <ManageStocks
          onClose={() => setManaging(false)}
          onChanged={() => {
            load();
          }}
        />
      )}
    </div>
  );
}
