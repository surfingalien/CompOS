import { randomUUID } from "node:crypto";
import { brainDb } from "./db.js";
import { emit } from "./emit.js";
import type { RawEvent } from "./bus.js";

const EXPECTED_DURATION_MS: Record<string, number> = {
  CHECK_RUN: 10 * 60_000,
  EVIDENCE_SYNC: 30 * 60_000,
  POLICY_GEN: 5 * 60_000,
  QUESTIONNAIRE: 60 * 60_000,
  DSAR: 30 * 86_400_000,
  REG_SCAN: 60 * 60_000,
  WEBHOOK_DELIVERY: 60_000,
  SLA_TIMER: 60 * 60_000,
};
const DEFAULT_BUDGET_MS = 15 * 60_000;

function parsePayload(fields: RawEvent["fields"]): Record<string, unknown> {
  if (!fields.payload) return {};
  try {
    return JSON.parse(fields.payload);
  } catch {
    return {};
  }
}

export async function correlateEvent(e: RawEvent) {
  if (!e.fields.flowId) return;
  const { type, flowId } = e.fields;

  if (type === "flow.started") {
    const payload = parsePayload(e.fields);
    await brainDb.flowRun
      .create({
        data: {
          id: randomUUID(),
          flowId,
          flowType: typeof payload.flowType === "string" ? payload.flowType : "UNKNOWN",
          traceId: e.fields.traceId || null,
          orgId: e.fields.orgId || null,
          status: "RUNNING",
        },
      })
      // flow.started is not guaranteed to be seen exactly once (at-least-once
      // delivery, or a redelivered batch after a crash mid-ack) — a duplicate
      // flowId is expected, not an error.
      .catch(() => {});
  }

  const run = await brainDb.flowRun.findUnique({ where: { flowId } });
  if (!run) return;

  const ended = type === "flow.completed" ? "SUCCEEDED" : type === "flow.failed" ? "FAILED" : null;
  await brainDb.flowRun.update({
    where: { id: run.id },
    data: {
      eventCount: { increment: 1 },
      lastEventAt: new Date(),
      ...(ended
        ? { status: ended, endedAt: new Date(), durationMs: Date.now() - run.startedAt.getTime() }
        : {}),
    },
  });
}

export async function reapStuckFlows() {
  const running = await brainDb.flowRun.findMany({ where: { status: "RUNNING" } });
  for (const r of running) {
    const budget = EXPECTED_DURATION_MS[r.flowType] ?? DEFAULT_BUDGET_MS;
    const overBudgetMs = Date.now() - r.startedAt.getTime() - budget;
    if (overBudgetMs > 0) {
      await brainDb.flowRun.update({ where: { id: r.id }, data: { status: "STUCK" } });
      emit({
        type: "flow.stuck",
        source: "brain",
        orgId: r.orgId ?? undefined,
        flowId: r.flowId,
        payload: { flowType: r.flowType, overBudgetMs },
      });
    }
  }
}
