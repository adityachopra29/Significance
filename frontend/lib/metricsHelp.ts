import { Factors } from "./api";

export interface MetricHelp {
  description: string;
  scale: string;
}

export const COMPOSITE_SCORE: MetricHelp = {
  description:
    "Overall trading relevance rank for this announcement. Combines event importance, materiality, surprise, sentiment, price under-reaction, liquidity, model confidence, and recency.",
  scale: "0–100. Higher = more worth attention. 60+ strong signal, 40–59 moderate, 20–39 weak, below 20 low priority.",
};

export const DIRECTION: MetricHelp = {
  description:
    "Expected price impact direction inferred from the filing language (orders, results tone, regulatory action, etc.).",
  scale: "bullish · bearish · neutral — qualitative label, not a probability.",
};

export const EVENT_TYPE: MetricHelp = {
  description:
    "Canonical category of the corporate action (e.g. order win, results, buyback, legal). Categories with historically larger price moves receive higher base weight.",
  scale: "Named category; weight inside score is 0–1 (see Event type weight below).",
};

export const SENTIMENT: MetricHelp = {
  description:
    "Signed tone of the announcement text as assessed by the model — positive vs negative wording about business outcomes.",
  scale: "−1 (very bearish) to +1 (very bullish). Score breakdown uses absolute magnitude (0–1).",
};

export const FACTOR_HELP: Record<keyof Factors, MetricHelp> = {
  event_type: {
    description:
      "Base importance of this announcement category based on historical market impact (M&A, order wins, results, etc.).",
    scale: "0–1 bar. 1.0 = highest-impact categories (e.g. acquisition, order win); ~0.3 = routine disclosures.",
  },
  materiality: {
    description:
      "How large the event is relative to the company — combines extracted amounts (₹ crore) vs market cap with the model's materiality assessment.",
    scale: "0–1. 1.0 ≈ event worth ≥10% of market cap or clearly company-moving; 0 = immaterial.",
  },
  surprise: {
    description:
      "How unexpected or novel the news is versus what the market likely already knew (guidance pre-bakes, repeated notices score lower).",
    scale: "0–1. 1.0 = highly surprising; 0 = fully anticipated / routine.",
  },
  sentiment: {
    description:
      "Strength of directional language in the filing, regardless of bull/bear sign. Fed into the score as |sentiment|.",
    scale: "0–1. 1.0 = strongly worded; 0 = flat / factual tone.",
  },
  price_reaction: {
    description:
      "Under-reaction signal from the event study. If the stock has not yet moved much on announcement day, more potential opportunity may remain.",
    scale: "0–1. 1.0 = little abnormal move yet (<6% day-0 AR); 0 = market already reacted sharply.",
  },
  liquidity: {
    description:
      "Tradability proxy. Uses ADV (average daily traded value in ₹ crore) when available, with market cap as a fallback.",
    scale: "0–1. 1.0 = very liquid (ADV ≥ ₹500 cr/day); ~0.55 = thin but tradable; ~0.35 = very illiquid.",
  },
  confidence: {
    description:
      "How confident the model is in its classification and summary, plus a small boost when the company is matched in our universe.",
    scale: "0–1. 1.0 = high confidence parse; lower = ambiguous filing or partial text.",
  },
  time_decay: {
    description:
      "Recency multiplier — fresh announcements rank higher; stale ones fade so the feed stays actionable.",
    scale: "0–1. 1.0 = just published; decays with ~48h half-life (floor 0.2).",
  },
};

export const FACTOR_LABELS: Record<keyof Factors, string> = {
  event_type: "Event type weight",
  materiality: "Materiality (vs size)",
  surprise: "Surprise / novelty",
  sentiment: "Sentiment magnitude",
  price_reaction: "Price reaction (under-reaction)",
  liquidity: "Liquidity / tradability",
  confidence: "Confidence",
  time_decay: "Recency",
};

export const EVENT_STUDY_HELP: Record<string, MetricHelp> = {
  ar_day0: {
    description:
      "Abnormal return on the first trading day on/after the announcement. Compares the stock's return to what the market model (vs Nifty 50) predicted.",
    scale: "Percent (%). Positive = outperformed market; negative = underperformed. ±6% is treated as a full day-0 reaction.",
  },
  car_t1: {
    description:
      "Cumulative abnormal return from event day through the next trading day — captures immediate post-announcement drift.",
    scale: "Percent (%). Sum of daily abnormal returns over T+1 window.",
  },
  car_t5: {
    description:
      "Cumulative abnormal return over ~5 trading days after the event — short-term post-announcement drift (PEAD-style horizon).",
    scale: "Percent (%). Positive = sustained outperformance vs market model.",
  },
  car_t20: {
    description:
      "Cumulative abnormal return over ~20 trading days — medium-term drift after the filing.",
    scale: "Percent (%). Useful for slower information diffusion / under-reaction patterns.",
  },
  beta: {
    description:
      "Market sensitivity estimated from pre-event data: how much the stock typically moves per unit Nifty 50 move (OLS market model).",
    scale: "Unitless ratio. β≈1 moves with the market; β>1 more volatile; β<1 defensive.",
  },
  abnormal_volume: {
    description:
      "Trading volume on event day relative to the stock's recent average — confirms whether the market engaged with the news.",
    scale: "Multiple of normal (e.g. 2.0× = double typical volume). >1.5× adds a small boost to the under-reaction factor.",
  },
};
