# Architecture

## What's real in this repo today

```
apps/api        Fastify — /healthz, /v1/brain/* (REST), /v1/brain/stream
                 (SSE) (org-scoping is a placeholder header read; real
                 API-key/session auth is not built)
packages/brain   The Brain: event bus, event lake, correlation, health
                 model, anomaly detectors, reflexes, diagnostician agent,
                 live SSE fan-out (live.ts, cross-process via Redis Pub/Sub)
packages/agents  runAgent(): the shared LLM-call runtime (prompt
                 resolution, JSON parse + repair retry, GenerationJob
                 cost/latency logging). One agent registered: ops_diagnostician
packages/db      Prisma client + RLS-transaction helper; schema covers
                 Organization/ApiKey, Integration (KMS-sealed connector
                 credentials), PromptTemplate/GenerationJob (agent runtime)
packages/core    Real AWS KMS envelope encryption (storeCredentials /
                 decryptCredentials) — see packages/core/src/kms.ts
packages/skills  Skill registry interface (no skills registered yet —
                 connectors would register here and call
                 getOrgCredentials(), which now actually decrypts)
packages/ethics-crypto  computeShredDate() — crypto-shred timing logic
                 for the (not-yet-built) isolated ethics service
```

Everything else in the target architecture below — `apps/web`,
`apps/workers` (Temporal), the isolated `apps/ethics` stack, the control
framework / evidence / policy / questionnaire / vendor-risk / incident
data model, connectors, the MCP tool catalog — is design, not code. Build
it as its own module and instrument it against `packages/brain`'s
`emit()` (see "Instrumenting a new module" below); don't assume it exists
by importing from a path that doesn't.

## The Brain

```
                        ┌─────────────────────────────────────────────────────┐
                        │                    THE BRAIN                         │
                        │              packages/brain                          │
                        │                                                     │
  every module  ───────▶│  NERVES (emit)      every call site emits typed      │
  that calls emit()     │      │              events w/ traceId + flowId       │
                         │      ▼                                              │
                        │  SPINAL CORD (Redis Streams, at-least-once)          │
                        │      │                                              │
                        │      ▼                                              │
                        │  MEMORY — Event Lake (Postgres, brain schema,        │
                        │      │              append-only)                    │
                        │      ▼                                              │
                        │  CORTEX — flow correlation (correlate.ts)           │
                        │         health model (health.ts)                   │
                        │         anomaly detectors (anomaly.ts)              │
                        │      │                                              │
                        │      ├──▶ REFLEXES (reflexes.ts) — operational      │
                        │      │       state only: pause connector, page,     │
                        │      │       open ops incident. NEVER touches       │
                        │      │       compliance data.                      │
                        │      ▼                                              │
                        │  diagnostician agent (agents/diagnostician.ts) —    │
                        │      │              RAG root-cause over the lake    │
                        │      ▼                                              │
                        │  apps/api routes/brain.ts — /v1/brain/*             │
                        │  worker.ts — /healthz + /status.json (deadman)      │
                        └─────────────────────────────────────────────────────┘
```

Three rules that make this a nervous system rather than a log pile:

1. **The Brain runs outside any workflow engine.** `worker.ts` is a plain
   Node process. If a future Temporal deployment dies, the Brain's
   silence detector is what notices Temporal is gone — it can't be, if
   it depends on Temporal to run.
2. **Reflexes only touch operational state.** `reflexes.ts` imports
   nothing from a compliance-data model, by construction — connector
   pause flags, ops incidents, and pages, never controls/evidence/policy.
3. **Ethics stays metadata-only.** `ethics.heartbeat` / `ethics.sla.fired`
   are the only events the (future) ethics service emits into the lake;
   case content never crosses into `BrainEvent.payload`.

## Instrumenting a new module

Call `emit()` from `@compos/brain` at the start/end of any unit of work
you want the Brain to see:

```ts
import { emit } from "@compos/brain";

emit({ type: "flow.started", source: "check-runner", flowId, payload: { flowType: "CHECK_RUN" } });
// ...do the work...
emit({ type: "flow.completed", source: "check-runner", flowId });
```

`emit()` is fire-and-forget and never throws into the caller — a bus
outage degrades the Brain's visibility, not the flow being observed.

## Design gaps carried over from the original draft (fixed here, noted for the record)

- `prisma/schema.prisma` needed `previewFeatures = ["multiSchema"]` and
  `@@schema("brain")` on every model/enum, or `prisma generate` fails.
- `bus.ts`'s `xautoclaim` call was missing the literal `"COUNT"` keyword
  ioredis requires, and read `.messages` off a reply that's actually a
  positional tuple `[cursor, entries, deletedIds]`.
- `worker.ts` referenced an `ingestState` object and a `publicStatus()`
  function that were never defined — both are real now (see `queries.ts`).
- `correlate.ts` called `emit()` without importing it, and derived
  `flowType` from a truthiness check on the raw payload string instead of
  parsing it.
- `anomaly.ts`/`reflexes.ts` called several functions (`dedupeAgainstOpen`,
  `reflex`, `pauseConnector`, `page`, `diagnoseInBackground`) that were
  referenced but never implemented in the draft. `costDetector` and
  `freshnessDetector` still return `[]` — they depend on cost-tracking
  and evidence-expiry tables that don't exist in this repo yet; that's
  now an explicit, commented gap instead of a call to an undefined function.
- The MCP scope table in the draft marked `list_anomalies` as "own org"
  visible, but `Anomaly`/`BrainIncident` carry no `orgId` — they describe
  platform subsystems, not tenant activity. Treated as platform-team-only
  here; revisit if anomalies ever gain per-org attribution.
- `deploy.sh` ran `psql` inside the Node/api container, which has no
  `psql` client installed — schema setup now runs against the `postgres`
  service container instead.

A second design doc ("final wiring") assumed ~150 files from prior rounds
— the full schema, `packages/questionnaire`, `packages/review`, `apps/web`,
`apps/workers`, the Check DSL, a User/session domain — already existed in
this repo. They don't; only what's in this file and the manifest above is
real. `docs/merge-ledger.md` #13–19 covers what was built for real from
that round (KMS, the Brain's SSE stream, `packages/agents`) versus what
was skipped and why (instrumented check runner, Temporal worker
bootstrap, session auth, screen-backend handlers).
