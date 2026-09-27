import { brainDb } from "./db.js";
import { emit } from "./emit.js";
import type { AnomalyDraft } from "./anomaly.js";
import { diagnose } from "./agents/diagnostician.js";
import type { Prisma } from "./generated/index.js";

/**
 * Reflex actions may only touch OPERATIONAL state: connector enable
 * flags, schedules, ops incidents, pages. They must never mutate
 * compliance data (controls, evidence, policies, requirements) — a
 * monitoring system rewriting audit evidence is a compliance product
 * committing suicide. Enforced here by construction: nothing in this
 * file imports a compliance-data model.
 */

export type ConnectorPauseHandler = (integrationType: string) => Promise<void>;

/** The connectors module isn't part of this consolidation yet; wire a real handler in. */
let pauseConnectorHandler: ConnectorPauseHandler = async () => {
  throw new Error("No connector-pause handler registered");
};

export function setConnectorPauseHandler(fn: ConnectorPauseHandler) {
  pauseConnectorHandler = fn;
}

async function reflex(rule: string, trigger: string, action: string, run: () => Promise<void>) {
  try {
    await run();
    await brainDb.reflexLog.create({ data: { rule, trigger, action, result: "EXECUTED" } });
    emit({ type: "brain.reflex.executed", source: "brain", payload: { rule, trigger, action } });
  } catch (err) {
    await brainDb.reflexLog.create({
      data: {
        rule,
        trigger,
        action,
        result: "FAILED",
        detail: { error: err instanceof Error ? err.message : String(err) },
      },
    });
  }
}

async function pauseConnector(integrationType: string, anomalyId: string) {
  await reflex("silence_pause_connector", anomalyId, "pauseConnector", () =>
    pauseConnectorHandler(integrationType),
  );
}

async function page(incidentId: string, title: string) {
  const url = process.env.PAGERDUTY_WEBHOOK;
  if (!url) {
    await brainDb.reflexLog.create({
      data: {
        rule: "critical_page",
        trigger: incidentId,
        action: "page",
        result: "SKIPPED",
        detail: { reason: "PAGERDUTY_WEBHOOK not configured" },
      },
    });
    return;
  }
  await reflex("critical_page", incidentId, "page", async () => {
    const res = await fetch(url, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ incidentId, title }),
    });
    if (!res.ok) throw new Error(`page webhook returned ${res.status}`);
  });
}

async function diagnoseInBackground(incidentId: string, title: string) {
  void diagnose({ question: `Root-cause this incident: ${title}`, timeRangeHours: 6 })
    .then((result) =>
      brainDb.brainIncident.update({
        where: { id: incidentId },
        data: { diagnosis: JSON.stringify(result) },
      }),
    )
    .catch((err) => {
      // diagnosis is best-effort context for the on-call human, not a
      // blocking step in incident creation
      console.error(`diagnostician failed for incident ${incidentId}:`, err);
    });
}

export async function processAnomalies(drafts: AnomalyDraft[]): Promise<void> {
  for (const d of drafts) {
    const a = await brainDb.anomaly.create({
      data: { ...d, details: d.details as Prisma.InputJsonValue },
    });
    emit({
      type: "brain.anomaly.opened",
      source: "brain",
      severityOverride: d.severity,
      payload: { anomalyId: a.id, ...d },
    });

    if (d.detector === "silence" && typeof d.details?.integrationType === "string") {
      await pauseConnector(d.details.integrationType, a.id);
    }

    if (d.severity === "CRITICAL") {
      const title = `${d.subsystem}: ${d.metric} anomaly (${d.value} vs baseline ${d.baseline})`;
      const incident = await brainDb.brainIncident.create({
        data: { title, anomalyIds: [a.id] },
      });
      emit({
        type: "brain.incident.opened",
        source: "brain",
        severityOverride: "CRITICAL",
        payload: { incidentId: incident.id },
      });
      await page(incident.id, title);
      await diagnoseInBackground(incident.id, title);
    }
  }
}
