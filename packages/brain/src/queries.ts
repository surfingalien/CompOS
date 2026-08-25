import { brainDb } from "./db.js";

export { latestHealth, healthHistory } from "./health.js";

/**
 * Design note: Anomaly and BrainIncident carry no `orgId` — they describe
 * platform subsystems ("connectors", "check_runner"), not per-org
 * activity, so there is no safe way to scope them to a single tenant.
 * The original draft's MCP scope table marked `list_anomalies` as
 * "own org" visible, which the schema can't actually support; treat
 * both as platform-team-only until/unless anomalies gain per-org
 * attribution (e.g. when a connector anomaly can be traced to one
 * org's integration instance).
 */
export async function listAnomalies(status?: string) {
  return brainDb.anomaly.findMany({
    where: status ? { status } : undefined,
    orderBy: { at: "desc" },
    take: 100,
  });
}

export async function listIncidents() {
  return brainDb.brainIncident.findMany({ orderBy: { startedAt: "desc" }, take: 100 });
}

/** Org-scoped: returns null (not the flow) if it belongs to a different org. */
export async function flowTimeline(flowId: string, orgId?: string) {
  const flow = await brainDb.flowRun.findUnique({ where: { flowId } });
  if (!flow) return null;
  if (orgId && flow.orgId && flow.orgId !== orgId) return null;

  const events = await brainDb.brainEvent.findMany({
    where: { flowId },
    orderBy: { at: "asc" },
  });
  return { flow, events };
}

export async function publicStatus() {
  const health = await brainDb.healthSnapshot.findFirst({ orderBy: { at: "desc" } });
  const openIncidents = await brainDb.brainIncident.count({ where: { status: { not: "RESOLVED" } } });
  return {
    composite: health?.composite ?? null,
    subsystems: health?.subsystems ?? null,
    openIncidents,
    at: health?.at ?? null,
  };
}
