import Fastify from "fastify";
import { brainDb } from "./db.js";
import { consume, ack, type RawEvent } from "./bus.js";
import { correlateEvent, reapStuckFlows } from "./correlate.js";
import { computeAndStoreHealth } from "./health.js";
import { runDetectors } from "./anomaly.js";
import { processAnomalies } from "./reflexes.js";
import { fanOutWebhooks } from "./webhooks.js";
import { publicStatus } from "./queries.js";

const CONSUMER = `brain-${process.pid}`;

// The original draft referenced `ingestState.lastAt`/`.lag` from /healthz
// without ever defining it — fixed here as real, mutated state so the
// deadman monitor gets a truthful signal instead of a ReferenceError.
const ingestState = { lastAt: null as Date | null, lag: 0 };

async function ingestLoop() {
  for await (const batch of consume(CONSUMER)) {
    if (!batch.length) continue;

    await brainDb.brainEvent
      .createMany({
        data: batch.map((e) => ({
          id: e.id,
          type: e.fields.type,
          source: e.fields.source,
          orgId: e.fields.orgId || null,
          severity: e.fields.severity as "INFO" | "WARN" | "ERROR" | "CRITICAL",
          traceId: e.fields.traceId || null,
          flowId: e.fields.flowId || null,
          payload: JSON.parse(e.fields.payload || "{}"),
        })),
        skipDuplicates: true, // redelivery under at-least-once must not error
      })
      .catch((err) => console.error("brain: failed to persist event batch", err));

    for (const e of batch) {
      await correlateEvent(e).catch((err) => console.error("brain: correlate failed", err));
      await fanOutWebhooks(e).catch((err) => console.error("brain: webhook fan-out failed", err));
    }

    await ack(batch.map((e: RawEvent) => e.streamId));
    ingestState.lastAt = new Date();
    ingestState.lag = 0;
  }
}

async function periodicLoop() {
  for (;;) {
    const t0 = Date.now();
    try {
      await reapStuckFlows();
      await computeAndStoreHealth();
      const anomalies = await runDetectors();
      await processAnomalies(anomalies);
    } catch (err) {
      console.error("brain: periodic loop iteration failed", err);
    }
    const elapsed = Date.now() - t0;
    await new Promise((r) => setTimeout(r, Math.max(60_000 - elapsed, 5000)));
  }
}

// Deadman heartbeat — an EXTERNAL uptime monitor is expected to watch this;
// if the brain itself dies, the outside world notices. The brain runs
// outside Temporal by design: the observer must not depend on the observed.
const hb = Fastify({ logger: false });
hb.get("/healthz", async () => ({
  ok: true,
  pid: process.pid,
  lastIngestAt: ingestState.lastAt,
  lagEvents: ingestState.lag,
}));
hb.get("/status.json", async () => publicStatus());

async function main() {
  await Promise.all([ingestLoop(), periodicLoop()]);
}

main().catch((err) => {
  console.error("brain worker crashed", err);
  process.exit(1);
});

hb.listen({ port: Number(process.env.BRAIN_HTTP_PORT ?? 3010), host: "0.0.0.0" }).catch((err) => {
  console.error("brain heartbeat server failed to start", err);
  process.exit(1);
});
