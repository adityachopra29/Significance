"use client";

import { useEffect, useState } from "react";
import { EventStudy, FeedItemDetail, getDetail } from "@/lib/api";
import { fmtNum, fmtPct, prettyEventType, scoreColor, timeAgo, formatAnnouncedAt } from "@/lib/format";
import {
  COMPOSITE_SCORE,
  DIRECTION,
  EVENT_STUDY_HELP,
  EVENT_TYPE,
  FACTOR_HELP,
  FACTOR_LABELS,
  SENTIMENT,
} from "@/lib/metricsHelp";
import InfoTip from "@/components/InfoTip";

function signedCell(label: string, v?: number | null, helpKey?: string) {
  const cls = v == null ? "" : v >= 0 ? "pos" : "neg";
  const help = helpKey ? EVENT_STUDY_HELP[helpKey] : undefined;
  return (
    <div className="es-cell" key={label}>
      <div className="k">
        {label}
        {help && <InfoTip help={help} />}
      </div>
      <div className={`v ${cls}`}>{fmtPct(v)}</div>
    </div>
  );
}

function metricCell(label: string, value: string, helpKey: string, raw?: number | null) {
  const help = EVENT_STUDY_HELP[helpKey];
  const cls =
    raw == null ? "" : helpKey === "abnormal_volume" ? "" : raw >= 0 ? "pos" : "neg";
  return (
    <div className="es-cell">
      <div className="k">
        {label}
        {help && <InfoTip help={help} />}
      </div>
      <div className={`v ${cls}`}>{value}</div>
    </div>
  );
}

export default function DetailDrawer({
  id,
  onClose,
}: {
  id: number;
  onClose: () => void;
}) {
  const [detail, setDetail] = useState<FeedItemDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    getDetail(id)
      .then((d) => active && setDetail(d))
      .catch((e) => active && setError(String(e)));
    return () => {
      active = false;
    };
  }, [id]);

  const es: EventStudy | null | undefined = detail?.event_study;
  const factorKeys = Object.keys(FACTOR_LABELS) as (keyof typeof FACTOR_LABELS)[];

  return (
    <div className="overlay" onClick={onClose}>
      <div className="drawer" onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose}>
          Close
        </button>

        {error && <div className="empty">{error}</div>}
        {!detail && !error && <div className="loading">Loading…</div>}

        {detail && (
          <>
            <div className="detail-score-row">
              {detail.composite_score != null ? (
                <>
                  <div
                    className="score"
                    style={{ color: scoreColor(detail.composite_score) }}
                  >
                    {detail.composite_score.toFixed(0)}
                  </div>
                  <div>
                    <div className="detail-score-label">
                      Composite score
                      <InfoTip help={COMPOSITE_SCORE} />
                    </div>
                    <h2>{detail.company?.name ?? detail.bse_scrip_code ?? "Unknown"}</h2>
                  </div>
                </>
              ) : (
                <div>
                  <div className="detail-score-label">
                    {detail.triage_tier ? `Tier ${detail.triage_tier}` : "Awaiting analysis"}
                  </div>
                  <h2>{detail.company?.name ?? detail.bse_scrip_code ?? "Unknown"}</h2>
                </div>
              )}
            </div>

            <div className="card-meta">
              {detail.company?.nse_symbol && <span className="chip">{detail.company.nse_symbol}</span>}
              {(detail.triage_event_type || detail.event_type) && (
                <span className="chip chip-with-tip">
                  {prettyEventType(detail.triage_event_type || detail.event_type)}
                  <InfoTip help={EVENT_TYPE} />
                </span>
              )}
              {detail.analysis_status === "pending" && (
                <span className="chip status-pending">Queued for analysis</span>
              )}
              {detail.analysis_status === "processing" && (
                <span className="chip status-processing">Analyzing…</span>
              )}
              {detail.direction && (
                <span className={`dir ${detail.direction} chip-with-tip`}>
                  {detail.direction}
                  <InfoTip help={DIRECTION} />
                </span>
              )}
              {detail.sentiment != null && (
                <span className="chip chip-with-tip">
                  sentiment {detail.sentiment >= 0 ? "+" : ""}
                  {detail.sentiment.toFixed(2)}
                  <InfoTip help={SENTIMENT} />
                </span>
              )}
              <span title={timeAgo(detail.announced_at)}>
                {formatAnnouncedAt(detail.announced_at)}
              </span>
            </div>

            {detail.summary && (
              <div className="section">
                <h3>AI summary</h3>
                <div className="summary">{detail.summary}</div>
              </div>
            )}

            {!detail.summary &&
              detail.headline &&
              detail.analysis_status === "done" && (
                <div className="section">
                  <h3>Headline</h3>
                  <div>{detail.headline}</div>
                  <p className="disclaimer" style={{ marginTop: 12 }}>
                    Analysis data is missing for this filing (likely from an incomplete database
                    migration). It will not be re-analyzed unless re-queued.
                  </p>
                </div>
              )}

            {!detail.summary &&
              detail.headline &&
              detail.analysis_status !== "done" && (
              <div className="section">
                <h3>Headline</h3>
                <div>{detail.headline}</div>
                <p className="disclaimer" style={{ marginTop: 12 }}>
                  Full LLM analysis is pending. Check back shortly or keep this drawer open.
                </p>
              </div>
            )}

            {detail.factors && detail.composite_score != null && (
            <div className="section">
              <h3>
                Score breakdown
                <InfoTip help={COMPOSITE_SCORE} />
              </h3>
              {factorKeys.map((key) => {
                const v = detail.factors?.[key] ?? 0;
                const label = FACTOR_LABELS[key];
                const help = FACTOR_HELP[key];
                return (
                  <div className="factor" key={key}>
                    <div className="factor-row">
                      <span className="label-with-tip">
                        {label}
                        <InfoTip help={help} />
                      </span>
                      <span>{fmtNum(v, 2)}</span>
                    </div>
                    <div className="bar">
                      <span style={{ width: `${Math.min(100, (v ?? 0) * 100)}%` }} />
                    </div>
                  </div>
                );
              })}
            </div>
            )}

            {detail.headline && (detail.summary || detail.analysis_status === "done") && (
            <div className="section">
              <h3>Headline</h3>
              <div>{detail.headline}</div>
            </div>
            )}

            {es && (es.car_t1 != null || es.ar_day0 != null) && (
              <div className="section">
                <h3>
                  Event study (price reaction)
                  <InfoTip
                    help={{
                      description:
                        "Statistical estimate of how the stock price moved around the announcement, vs a Nifty 50 market model fitted on pre-event data.",
                      scale:
                        "Returns in %. CAR = cumulative abnormal return over the window after the event day.",
                    }}
                  />
                </h3>
                <div className="es-grid">
                  {signedCell("Abnormal return (day 0)", es.ar_day0, "ar_day0")}
                  {signedCell("CAR (T+1)", es.car_t1, "car_t1")}
                  {signedCell("CAR (T+5)", es.car_t5, "car_t5")}
                  {signedCell("CAR (T+20)", es.car_t20, "car_t20")}
                  {metricCell("Beta", fmtNum(es.beta), "beta", es.beta)}
                  {metricCell(
                    "Abnormal volume",
                    es.abnormal_volume ? `${fmtNum(es.abnormal_volume)}×` : "-",
                    "abnormal_volume",
                    es.abnormal_volume,
                  )}
                </div>
              </div>
            )}

            {(detail.company?.chart_url || detail.attachment_url) && (
              <div className="section">
                <div className="link-row">
                  {detail.company?.chart_url && (
                    <a href={detail.company.chart_url} target="_blank" rel="noreferrer">
                      View stock chart →
                    </a>
                  )}
                  {detail.attachment_url && (
                    <a href={detail.attachment_url} target="_blank" rel="noreferrer">
                      View original filing →
                    </a>
                  )}
                </div>
              </div>
            )}

            <div className="section">
              <span className="disclaimer">
                Analysis by {detail.model_provider ?? "engine"} · For research only, not investment advice.
              </span>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
