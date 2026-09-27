import { brainDb } from "./db.js";

const SUBSYSTEMS = [
  "api",
  "mcp",
  "connectors",
  "check_runner",
  "llm",
  "temporal",
  "sla_timers",
  "review_queue",
  "ethics_service",
] as const;
type Subsystem = (typeof SUBSYSTEMS)[number];

// subsystem -> BrainEvent.source. Keep in sync with BrainSource in emit.ts.
const SOURCE_BY_SUBSYSTEM: Record<Subsystem, string> = {
  api: "api",
  mcp: "mcp",
  connectors: "connector",
  check_runner: "check-runner",
  llm: "agent",
  temporal: "workers",
  sla_timers: "sla",
  review_queue: "review",
  ethics_service: "ethics",
};
const SUBSYSTEM_BY_SOURCE: Record<string, Subsystem> = Object.fromEntries(
  Object.entries(SOURCE_BY_SUBSYSTEM).map(([sub, src]) => [src, sub as Subsystem]),
);

function avg(values: number[]): number {
  if (!values.length) return 1;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

export interface HealthResult {
  composite: number;
  subsystems: Record<Subsystem, number>;
  openAnomalies: number;
}

export async function computeAndStoreHealth(): Promise<HealthResult> {
  const subsystems = Object.fromEntries(SUBSYSTEMS.map((s) => [s, 1])) as Record<
    Subsystem,
    number
  >;

  // error-rate scoring over the last hour, per source
  const rates = await brainDb.$queryRaw<Array<{ source: string; errs: bigint; total: bigint }>>`
    SELECT source,
      COUNT(*) FILTER (WHERE severity IN ('ERROR','CRITICAL')) AS errs,
      COUNT(*) AS total
    FROM brain."BrainEvent" WHERE at > now() - interval '1 hour'
    GROUP BY source`;

  for (const r of rates) {
    const key = SUBSYSTEM_BY_SOURCE[r.source];
    if (!key) continue;
    const errRate = Number(r.errs) / Math.max(Number(r.total), 1);
    subsystems[key] = Math.max(0, 1 - errRate * 5); // 20% errors → 0
  }

  // silence scoring: a source with ZERO events in 2h (when it should be
  // active) scores low — dead connectors and crashed workers are found by
  // absence, not just by errors.
  for (const s of SUBSYSTEMS) {
    const last = await brainDb.brainEvent.findFirst({
      where: { source: SOURCE_BY_SUBSYSTEM[s] },
      orderBy: { at: "desc" },
      select: { at: true },
    });
    if (!last || Date.now() - last.at.getTime() > 2 * 3_600_000) {
      subsystems[s] = Math.min(subsystems[s], 0.3);
    }
  }

  const openAnomalies = await brainDb.anomaly.count({ where: { status: "OPEN" } });
  const values = Object.values(subsystems);
  const composite =
    Math.round((Math.min(...values) * 0.6 + avg(values) * 0.4) * 100) / 100; // weakest-leg weighted

  await brainDb.healthSnapshot.create({ data: { composite, subsystems, openAnomalies } });
  return { composite, subsystems, openAnomalies };
}

export async function latestHealth(): Promise<HealthResult | null> {
  const snap = await brainDb.healthSnapshot.findFirst({ orderBy: { at: "desc" } });
  if (!snap) return null;
  return {
    composite: snap.composite,
    subsystems: snap.subsystems as Record<Subsystem, number>,
    openAnomalies: snap.openAnomalies,
  };
}

export async function healthHistory(limit: number) {
  return brainDb.healthSnapshot.findMany({ orderBy: { at: "desc" }, take: limit });
}
