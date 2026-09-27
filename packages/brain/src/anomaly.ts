import { brainDb } from "./db.js";

export interface AnomalyDraft {
  detector: string;
  subsystem: string;
  metric: string;
  value: number;
  baseline: number;
  zScore?: number;
  severity: "WARN" | "CRITICAL";
  details?: Record<string, unknown>;
}

/** Five explainable detectors — deliberately no black-box ML. */
export async function runDetectors(): Promise<AnomalyDraft[]> {
  const out: AnomalyDraft[] = [
    ...(await silenceDetector()),
    ...(await errorRateDetector()),
    ...(await stuckFlowDetector()),
    ...(await costDetector()),
    ...(await freshnessDetector()),
  ];
  return dedupeAgainstOpen(out);
}

/** Don't re-open an anomaly for a subsystem+metric pair that's already OPEN. */
async function dedupeAgainstOpen(drafts: AnomalyDraft[]): Promise<AnomalyDraft[]> {
  if (!drafts.length) return drafts;
  const open = await brainDb.anomaly.findMany({
    where: { status: "OPEN" },
    select: { subsystem: true, metric: true },
  });
  const openKeys = new Set(open.map((o) => `${o.subsystem}::${o.metric}`));
  return drafts.filter((d) => !openKeys.has(`${d.subsystem}::${d.metric}`));
}

function round(n: number, dp: number): number {
  const f = 10 ** dp;
  return Math.round(n * f) / f;
}

/**
 * Per-source expected cadence (hours between events, 7d P50); alarm when
 * the current gap exceeds 3x that cadence — catches dead connectors and
 * crashed workers by absence rather than by error volume.
 */
async function silenceDetector(): Promise<AnomalyDraft[]> {
  const sources = await brainDb.$queryRaw<Array<{ source: string; last: Date; p50h: number }>>`
    WITH gaps AS (
      SELECT source, at,
             EXTRACT(EPOCH FROM (at - lag(at) OVER (PARTITION BY source ORDER BY at))) / 3600 AS gap_h
      FROM brain."BrainEvent" WHERE at > now() - interval '7 days')
    SELECT source, max(at) AS last, percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_h) AS p50h
    FROM gaps GROUP BY source HAVING percentile_cont(0.5) WITHIN GROUP (ORDER BY gap_h) > 0`;

  return sources
    .filter((s) => (Date.now() - s.last.getTime()) / 3_600_000 > s.p50h * 3)
    .map((s) => ({
      detector: "silence",
      subsystem: s.source,
      metric: "hours_since_last_event",
      value: round((Date.now() - s.last.getTime()) / 3_600_000, 1),
      baseline: round(s.p50h, 1),
      severity: "CRITICAL" as const,
      details: { lastAt: s.last },
    }));
}

/** Error share per source over the last hour vs. its 7d same-source baseline, as a z-score. */
async function errorRateDetector(): Promise<AnomalyDraft[]> {
  const rows = await brainDb.$queryRaw<
    Array<{ source: string; recent_rate: number; baseline_mean: number; baseline_stddev: number }>
  >`
    WITH hourly AS (
      SELECT source, date_trunc('hour', at) AS bucket,
        COUNT(*) FILTER (WHERE severity IN ('ERROR','CRITICAL'))::float / GREATEST(COUNT(*), 1) AS err_rate
      FROM brain."BrainEvent" WHERE at > now() - interval '7 days'
      GROUP BY source, bucket),
    recent AS (
      SELECT source, err_rate AS recent_rate FROM hourly WHERE bucket = date_trunc('hour', now())),
    baseline AS (
      SELECT source, avg(err_rate) AS baseline_mean, stddev_samp(err_rate) AS baseline_stddev
      FROM hourly WHERE bucket < date_trunc('hour', now()) GROUP BY source)
    SELECT recent.source, recent.recent_rate, baseline.baseline_mean,
           COALESCE(baseline.baseline_stddev, 0) AS baseline_stddev
    FROM recent JOIN baseline ON baseline.source = recent.source`;

  const out: AnomalyDraft[] = [];
  for (const r of rows) {
    const stddev = r.baseline_stddev || 0.01; // avoid div-by-zero on a flat-line baseline
    const z = (r.recent_rate - r.baseline_mean) / stddev;
    if (z > 2) {
      out.push({
        detector: "rate",
        subsystem: r.source,
        metric: "error_rate",
        value: round(r.recent_rate, 3),
        baseline: round(r.baseline_mean, 3),
        zScore: round(z, 2),
        severity: z > 4 ? "CRITICAL" : "WARN",
      });
    }
  }
  return out;
}

/** Count of flows the reaper marked STUCK, vs. a fixed low-tolerance threshold. */
async function stuckFlowDetector(): Promise<AnomalyDraft[]> {
  const stuck = await brainDb.flowRun.count({ where: { status: "STUCK" } });
  if (stuck === 0) return [];
  return [
    {
      detector: "stuck_flow",
      subsystem: "temporal",
      metric: "stuck_flow_count",
      value: stuck,
      baseline: 0,
      severity: stuck >= 5 ? "CRITICAL" : "WARN",
    },
  ];
}

/**
 * LLM $/hour vs. 7d same-hour baseline. Not yet implemented: there is no
 * cost/usage table in this repo yet (module 5's GenerationJob cost field
 * hasn't landed). Returns [] rather than querying a table that doesn't
 * exist — wire this up once generation cost tracking ships.
 */
async function costDetector(): Promise<AnomalyDraft[]> {
  return [];
}

/**
 * Evidence expected to expire soon without a renewal in flight. Not yet
 * implemented: depends on the evidence/control schema (module 1-10),
 * which isn't part of this consolidation. Returns [] for the same reason
 * as costDetector above.
 */
async function freshnessDetector(): Promise<AnomalyDraft[]> {
  return [];
}
